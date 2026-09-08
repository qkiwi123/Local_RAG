"""S2 只读设置视图和本地推理运行状态。

运行状态只查看 lru_cache 元数据；缓存为空时绝不调用模型 getter。Hugging Face
缓存检查固定使用 local_files_only，不触发网络下载。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.config import settings
from backend.models.settings_schemas import (
    APIKeyStatus,
    EffectiveSettingsSnapshot,
    IndexRuntimeStatus,
    LLMSettingsView,
    ModelRuntimeStatus,
    SettingsReadResponse,
    SettingsRuntimeStatusResponse,
    SettingsRuntimeSummary,
    TorchRuntimeStatus,
)
from backend.services.model_cache import resolve_cached_snapshot
from backend.services.parsers.constants import PARSER_VERSION


_LLM_FIELDS = (
    "enabled",
    "provider",
    "base_url",
    "model",
    "temperature",
    "max_tokens",
    "timeout_seconds",
)
_RETRIEVAL_FIELDS = (
    "mode",
    "final_top_k",
    "dense_candidate_k",
    "dense_distance_threshold",
    "bm25_top_k",
    "bm25_k1",
    "bm25_b",
    "rrf_k",
    "reranker_enabled",
    "reranker_candidate_k",
    "max_chunks_per_document",
)


def _masked_key_status(snapshot: EffectiveSettingsSnapshot) -> APIKeyStatus:
    secret = (
        snapshot.api_key.get_secret_value()
        if snapshot.api_key is not None
        else None
    )
    public_source = (
        "runtime"
        if snapshot.api_key_source == "runtime_secret"
        else snapshot.api_key_source
    )
    # 很短的 Key 不返回 suffix，避免“后缀”实际等于完整密钥。
    suffix = secret[-4:] if secret is not None and len(secret) >= 8 else None
    return APIKeyStatus(
        configured=secret is not None,
        source=public_source,
        masked_suffix=suffix,
    )


def build_settings_response(
    snapshot: EffectiveSettingsSnapshot,
) -> SettingsReadResponse:
    field_effects = {
        **{f"llm.{name}": "next_request" for name in _LLM_FIELDS},
        **{
            f"retrieval.{name}": "next_request"
            for name in _RETRIEVAL_FIELDS
        },
        "llm.api_key": "next_request",
        "retrieval.refusal_gate.enabled": "unavailable",
    }
    return SettingsReadResponse(
        revision=snapshot.revision,
        updated_at=snapshot.updated_at,
        llm=LLMSettingsView(
            **snapshot.llm.model_dump(),
            api_key=_masked_key_status(snapshot),
        ),
        retrieval=snapshot.retrieval,
        runtime=SettingsRuntimeSummary(),
        field_sources=dict(snapshot.field_sources),
        field_effects=field_effects,
    )


def _model_cache_available(model_id: str) -> bool:
    try:
        resolve_cached_snapshot(model_id)
        return True
    except Exception:
        return False


def _cache_size(getter) -> int:
    cache_info = getattr(getter, "cache_info", None)
    if cache_info is None:
        return 0
    try:
        return int(cache_info().currsize)
    except Exception:
        return 0


def _read_actual_device(model) -> str | None:
    actual = getattr(model, "device", None)
    if actual is None:
        try:
            actual = next(model.parameters()).device
        except Exception:
            return None
    try:
        import torch

        return str(torch.device(actual))
    except Exception:
        return None


def _device_matches(configured: str, actual: str) -> bool:
    try:
        import torch

        expected_device = torch.device(configured)
        actual_device = torch.device(actual)
    except (ImportError, RuntimeError, TypeError, ValueError):
        return False
    if expected_device.type != actual_device.type:
        return False
    if expected_device.type != "cuda":
        return True
    expected_index = expected_device.index if expected_device.index is not None else 0
    actual_index = actual_device.index if actual_device.index is not None else 0
    return expected_index == actual_index


def _model_runtime_status(
    *,
    getter,
    model_id: str,
    enabled: bool,
    configured_device: str,
    batch_size: int,
    fp16: bool | None,
    nested_model_attr: str | None = None,
) -> ModelRuntimeStatus:
    cache_available = _model_cache_available(model_id)
    if _cache_size(getter) == 0:
        return ModelRuntimeStatus(
            model=model_id,
            enabled=enabled,
            configured_device=configured_device,
            actual_device=None,
            loaded=False,
            verified=False,
            cache_available=cache_available,
            batch_size=batch_size,
            fp16=fp16,
        )

    # currsize > 0 时 getter() 是 cache hit；缓存为空时上面的分支保证不调用。
    try:
        cached = getter()
    except Exception:
        return ModelRuntimeStatus(
            model=model_id,
            enabled=enabled,
            configured_device=configured_device,
            actual_device=None,
            loaded=True,
            verified=False,
            cache_available=cache_available,
            batch_size=batch_size,
            fp16=fp16,
            verification_error="cached_model_unavailable",
        )
    target = (
        getattr(cached, nested_model_attr, None)
        if nested_model_attr
        else cached
    )
    actual_device = _read_actual_device(target)
    if actual_device is None:
        verification_error = "actual_device_unavailable"
        verified = False
    else:
        verified = _device_matches(configured_device, actual_device)
        verification_error = None if verified else "device_mismatch"
    return ModelRuntimeStatus(
        model=model_id,
        enabled=enabled,
        configured_device=configured_device,
        actual_device=actual_device,
        loaded=True,
        verified=verified,
        cache_available=cache_available,
        batch_size=batch_size,
        fp16=fp16,
        verification_error=verification_error,
    )


def _torch_status() -> TorchRuntimeStatus:
    try:
        import torch
    except Exception:
        return TorchRuntimeStatus(cuda_available=False)
    cuda_available = bool(torch.cuda.is_available())
    gpu_names = []
    if cuda_available:
        for index in range(torch.cuda.device_count()):
            try:
                gpu_names.append(torch.cuda.get_device_name(index))
            except (AssertionError, RuntimeError):
                gpu_names.append(f"cuda:{index}")
    return TorchRuntimeStatus(
        version=str(torch.__version__),
        cuda_available=cuda_available,
        cuda_runtime=str(torch.version.cuda) if torch.version.cuda else None,
        gpu_names=gpu_names,
    )


def _is_test_storage(*paths: str) -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    temp_root = Path(tempfile.gettempdir()).resolve()
    for raw in paths:
        try:
            candidate = Path(raw).resolve()
            if candidate == temp_root or temp_root in candidate.parents:
                return True
        except (OSError, RuntimeError):
            continue
    return False


def build_runtime_status(
    snapshot: EffectiveSettingsSnapshot,
) -> SettingsRuntimeStatusResponse:
    from backend.services import embedding, reranker, sparse_retriever, vector_store

    embedding_status = _model_runtime_status(
        getter=embedding._get_model,
        model_id=settings.EMBEDDING_MODEL,
        enabled=True,
        configured_device=settings.EMBEDDING_DEVICE,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        fp16=None,
    )
    reranker_status = _model_runtime_status(
        getter=reranker._get_model,
        model_id=settings.RERANKER_MODEL,
        enabled=snapshot.retrieval.reranker_enabled,
        configured_device=settings.RERANKER_DEVICE,
        batch_size=settings.RERANKER_BATCH_SIZE,
        fp16=settings.RERANKER_USE_FP16,
        nested_model_attr="model",
    )
    storage_paths = (
        settings.CHROMA_PERSIST_DIR,
        settings.METADATA_DB_PATH,
        settings.UPLOAD_DIR,
    )
    sparse_status = sparse_retriever.get_runtime_status(
        k1=snapshot.retrieval.bm25_k1, b=snapshot.retrieval.bm25_b
    )
    from backend.services import sparse_rebuild

    rebuild = sparse_rebuild.get_rebuild_status()
    return SettingsRuntimeStatusResponse(
        revision=snapshot.revision,
        updated_at=snapshot.updated_at,
        embedding=embedding_status,
        reranker=reranker_status,
        torch=_torch_status(),
        index=IndexRuntimeStatus(
            parser_version=PARSER_VERSION,
            chunk_strategy=settings.CHUNK_STRATEGY,
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            chunk_max_tokens=settings.CHUNK_MAX_TOKENS,
            storage_profile=(
                "test" if _is_test_storage(*storage_paths) else "production"
            ),
            chroma_directory=Path(settings.CHROMA_PERSIST_DIR).name,
            metadata_database=Path(settings.METADATA_DB_PATH).name,
            upload_directory=Path(settings.UPLOAD_DIR).name,
            process_index_revision=vector_store.get_index_revision(),
            sparse_backend=sparse_status["sparse_backend"],
            sparse_index_status=sparse_status["sparse_index_status"],
            sparse_index_directory=Path(settings.SPARSE_INDEX_ROOT).name,
            sparse_index_generation=sparse_status["sparse_index_generation"],
            sparse_manifest_hash=sparse_status["sparse_manifest_hash"],
            sparse_index_load_ms=sparse_status["sparse_index_load_ms"],
            sparse_contract_version=sparse_status["sparse_contract_version"],
            sparse_rebuild_enabled=rebuild["enabled"],
            sparse_rebuild_pending=rebuild["pending"],
            sparse_rebuild_running=rebuild["running"],
            sparse_rebuild_last_outcome=(
                rebuild["last_result"].get("outcome")
            ),
        ),
    )
