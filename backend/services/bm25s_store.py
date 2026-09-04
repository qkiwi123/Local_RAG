"""BM25S generation构建、完整性校验、mmap载入和原子发布。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
import uuid

import bm25s
import jieba
import numpy as np

from backend.services.sparse_index import (
    SparseHit,
    SparseIndexBuildError,
    SparseIndexCorruptError,
    SparseIndexDirtyError,
    SparseIndexMismatchError,
    SparseIndexMissingError,
)
from backend.services.sparse_tokenizer import TOKENIZER_CONTRACT


SCHEMA_VERSION = 1
INDEX_FORMAT = "bm25s-rank-bm25-compat-v1"
METHOD = "atire"
IDF_METHOD = "rank_bm25_robertson_epsilon"
DTYPE = "float64"
INT_DTYPE = "int32"
BACKEND = "numpy"
CSC_BACKEND = "numpy"
REQUIRED_INDEX_FILES = (
    "data.csc.index.npy",
    "indices.csc.index.npy",
    "indptr.csc.index.npy",
    "vocab.index.json",
    "params.index.json",
    "chunk_ids.jsonl",
)
_IDF_SELECTOR_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_generation_id(value: str) -> str:
    if not value or len(value) > 100 or not all(
        char.isalnum() or char in "-_" for char in value
    ):
        raise SparseIndexBuildError("generation_id仅允许字母、数字、短横线和下划线")
    if value.startswith("."):
        raise SparseIndexBuildError("generation_id不能是隐藏或临时目录名")
    return value


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _write_json(path: Path, payload: Any, *, exclusive: bool = True) -> None:
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical(manifest))


def _chunk_ids_hash(chunk_ids: Sequence[str]) -> str:
    return _sha256_bytes("\n".join(chunk_ids).encode("utf-8"))


def _rows_digest(rows: Sequence[Mapping[str, Any]], field: str) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical({"chunk_id": row["chunk_id"], field: row[field]}))
        digest.update(b"\n")
    return digest.hexdigest()


@contextmanager
def _compat_idf_selector(*, average_idf: float, epsilon: float) -> Iterator[None]:
    """在构建临界区注册P1验证过的rank_bm25兼容IDF。"""
    original = bm25s._select_idf_scorer

    def compat_idf(df: int, N: int) -> float:
        raw = math.log(N - df + 0.5) - math.log(df + 0.5)
        return epsilon * average_idf if raw < 0 else raw

    def select(method: str) -> Callable[..., float]:
        if method == IDF_METHOD:
            return compat_idf
        return original(method)

    with _IDF_SELECTOR_LOCK:
        bm25s._select_idf_scorer = select
        try:
            yield
        finally:
            bm25s._select_idf_scorer = original


def _validate_chunks(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not chunks:
        raise SparseIndexBuildError("空语料不能构建持久化BM25索引")
    normalized = []
    for index, row in enumerate(chunks):
        chunk_id = str(row.get("chunk_id") or "").strip()
        content = row.get("content")
        metadata = row.get("metadata") or {}
        if not chunk_id:
            raise SparseIndexBuildError(f"第{index + 1}个chunk缺少稳定chunk_id")
        if not isinstance(content, str):
            raise SparseIndexBuildError(f"chunk {chunk_id}正文不是字符串")
        if not isinstance(metadata, Mapping):
            raise SparseIndexBuildError(f"chunk {chunk_id} metadata不是对象")
        document_id = str(metadata.get("document_id") or "").strip()
        if not document_id:
            raise SparseIndexBuildError(f"chunk {chunk_id}缺少document_id")
        normalized.append(
            {"chunk_id": chunk_id, "content": content, "metadata": dict(metadata)}
        )
    normalized.sort(key=lambda row: row["chunk_id"])
    ids = [row["chunk_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise SparseIndexBuildError("chunk_id重复，拒绝构建")
    return normalized


def _validate_parameters(k1: float, b: float, epsilon: float) -> None:
    if not math.isfinite(k1) or k1 <= 0:
        raise SparseIndexBuildError("k1必须是大于0的有限数")
    if not math.isfinite(b) or not 0 <= b <= 1:
        raise SparseIndexBuildError("b必须是0到1之间的有限数")
    if not math.isfinite(epsilon) or epsilon < 0:
        raise SparseIndexBuildError("epsilon必须是非负有限数")


def _file_manifest(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "bytes": (directory / name).stat().st_size,
            "sha256": _sha256_file(directory / name),
        }
        for name in REQUIRED_INDEX_FILES
    }


def _index_bytes(files: Mapping[str, Mapping[str, Any]]) -> int:
    return sum(int(info["bytes"]) for info in files.values())


def _default_generation_id(corpus_hash: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"bm25-{stamp}-{corpus_hash[:12]}"


def _stable_positive_ids(
    scores: np.ndarray, chunk_ids: Sequence[str], top_k: int
) -> list[str]:
    return [
        chunk_ids[ordinal]
        for ordinal, _score in sorted(
            (
                (ordinal, float(score))
                for ordinal, score in enumerate(scores)
                if float(score) > 0.0
            ),
            key=lambda item: (-item[1], chunk_ids[item[0]]),
        )[:top_k]
    ]


def _verify_against_rank_bm25(
    *,
    model: Any,
    corpus_tokens: Sequence[Sequence[str]],
    chunk_ids: Sequence[str],
    questions: Sequence[Mapping[str, Any]],
    tokenize: Callable[[str], list[str]],
    k1: float,
    b: float,
    epsilon: float,
    oracle: Any | None = None,
) -> dict[str, Any]:
    from rank_bm25 import BM25Okapi

    if not questions:
        return {"executed": False, "passed": None, "question_count": 0}
    oracle = oracle or BM25Okapi(corpus_tokens, k1=k1, b=b, epsilon=epsilon)
    k_values = (1, 5, 15, 50)
    exact = {str(k): 0 for k in k_values}
    max_delta = 0.0
    for index, question in enumerate(questions):
        text = question.get("question")
        if not isinstance(text, str):
            raise SparseIndexBuildError(f"第{index + 1}道oracle问题缺少question文本")
        tokens = tokenize(text)
        expected_scores = np.asarray(oracle.get_scores(tokens), dtype=np.float64)
        actual_scores = np.asarray(model.get_scores(tokens), dtype=np.float64)
        if expected_scores.shape != actual_scores.shape:
            raise SparseIndexBuildError("oracle与BM25S分数数组形状不一致")
        if expected_scores.size:
            max_delta = max(
                max_delta,
                float(np.max(np.abs(expected_scores - actual_scores))),
            )
        for k in k_values:
            if _stable_positive_ids(expected_scores, chunk_ids, k) == _stable_positive_ids(
                actual_scores, chunk_ids, k
            ):
                exact[str(k)] += 1
    passed = max_delta == 0.0 and all(
        value == len(questions) for value in exact.values()
    )
    result = {
        "executed": True,
        "passed": passed,
        "question_count": len(questions),
        "k_values": list(k_values),
        "exact_order_questions": exact,
        "max_absolute_score_delta": max_delta,
    }
    if not passed:
        raise SparseIndexBuildError(f"generation未通过rank_bm25 oracle：{result}")
    return result


def _contract_payload(
    *,
    corpus_id: str,
    tokenizer: Mapping[str, Any],
    chunking: Mapping[str, Any],
    source: Mapping[str, Any],
    k1: float,
    b: float,
    epsilon: float,
) -> dict[str, Any]:
    return {
        "corpus_id": corpus_id,
        "tokenizer": dict(tokenizer),
        "chunking": dict(chunking),
        "source": dict(source),
        "bm25": {
            "implementation": "bm25s",
            "bm25s_version": bm25s.__version__,
            "compatibility_oracle": "rank_bm25.BM25Okapi",
            "method": METHOD,
            "idf_method": IDF_METHOD,
            "k1": k1,
            "b": b,
            "epsilon": epsilon,
            "dtype": DTYPE,
            "int_dtype": INT_DTYPE,
            "backend": BACKEND,
            "csc_backend": CSC_BACKEND,
        },
    }


def build_generation(
    *,
    chunks: Sequence[Mapping[str, Any]],
    target_root: str | Path,
    corpus_id: str,
    tokenize: Callable[[str], list[str]],
    tokenizer: Mapping[str, Any],
    chunking: Mapping[str, Any],
    source: Mapping[str, Any],
    k1: float = 1.5,
    b: float = 0.75,
    epsilon: float = 0.25,
    generation_id: str | None = None,
    publish: bool = False,
    replace_active: bool = False,
    oracle_questions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """构建不可变generation；校验成功后原子落位，并可原子发布指针。"""
    started = time.perf_counter()
    _validate_parameters(k1, b, epsilon)
    rows = _validate_chunks(chunks)
    if not corpus_id.strip():
        raise SparseIndexBuildError("corpus_id不能为空")
    chunk_ids = [row["chunk_id"] for row in rows]
    corpus_hash = _rows_digest(rows, "content")
    generation_id = _safe_generation_id(
        generation_id or _default_generation_id(corpus_hash)
    )
    unresolved_root = Path(target_root)
    if unresolved_root.exists() and _is_link(unresolved_root):
        raise SparseIndexBuildError("target_root不能是符号链接或junction")
    root = unresolved_root.resolve()
    generations = root / "generations"
    final_dir = generations / generation_id
    if generations.exists() and _is_link(generations):
        raise SparseIndexBuildError("generations不能是符号链接或junction")
    if final_dir.exists():
        raise FileExistsError(f"generation已存在，拒绝覆盖：{final_dir}")
    if publish and (root / "active.json").exists() and not replace_active:
        raise FileExistsError("已有active.json；发布新generation需显式replace_active")
    generations.mkdir(parents=True, exist_ok=True)
    temp_dir = generations / f".tmp-{generation_id}-{uuid.uuid4().hex}"
    temp_dir.mkdir()
    try:
        token_started = time.perf_counter()
        corpus_tokens = [tokenize(row["content"]) for row in rows]
        tokenize_s = time.perf_counter() - token_started
        if not any(corpus_tokens):
            raise SparseIndexBuildError("语料分词结果为空，不能构建BM25索引")
        # 直接使用兼容oracle计算出的average_idf，连浮点累加顺序也保持一致；
        # 独立按set/Counter重算会产生约1e-12差异，不能满足P1的分数严格门禁。
        from rank_bm25 import BM25Okapi

        oracle_model = BM25Okapi(corpus_tokens, k1=k1, b=b, epsilon=epsilon)
        average_idf = float(oracle_model.average_idf)
        index_started = time.perf_counter()
        with _compat_idf_selector(average_idf=average_idf, epsilon=epsilon):
            model = bm25s.BM25(
                method=METHOD,
                idf_method=IDF_METHOD,
                k1=k1,
                b=b,
                dtype=DTYPE,
                int_dtype=INT_DTYPE,
                backend=BACKEND,
                csc_backend=CSC_BACKEND,
                auto_compile=False,
            )
            # BM25S的字符串token入口用set创建词表，跨进程文件hash不稳定。
            # 这里固定Unicode词项升序及连续ordinal，再走官方token-ID入口。
            vocabulary = {
                token: ordinal
                for ordinal, token in enumerate(
                    sorted({token for tokens in corpus_tokens for token in tokens})
                )
            }
            corpus_token_ids = [
                [vocabulary[token] for token in tokens] for tokens in corpus_tokens
            ]
            model.index(
                (corpus_token_ids, vocabulary),
                create_empty_token=True,
                show_progress=False,
            )
        index_s = time.perf_counter() - index_started
        oracle_started = time.perf_counter()
        oracle_result = _verify_against_rank_bm25(
            model=model,
            corpus_tokens=corpus_tokens,
            chunk_ids=chunk_ids,
            questions=oracle_questions,
            tokenize=tokenize,
            k1=k1,
            b=b,
            epsilon=epsilon,
            oracle=oracle_model,
        )
        oracle_result["wall_s"] = time.perf_counter() - oracle_started
        save_started = time.perf_counter()
        model.save(temp_dir, corpus=None, show_progress=False)
        with (temp_dir / "chunk_ids.jsonl").open(
            "x", encoding="utf-8", newline="\n"
        ) as handle:
            for ordinal, chunk_id in enumerate(chunk_ids):
                handle.write(
                    json.dumps(
                        {"ordinal": ordinal, "chunk_id": chunk_id},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ) + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        save_s = time.perf_counter() - save_started
        files = _file_manifest(temp_dir)
        contract = _contract_payload(
            corpus_id=corpus_id,
            tokenizer=tokenizer,
            chunking=chunking,
            source=source,
            k1=k1,
            b=b,
            epsilon=epsilon,
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "index_format": INDEX_FORMAT,
            "generation_id": generation_id,
            "status": "validated",
            "created_at": _now(),
            "contract": contract,
            "contract_sha256": _sha256_bytes(_canonical(contract)),
            "corpus": {
                "document_count": len(
                    {str(row["metadata"]["document_id"]) for row in rows}
                ),
                "chunk_count": len(rows),
                "ordered_chunk_ids_sha256": _chunk_ids_hash(chunk_ids),
                "content_sha256": corpus_hash,
                "metadata_sha256": _rows_digest(rows, "metadata"),
            },
            "compatibility": {
                "p1_gate": "120q-k1-k5-k15-k50-ordered-and-score-exact",
                "average_raw_robertson_idf": average_idf,
                "oracle": oracle_result,
            },
            "build": {
                "python": sys.version,
                "platform": sys.platform,
                "jieba_version": getattr(jieba, "__version__", None),
                "numpy_version": np.__version__,
                "tokenize_s": tokenize_s,
                "index_s": index_s,
                "save_s": save_s,
            },
            "files": files,
            "index_bytes": _index_bytes(files),
            "body_storage": "chunk_id_only; content_and_metadata_remain_in_chroma",
        }
        _write_json(temp_dir / "manifest.json", manifest)
        loaded = load_generation(
            temp_dir,
            expected_contract=contract,
            expected_generation_id=generation_id,
        )
        try:
            if not loaded.is_mmap:
                raise SparseIndexCorruptError("构建后重载未使用mmap")
        finally:
            loaded.close()
        os.replace(temp_dir, final_dir)
        if publish:
            activate_generation(root, generation_id, allow_replace=replace_active)
        return {
            "generation_id": generation_id,
            "generation_dir": str(final_dir),
            "manifest_hash": _manifest_hash(manifest),
            "document_count": manifest["corpus"]["document_count"],
            "chunk_count": manifest["corpus"]["chunk_count"],
            "index_bytes": manifest["index_bytes"],
            "published": publish,
            "wall_s": time.perf_counter() - started,
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    if not path.is_file():
        raise SparseIndexMissingError(f"缺少manifest：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SparseIndexCorruptError(f"manifest不可读：{path}") from error
    if not isinstance(payload, dict):
        raise SparseIndexCorruptError("manifest根节点必须是对象")
    return payload


def _read_chunk_ids(path: Path) -> list[str]:
    chunk_ids = []
    try:
        with path.open(encoding="utf-8") as handle:
            for expected, line in enumerate(handle):
                row = json.loads(line)
                if row.get("ordinal") != expected or not row.get("chunk_id"):
                    raise SparseIndexCorruptError("chunk_ids ordinal或ID无效")
                chunk_ids.append(str(row["chunk_id"]))
    except SparseIndexCorruptError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise SparseIndexCorruptError("chunk_ids.jsonl不可读") from error
    if len(chunk_ids) != len(set(chunk_ids)):
        raise SparseIndexCorruptError("chunk_ids.jsonl包含重复ID")
    return chunk_ids


def _assert_contract(actual: Any, expected: Any, path: str = "contract") -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            raise SparseIndexMismatchError(f"{path}类型不匹配")
        for key, value in expected.items():
            if key not in actual:
                raise SparseIndexMismatchError(f"{path}.{key}缺失")
            _assert_contract(actual[key], value, f"{path}.{key}")
    elif actual != expected:
        raise SparseIndexMismatchError(
            f"{path}不匹配：索引={actual!r}，期望={expected!r}"
        )


class LoadedBM25SIndex:
    def __init__(
        self,
        *,
        directory: Path,
        manifest: dict[str, Any],
        model: Any,
        chunk_ids: list[str],
    ) -> None:
        self.directory = directory
        self.manifest = manifest
        self.model = model
        self.chunk_ids = chunk_ids
        self.generation_id = str(manifest["generation_id"])
        self.manifest_hash = _manifest_hash(manifest)
        self.is_mmap = all(
            isinstance(value, np.memmap)
            for key, value in model.scores.items()
            if key != "num_docs"
        )

    def search_tokens(self, tokens: Sequence[str], top_k: int) -> list[SparseHit]:
        if top_k <= 0 or not tokens:
            return []
        scores = np.asarray(self.model.get_scores(list(tokens)), dtype=np.float64)
        ranked = sorted(
            (
                (ordinal, float(score))
                for ordinal, score in enumerate(scores)
                if float(score) > 0.0
            ),
            key=lambda item: (-item[1], self.chunk_ids[item[0]]),
        )[:top_k]
        return [
            SparseHit(
                chunk_id=self.chunk_ids[ordinal],
                score=score,
                rank=rank,
                ordinal=ordinal,
            )
            for rank, (ordinal, score) in enumerate(ranked, 1)
        ]

    def close(self) -> None:
        _close_model_mmaps(self.model)

    def __enter__(self) -> "LoadedBM25SIndex":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def load_generation(
    generation_dir: str | Path,
    *,
    expected_contract: Mapping[str, Any] | None = None,
    expected_generation_id: str | None = None,
) -> LoadedBM25SIndex:
    unresolved = Path(generation_dir)
    if _is_link(unresolved):
        raise SparseIndexCorruptError("generation不能是符号链接或junction")
    directory = unresolved.resolve()
    if not directory.is_dir():
        raise SparseIndexMissingError(f"generation目录不存在：{directory}")
    manifest = _read_manifest(directory)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SparseIndexMismatchError("Sparse manifest schema版本不匹配")
    if manifest.get("index_format") != INDEX_FORMAT:
        raise SparseIndexMismatchError("Sparse index格式不匹配")
    expected_name = expected_generation_id or directory.name
    if manifest.get("generation_id") != expected_name:
        raise SparseIndexMismatchError("manifest generation_id与目录名不匹配")
    if manifest.get("status") != "validated":
        raise SparseIndexCorruptError("generation不是validated状态")
    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        raise SparseIndexCorruptError("manifest缺少contract")
    if manifest.get("contract_sha256") != _sha256_bytes(_canonical(contract)):
        raise SparseIndexCorruptError("contract hash不匹配")
    if expected_contract is not None:
        _assert_contract(contract, expected_contract)
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(REQUIRED_INDEX_FILES):
        raise SparseIndexCorruptError("manifest文件清单不完整")
    for name in REQUIRED_INDEX_FILES:
        path = directory / name
        if _is_link(path):
            raise SparseIndexCorruptError(f"索引文件不能是链接：{name}")
        if not path.is_file():
            raise SparseIndexMissingError(f"缺少索引文件：{path}")
        info = files[name]
        if path.stat().st_size != info.get("bytes") or _sha256_file(path) != info.get("sha256"):
            raise SparseIndexCorruptError(f"索引文件hash或大小不匹配：{name}")
    chunk_ids = _read_chunk_ids(directory / "chunk_ids.jsonl")
    corpus = manifest.get("corpus") or {}
    if len(chunk_ids) != corpus.get("chunk_count"):
        raise SparseIndexCorruptError("chunk数量与manifest不一致")
    if _chunk_ids_hash(chunk_ids) != corpus.get("ordered_chunk_ids_sha256"):
        raise SparseIndexCorruptError("有序chunk ID hash不一致")
    try:
        model = bm25s.BM25.load(
            directory,
            mmap=True,
            load_corpus=False,
            show_progress=False,
            auto_compile=False,
        )
    except Exception as error:
        raise SparseIndexCorruptError("BM25S索引无法mmap载入") from error
    try:
        if model.scores.get("num_docs") != len(chunk_ids):
            raise SparseIndexCorruptError("BM25S num_docs与chunk映射不一致")
        if not all(
            isinstance(value, np.memmap)
            for key, value in model.scores.items()
            if key != "num_docs"
        ):
            raise SparseIndexCorruptError("BM25S数组未按mmap只读载入")
    except Exception:
        _close_model_mmaps(model)
        raise
    return LoadedBM25SIndex(
        directory=directory, manifest=manifest, model=model, chunk_ids=chunk_ids
    )


def _close_model_mmaps(model: Any) -> None:
    for key, value in model.scores.items():
        if key == "num_docs":
            continue
        mmap_handle = getattr(value, "_mmap", None)
        if mmap_handle is not None:
            mmap_handle.close()


def activate_generation(
    target_root: str | Path,
    generation_id: str,
    *,
    allow_replace: bool = False,
) -> dict[str, Any]:
    root = Path(target_root).resolve()
    generation_id = _safe_generation_id(generation_id)
    generation_dir = root / "generations" / generation_id
    loaded = load_generation(generation_dir)
    loaded.close()
    active = root / "active.json"
    if _is_link(active):
        raise SparseIndexCorruptError("active.json不能是链接")
    if active.exists() and not allow_replace:
        raise FileExistsError("active.json已存在，拒绝隐式切换")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "generation_id": generation_id,
        "manifest_hash": _manifest_hash(_read_manifest(generation_dir)),
        "updated_at": _now(),
        "dirty_reason": None,
    }
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".active-{uuid.uuid4().hex}.tmp"
    _write_json(temporary, payload)
    os.replace(temporary, active)
    return payload


def mark_active_dirty(target_root: str | Path, reason: str) -> dict[str, Any]:
    root = Path(target_root).resolve()
    active = root / "active.json"
    if _is_link(active):
        raise SparseIndexCorruptError("active.json不能是链接")
    if not active.is_file():
        raise SparseIndexMissingError("active.json不存在")
    try:
        payload = json.loads(active.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SparseIndexCorruptError("active.json不可读") from error
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("status") not in {
        "ready", "dirty"
    }:
        raise SparseIndexCorruptError("active.json合同无效")
    payload.update(status="dirty", dirty_reason=reason or "corpus_changed", updated_at=_now())
    temporary = root / f".active-{uuid.uuid4().hex}.tmp"
    _write_json(temporary, payload)
    os.replace(temporary, active)
    return payload


def load_active(
    target_root: str | Path,
    *,
    expected_contract: Mapping[str, Any] | None = None,
) -> LoadedBM25SIndex:
    root = Path(target_root).resolve()
    active_path = root / "active.json"
    if _is_link(active_path):
        raise SparseIndexCorruptError("active.json不能是链接")
    if not active_path.is_file():
        raise SparseIndexMissingError("active.json不存在")
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SparseIndexCorruptError("active.json不可读") from error
    if active.get("schema_version") != SCHEMA_VERSION:
        raise SparseIndexMismatchError("active schema版本不匹配")
    if active.get("status") == "dirty":
        raise SparseIndexDirtyError(str(active.get("dirty_reason") or "索引已dirty"))
    if active.get("status") != "ready":
        raise SparseIndexCorruptError("active状态不是ready")
    generation_id = _safe_generation_id(str(active.get("generation_id") or ""))
    directory = root / "generations" / generation_id
    manifest = _read_manifest(directory)
    if active.get("manifest_hash") != _manifest_hash(manifest):
        raise SparseIndexCorruptError("active指针的manifest hash不匹配")
    return load_generation(directory, expected_contract=expected_contract)
