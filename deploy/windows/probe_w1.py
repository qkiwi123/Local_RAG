"""Offline, isolated W1 inference/dependency probe; never imports backend/.env."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback


def prepare(root: Path) -> None:
    sys.dont_write_bytecode = True
    for name in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME",
                 "XDG_CACHE_HOME", "NUMBA_CACHE_DIR", "MPLCONFIGDIR"):
        path = root / "cache" / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(root / "temp")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE",
                 "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "TOKENIZERS_PARALLELISM"):
        os.environ[name] = "true" if name != "TOKENIZERS_PARALLELISM" else "false"
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "VISION_API_KEY"):
        os.environ.pop(name, None)
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.chdir(root / "temp")  # Not the repository or application root.


def verify_payload(root: Path) -> dict:
    manifest = json.loads((root / "w1-manifest.json").read_text(encoding="utf-8"))
    def check(entry):
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != entry["size"]:
            raise ValueError(f"Missing/escaping/wrong-size payload: {entry['path']}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"Payload hash mismatch: {entry['path']}")
    # Tens of thousands of small library/header files; bounded parallel reads
    # avoid serial filesystem latency. Inference itself remains sequential.
    with ThreadPoolExecutor(max_workers=8) as executor:
        for _ in executor.map(check, manifest["files"]):
            pass
    return {"files": len(manifest["files"]), "bytes": manifest["bytes"], "sha256_verified": True}


def audit_guard(root: Path, events: list) -> None:
    def inside(value):
        if isinstance(value, (str, bytes, os.PathLike)):
            # dill probes file-handle types with NUL (/dev/null on POSIX).
            # This discards bytes, not an application file outside the bundle.
            if os.fsdecode(value).casefold() == os.devnull.casefold():
                return True
            return Path(os.fsdecode(value)).resolve().is_relative_to(root)
        return True  # File descriptors do not identify filesystem destinations.

    def guard(event, args):
        if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo"}:
            events.append({"event": event, "kind": "network"})
            raise PermissionError("W1 is offline; network operations are prohibited")
        writes = []
        if event == "open":
            path, mode, flags = args
            if (mode and any(char in mode for char in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                writes = [path]
        elif event in {"os.mkdir", "os.remove", "os.rmdir"}:
            writes = [args[0]]
        elif event in {"os.rename", "os.link", "os.symlink"}:
            writes = list(args[:2])
        if any(not inside(path) for path in writes):
            events.append({"event": event, "kind": "outside_write"})
            raise PermissionError("W1 write escaped the bundle root")
    sys.addaudithook(guard)


def assert_module_paths(root: Path) -> int:
    for path in sys.path:
        if not Path(path).resolve().is_relative_to(root):
            raise ValueError(f"Global import path leaked: {path}")
    count = 0
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and not filename.startswith("<"):
            if not Path(filename).resolve().is_relative_to(root):
                raise ValueError(f"Global module leaked: {filename}")
            count += 1
    return count


def driver_probe() -> dict:
    """Actual driver API presence, not CUDA_VISIBLE_DEVICES or a claimed flag."""
    try:
        driver = ctypes.WinDLL("nvcuda.dll")
        result = {"nvcuda_loadable": True, "cuInit": int(driver.cuInit(0))}
        count = ctypes.c_int()
        result["cuDeviceGetCount"] = int(driver.cuDeviceGetCount(ctypes.byref(count)))
        result["device_count"] = count.value
        return result
    except OSError:
        return {"nvcuda_loadable": False, "device_count": 0}


def loaded_module_paths() -> list[Path]:
    """Query loader handles, not memory-map names cached before a directory move."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.K32EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    kernel.K32EnumProcessModules.restype = ctypes.c_int
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.GetModuleFileNameW.restype = ctypes.c_ulong
    capacity = 1024
    while True:
        handles = (ctypes.c_void_p * capacity)()
        needed = ctypes.c_ulong()
        if not kernel.K32EnumProcessModules(kernel.GetCurrentProcess(), handles,
                                            ctypes.sizeof(handles), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value <= ctypes.sizeof(handles):
            break
        capacity = needed.value // ctypes.sizeof(ctypes.c_void_p) + 64
    paths = []
    for handle in handles[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
        if not length or length >= len(buffer):
            raise OSError("Cannot obtain a complete loaded-module path")
        paths.append(Path(buffer.value).resolve())
    return paths


def native_runtime_paths(root: Path) -> dict:
    libraries = {}
    for path in loaded_module_paths():
        if path.name.lower().startswith(("msvcp140", "vcruntime140", "concrt140")):
            if not path.is_file() or not path.is_relative_to(root):
                raise ValueError(f"MSVC runtime was borrowed from outside the bundle: {path.name}")
            libraries[path.name] = path.relative_to(root).as_posix()
    if not libraries:
        raise ValueError("No loaded MSVC runtime was found")
    return libraries


def run_models(root: Path, device: str) -> dict:
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    from FlagEmbedding import FlagReranker

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("GPU requested but CUDA unavailable; no silent fallback")
    query = "知识库怎样备份才完整？"
    documents = [
        "知识库备份前先停止服务，然后复制完整数据目录，包含原文、SQLite、向量索引和OCR图片。",
        "苹果树应在春季修剪，浇水频率取决于土壤和天气。",
        "API Key可以在设置页面填写，保存后启用生成模型。",
    ]
    started = time.perf_counter()
    embedding = SentenceTransformer(str(root / "models" / "embedding"), local_files_only=True, device=device)
    embed_load = time.perf_counter() - started
    started = time.perf_counter()
    vectors = embedding.encode([query, *documents], normalize_embeddings=True, show_progress_bar=False, batch_size=32)
    embed_cold = time.perf_counter() - started
    started = time.perf_counter()
    warm_vectors = embedding.encode([query, *documents], normalize_embeddings=True, show_progress_bar=False, batch_size=32)
    embed_warm = time.perf_counter() - started
    params = list(embedding.parameters())
    if any(str(parameter.device) != device for parameter in params):
        raise ValueError("Embedding parameters are on the wrong device")
    if vectors.shape != (4, 1024) or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
        raise ValueError("Invalid embedding output")
    similarity = vectors[1:] @ vectors[0]
    if int(np.argmax(similarity)) != 0 or not np.allclose(vectors, warm_vectors, atol=1e-4):
        raise ValueError("Embedding semantic/repeatability smoke check failed")
    embedding_dtype = str(params[0].dtype)
    del params
    started = time.perf_counter()
    reranker = FlagReranker(str(root / "models" / "reranker"), devices=device,
                           use_fp16=device.startswith("cuda"), batch_size=8, max_length=512)
    rerank_load = time.perf_counter() - started
    # Exercise the configured 15-candidate boundary, retain five results.
    pairs = [(query, documents[index % 3]) for index in range(15)]
    started = time.perf_counter()
    scores = np.asarray(reranker.compute_score(pairs))
    rerank_cold = time.perf_counter() - started
    started = time.perf_counter()
    warm_scores = np.asarray(reranker.compute_score(pairs))
    rerank_warm = time.perf_counter() - started
    params = list(reranker.model.parameters())
    if any(str(parameter.device) != device for parameter in params):
        raise ValueError("Reranker parameters are on the wrong device")
    expected_dtype = torch.float16 if device.startswith("cuda") else torch.float32
    if any(parameter.is_floating_point() and parameter.dtype != expected_dtype for parameter in params):
        raise ValueError("Reranker precision differs from GPU-FP16/CPU-FP32 contract")
    if scores.shape != (15,) or not np.isfinite(scores).all() or int(np.argmax(scores)) % 3 != 0:
        raise ValueError("Reranker semantic/output smoke check failed")
    if not np.allclose(scores, warm_scores, atol=0.03):
        raise ValueError("Reranker repeatability smoke check failed")
    return {"torch": torch.__version__, "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(), "device": device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "gpu_capability": list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
            "torch_cuda_arch_list": torch.cuda.get_arch_list(),
            "embedding": {"dimension": 1024, "dtype": embedding_dtype, "device": str(next(embedding.parameters()).device),
                "load_s": embed_load, "cold_s": embed_cold, "warm_s": embed_warm, "similarity": similarity.tolist()},
            "reranker": {"device": str(params[0].device), "dtype": str(params[0].dtype), "candidate_k": 15, "top_k": 5,
                "load_s": rerank_load, "cold_s": rerank_cold, "warm_s": rerank_warm, "scores": scores.tolist()},
            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated() if device.startswith("cuda") else 0}


def dependency_smoke(root: Path) -> dict:
    for name in ("fastapi", "uvicorn", "httpx", "openai", "aiosqlite", "jose", "passlib",
                 "PyPDF2", "docx", "pptx", "markdown_it", "PIL", "pypdfium2", "onnxruntime", "cryptography"):
        importlib.import_module(name)
    import bm25s
    import jieba
    import numpy as np
    import chromadb
    from chromadb.config import Settings
    jieba.dt.cache_file = str(root / "cache" / "jieba.cache")
    docs = [list(jieba.cut(text)) for text in ("知识库备份复制完整目录", "苹果树浇水", "配置模型密钥")]
    tokens = bm25s.tokenize([" ".join(words) for words in docs], stopwords=[], show_progress=False)
    sparse = bm25s.BM25(auto_compile=False)
    sparse.index(tokens, show_progress=False)
    path = root / "temp" / "bm25"
    sparse.save(path, show_progress=False)
    mmap = bm25s.BM25.load(path, mmap=True, load_corpus=False, show_progress=False, auto_compile=False)
    if not all(isinstance(mmap.scores[key], np.memmap) for key in ("data", "indices", "indptr")):
        raise ValueError("BM25S is not using actual mmap arrays")
    result, _ = mmap.retrieve(bm25s.tokenize(["知识库 备份"], stopwords=[], show_progress=False), k=1, show_progress=False)
    if int(result[0][0]) != 0:
        raise ValueError("BM25 mmap retrieval smoke failed")
    for key in ("data", "indices", "indptr"):
        mmap.scores[key]._mmap.close()
    client = chromadb.PersistentClient(path=str(root / "temp" / "chroma"), settings=Settings(anonymized_telemetry=False))
    collection = client.get_or_create_collection("w1_smoke", embedding_function=None)
    collection.upsert(ids=["one", "two"], embeddings=[[1.0, 0.0], [0.0, 1.0]], documents=["备份", "修剪"])
    if collection.query(query_embeddings=[[1.0, 0.0]], n_results=1)["ids"][0] != ["one"]:
        raise ValueError("Chroma local persistence/query failed")
    return {"imports": "passed", "bm25s_mmap": "passed", "chroma": "passed",
            "note": "Dependency-only synthetic smoke; not backend Hybrid/first-generation/UI acceptance"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cpu", "cuda:0"], required=True)
    parser.add_argument("--expect-no-driver", action="store_true")
    parser.add_argument("--report", default="probe.json", help="Filename under bundle/reports/")
    args = parser.parse_args()
    if Path(args.report).name != args.report or ":" in args.report:
        parser.error("report must be a filename, not an arbitrary path")
    root = Path(__file__).resolve().parents[1]
    events = []
    report = {"status": "failed", "python": platform.python_version(), "os": platform.platform(),
              "device_requested": args.device, "expect_no_driver": args.expect_no_driver, "scope": "W1 only",
              "guard_policy": "block network/covered external file writes; allow exact OS null device; native DLL IO is not fully traced"}
    try:
        if sys.platform != "win32" or sys.flags.isolated != 1 or not Path(sys.executable).resolve().is_relative_to(root / "runtime"):
            raise ValueError("Run with this bundle's embedded Windows Python")
        prepare(root)
        audit_guard(root, events)
        print("Verifying bundled hashes...", flush=True)
        report["payload"] = verify_payload(root)
        report["driver"] = driver_probe()
        if args.expect_no_driver and (args.device != "cpu" or report["driver"]["nvcuda_loadable"]):
            raise ValueError("No-driver acceptance requires CPU AND an actually absent/unloadable NVIDIA driver")
        print("Dependency imports/persistence smoke...", flush=True)
        report["dependencies"] = dependency_smoke(root)
        print(f"Real model inference on {args.device}...", flush=True)
        report["models"] = run_models(root, args.device)
        report["native_runtime_paths"] = native_runtime_paths(root)
        report["bundled_modules"] = assert_module_paths(root)
        report["versions"] = {item.metadata["Name"]: item.version for item in metadata.distributions()}
        import psutil
        report["peak_working_set_bytes"] = getattr(psutil.Process().memory_info(), "peak_wset", None)
        report["isolated"] = bool(sys.flags.isolated)
        if events:
            raise ValueError("Network/outside-write attempts occurred; examine guard_events")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    report["guard_events"] = events
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "device_requested", "expect_no_driver", "guard_events")}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
