"""Explicit portable defaults and workspace guard, before storage/model imports.

Source mode never calls these functions. Only the allowlisted v5-to-v6
conversation migration runs here; all paths derive from the bundled backend root.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid


MODELS = {
    "embedding": ("BAAI/bge-large-zh-v1.5", "79e7739b6ab944e86d6171e44d24c997fc1e0116"),
    "reranker": ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"),
}
WORKSPACE_VERSION = 1
STORAGE_SCHEMA = 6


def owned_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink() or path.is_junction():
        raise ValueError(f"便携路径越界或为链接：{relative}")
    return path


def model_path(root: Path, model_id: str) -> Path:
    """Resolve ONLY fixed bundled assets; never try a user's HF cache."""
    key = next((key for key, model in MODELS.items() if model[0] == model_id), None)
    if key is None:
        raise ValueError("便携模式仅支持随包的固定Embedding/Reranker模型")
    lock_path = owned_path(root, "locks/w1-lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    model = lock["models"][key]
    if (model["id"], model["revision"]) != MODELS[key] or not model["files"]:
        raise ValueError("随包模型版本与便携合同不一致，请重新解压完整包")
    directory = owned_path(root, f"models/{key}")
    for entry in model["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in entry["path"]:
            raise ValueError("模型清单路径无效")
        path = owned_path(root, f"models/{key}/{entry['path']}")
        if not path.is_file() or path.stat().st_size != entry["size"]:
            raise ValueError("随包模型资源缺失或大小不符，请重新解压完整包")
    return directory.resolve()


def _contract(root: Path) -> dict:
    return {"storage_schema": STORAGE_SCHEMA,
            "runtime_lock_sha256": hashlib.sha256(owned_path(root, "locks/w1-lock.json").read_bytes()).hexdigest(),
            "models": {key: {"id": value[0], "revision": value[1]} for key, value in MODELS.items()}}


def read_workspace(root: Path) -> dict:
    path = owned_path(root, "data/workspace.json")
    workspace = json.loads(path.read_text(encoding="utf-8"))
    expected = _contract(root)
    previous = {**expected, "storage_schema": 5}
    if workspace.get("format") != WORKSPACE_VERSION or workspace.get("contract") not in (expected, previous):
        raise ValueError("便携工作空间版本不兼容，已拒绝打开；请使用匹配版本和完整备份")
    identifier = str(uuid.UUID(workspace["workspace_id"]))
    if (workspace.get("corpus_id") != "portable-" + identifier
            or type(workspace.get("sparse_initialized")) is not bool
            or type(workspace.get("sparse_publication_pending", False)) is not bool):
        raise ValueError("便携工作空间标识或首发状态无效")
    return workspace


def _save_workspace(root: Path, workspace: dict, *, initial: bool = False) -> None:
    path = owned_path(root, "data/workspace.json")
    payload = json.dumps(workspace, ensure_ascii=False, indent=2) + "\n"
    if initial:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(f".workspace-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight_storage(root: Path) -> int | None:
    """Read existing SQLite version before any business imports may migrate it."""
    database = owned_path(root, "data/metadata.db")
    if not database.exists():
        return
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        versions = connection.execute("SELECT schema_version FROM schema_meta").fetchall()
        if versions not in ([(5,)], [(STORAGE_SCHEMA,)]):
            raise ValueError("便携数据库schema不兼容，拒绝隐式迁移")
        return versions[0][0]
    finally:
        connection.close()


def _upgrade_conversation_storage(root: Path, workspace: dict, version: int | None) -> None:
    """唯一放行的v5→v6保留性迁移：先SQLite一致性备份，再加列/表。"""
    if version == 5:
        database = owned_path(root, "data/metadata.db")
        directory = owned_path(root, "data/migration-backups")
        directory.mkdir(exist_ok=True)
        backup = owned_path(root, f"data/migration-backups/metadata-v5-{uuid.uuid4().hex}.db")
        with backup.open("xb"):
            pass
        source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(backup)
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
            source.close()
        from backend.services.metadata_schema import bootstrap_structured_database

        bootstrap_structured_database(str(database), created_at="portable-v6-migration")
    if workspace["contract"] != _contract(root):
        workspace["contract"] = _contract(root)
        _save_workspace(root, workspace)


def prepare_environment(root: Path, device: str, batch_profile: str = "normal") -> dict[str, str]:
    """Return a private environment mapping; inherited deployment values ignored."""
    root = root.resolve()
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("RAG_PORTABLE_DEVICE只能是cpu或cuda:0，不会静默回退")
    if batch_profile not in {"normal", "reduced"}:
        raise ValueError("便携batch profile只能是normal或reduced")
    # Check every known persistent/output path before mkdir/import side effects.
    directories = ("data", "data/uploads", "data/chroma_db", "data/sparse_index",
                   "data/vision_cache", "data/ocr_images", "cache", "temp", "logs", "state")
    files = ("data/workspace.json", "data/metadata.db", "data/metadata.db-wal", "data/metadata.db-shm",
             "data/runtime_secrets.bin", "data/sparse_index/active.json")
    for relative in (*directories, *files):
        owned_path(root, relative)
    for model in MODELS.values():
        model_path(root, model[0])
    data = root / "data"
    if (data / "workspace.json").exists():
        workspace = read_workspace(root)
        version = _preflight_storage(root)
        _upgrade_conversation_storage(root, workspace, version)
    else:
        if data.exists() and any(data.iterdir()):
            raise ValueError("data已有内容但无便携工作空间标识，拒绝接管或迁移现有知识库")
        workspace_id = str(uuid.uuid4())
        workspace = {"format": WORKSPACE_VERSION, "workspace_id": workspace_id,
                     "corpus_id": "portable-" + workspace_id, "contract": _contract(root),
                     "sparse_initialized": False, "sparse_publication_pending": False}
        data.mkdir(exist_ok=True)
        _save_workspace(root, workspace, initial=True)
    for relative in directories:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for variable in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME",
                     "XDG_CACHE_HOME", "NUMBA_CACHE_DIR", "MPLCONFIGDIR", "CUDA_CACHE_PATH",
                     "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
        path = owned_path(root, f"cache/{variable.lower()}")
        path.mkdir(exist_ok=True)
        os.environ[variable] = str(path)
    for variable in ("TEMP", "TMP", "TMPDIR"):
        os.environ[variable] = str(root / "temp")
    tempfile.tempdir = str(root / "temp")
    for variable in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE",
                     "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        os.environ[variable] = "true"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    for variable in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "VISION_API_KEY"):
        os.environ.pop(variable, None)
    return {
        "UPLOAD_DIR": str(root / "data/uploads"), "METADATA_DB_PATH": str(root / "data/metadata.db"),
        "CHROMA_PERSIST_DIR": str(root / "data/chroma_db"),
        "SPARSE_INDEX_ROOT": str(root / "data/sparse_index"), "SPARSE_INDEX_CORPUS_ID": workspace["corpus_id"],
        "VISION_CACHE_DIR": str(root / "data/vision_cache"), "OCR_IMAGE_DIR": str(root / "data/ocr_images"),
        "LLM_ENABLED": "false", "DEEPSEEK_API_KEY": "", "VISION_API_KEY": "", "OCR_ENABLED": "false",
        "CHUNK_STRATEGY": "recursive", "CHUNK_SIZE": "400", "CHUNK_OVERLAP": "80",
        "RETRIEVAL_MODE": "hybrid", "DENSE_CANDIDATE_K": "15", "RERANKER_CANDIDATE_K": "15",
        "RETRIEVAL_TOP_K": "5", "BM25_TOP_K": "15", "SPARSE_BACKEND": "bm25s_mmap",
        "RERANKER_ENABLED": "true", "EMBEDDING_DEVICE": device, "RERANKER_DEVICE": device,
        "RERANKER_USE_FP16": "true" if device.startswith("cuda") else "false",
        "EMBEDDING_BATCH_SIZE": "16" if batch_profile == "reduced" else "32",
        "RERANKER_BATCH_SIZE": "4" if batch_profile == "reduced" else "8",
        "EMBEDDING_MODEL": MODELS["embedding"][0], "RERANKER_MODEL": MODELS["reranker"][0],
        "VISION_SOURCE": "follow_llm", "CITATION_PREVIEW_ENABLED": "true",
        "RETRIEVAL_TRACE_ENABLED": "false", "PREWARM_MODELS": "true",
    }


def _bound_workspace(settings) -> tuple[Path, dict] | None:
    if getattr(settings, "DISTRIBUTION_MODE", "source") != "portable":
        return None
    root = Path(settings.BASE_DIR).resolve()
    if Path(settings.SPARSE_INDEX_ROOT).resolve() != owned_path(root, "data/sparse_index"):
        raise ValueError("便携Sparse根目录与工作空间不匹配")
    workspace = read_workspace(root)
    if settings.SPARSE_INDEX_CORPUS_ID != workspace["corpus_id"]:
        raise ValueError("便携Sparse corpus与工作空间不匹配")
    return root, workspace


def may_bootstrap(settings) -> bool:
    bound = _bound_workspace(settings)
    return bound is not None and not bound[1]["sparse_initialized"]


def record_sparse_publication(settings, generation_id: str) -> None:
    """Durable BEFORE active.json publication: a lost pointer never means new data.

    A crash at this barrier may ONLY replay this exact validated candidate; it
    cannot authorize a new initial generation when a published pointer is lost.
    """
    bound = _bound_workspace(settings)
    if bound is not None and not bound[1]["sparse_initialized"]:
        root, workspace = bound
        workspace.update(sparse_initialized=True, initial_generation=generation_id,
                         sparse_publication_pending=True)
        _save_workspace(root, workspace)


def pending_publication(settings) -> str | None:
    bound = _bound_workspace(settings)
    if bound is not None and bound[1].get("sparse_publication_pending"):
        return str(bound[1]["initial_generation"])
    return None


def complete_sparse_publication(settings) -> None:
    bound = _bound_workspace(settings)
    if bound is not None and bound[1].get("sparse_publication_pending"):
        root, workspace = bound
        workspace["sparse_publication_pending"] = False
        _save_workspace(root, workspace)


def snapshot_oracle(chunks: list[dict]) -> tuple[list[dict], dict]:
    """Bounded deterministic implementation checks, not answer-quality questions."""
    import unicodedata
    from backend.services.sparse_tokenizer import tokenize

    selected = sorted(chunks, key=lambda row: hashlib.sha256(row["chunk_id"].encode()).hexdigest())[:32]
    queries = []
    for row in selected:
        tokens = list(dict.fromkeys(tokenize(row["content"][:2048])))[:12]
        for terms in (tokens[:1], tokens[:3]):
            query = " ".join(terms)
            if query and query not in queries:
                queries.append(query)
    seed = hashlib.sha256(json.dumps(queries, ensure_ascii=False).encode()).hexdigest()
    negative = "w2nomatch" + seed
    while any(negative in unicodedata.normalize("NFKC", row["content"]).lower() for row in chunks):
        negative += "x"
    queries.append(negative)
    payload = json.dumps(queries, ensure_ascii=False, separators=(",", ":")).encode()
    return ([{"question": query} for query in queries],
            {"policy": "portable-snapshot-v1", "count": len(queries), "sha256": hashlib.sha256(payload).hexdigest()})
