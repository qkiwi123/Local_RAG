"""运行时设置的环境合并、白名单校验和不可变快照管理。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Mapping

from pydantic import ValidationError

from backend.config import settings
from backend.models.settings_schemas import (
    EffectiveSettingsSnapshot,
    LLMSettings,
    RefusalGateSettings,
    RetrievalSettings,
    RuntimeSettingsOverrides,
)
from backend.services.secret_store import SecretStore
from backend.services.settings_store import SettingsStore, StoredRuntimeSettings


DYNAMIC_FIELD_WHITELIST = frozenset(
    {
        "llm.enabled",
        "llm.provider",
        "llm.base_url",
        "llm.model",
        "llm.temperature",
        "llm.max_tokens",
        "llm.timeout_seconds",
        "retrieval.mode",
        "retrieval.final_top_k",
        "retrieval.dense_candidate_k",
        "retrieval.dense_distance_threshold",
        "retrieval.bm25_top_k",
        "retrieval.bm25_k1",
        "retrieval.bm25_b",
        "retrieval.rrf_k",
        "retrieval.reranker_enabled",
        "retrieval.reranker_candidate_k",
        "retrieval.max_chunks_per_document",
        "retrieval.refusal_gate.enabled",
    }
)


class RuntimeSettingsValidationError(ValueError):
    """设置覆盖或合并后的字段关系不合法。"""


def _contains_sensitive_override(value) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if (
                normalized
                in {
                    "api_key",
                    "apikey",
                    "password",
                    "secret",
                    "token",
                    "authorization",
                    "access_token",
                    "refresh_token",
                }
                or normalized.endswith("_api_key")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            ):
                return True
            if _contains_sensitive_override(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_override(item) for item in value)
    return False


def _environment_payload(environment) -> dict:
    final_top_k = int(environment.RETRIEVAL_TOP_K)
    reranker_enabled = bool(environment.RERANKER_ENABLED)
    reranker_candidate_k = int(environment.RERANKER_CANDIDATE_K)
    dense_candidate_k = getattr(environment, "DENSE_CANDIDATE_K", None)
    if dense_candidate_k is None:
        dense_candidate_k = (
            max(final_top_k, reranker_candidate_k)
            if reranker_enabled
            else min(100, max(final_top_k, final_top_k * 2))
        )
    max_chunks = int(
        getattr(environment, "MAX_CHUNKS_PER_DOCUMENT", min(3, final_top_k))
    )
    return {
        "llm": {
            "enabled": bool(getattr(environment, "LLM_ENABLED", True)),
            "provider": str(getattr(environment, "LLM_PROVIDER", "deepseek")),
            "base_url": str(environment.DEEPSEEK_BASE_URL),
            "model": str(environment.DEEPSEEK_MODEL),
            "temperature": float(getattr(environment, "LLM_TEMPERATURE", 0.3)),
            "max_tokens": int(getattr(environment, "LLM_MAX_TOKENS", 2000)),
            "timeout_seconds": float(
                getattr(environment, "LLM_TIMEOUT_SECONDS", 120)
            ),
        },
        "retrieval": {
            "mode": str(environment.RETRIEVAL_MODE),
            "final_top_k": final_top_k,
            "dense_candidate_k": int(dense_candidate_k),
            "dense_distance_threshold": float(
                environment.RETRIEVAL_DISTANCE_THRESHOLD
            ),
            "bm25_top_k": int(environment.BM25_TOP_K),
            "bm25_k1": float(environment.BM25_K1),
            "bm25_b": float(environment.BM25_B),
            "rrf_k": int(environment.RRF_K),
            "reranker_enabled": reranker_enabled,
            "reranker_candidate_k": reranker_candidate_k,
            "max_chunks_per_document": max_chunks,
            "refusal_gate": {"enabled": False},
        },
    }


def _merge_nested(base: dict, overrides: Mapping) -> dict:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge_nested(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _flatten_leaf_paths(value: Mapping, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            paths.update(_flatten_leaf_paths(item, path))
        else:
            paths.add(path)
    return paths


def _normalize_overrides(payload: Mapping | RuntimeSettingsOverrides) -> dict:
    if not isinstance(payload, RuntimeSettingsOverrides) and (
        not isinstance(payload, Mapping) or _contains_sensitive_override(payload)
    ):
        raise RuntimeSettingsValidationError(
            "运行时设置覆盖必须是非敏感字段对象"
        )
    try:
        model = (
            payload
            if isinstance(payload, RuntimeSettingsOverrides)
            else RuntimeSettingsOverrides.model_validate(payload)
        )
    except (TypeError, ValidationError, ValueError):
        raise RuntimeSettingsValidationError("运行时设置覆盖校验失败") from None
    normalized = model.to_payload()
    unknown = _flatten_leaf_paths(normalized) - DYNAMIC_FIELD_WHITELIST
    if unknown:
        raise RuntimeSettingsValidationError(
            "包含非动态白名单字段: " + ", ".join(sorted(unknown))
        )
    return normalized


def _validate_effective(payload: Mapping) -> tuple[LLMSettings, RetrievalSettings]:
    try:
        llm = LLMSettings.model_validate(payload["llm"])
        retrieval_payload = deepcopy(payload["retrieval"])
        gate_payload = retrieval_payload.get("refusal_gate", {})
        retrieval_payload["refusal_gate"] = RefusalGateSettings.model_validate(
            {
                "available": False,
                "enabled": bool(gate_payload.get("enabled", False)),
                "reason": "pending_v3_5_calibration",
            }
        )
        retrieval = RetrievalSettings.model_validate(retrieval_payload)
    except (KeyError, TypeError, ValidationError, ValueError):
        raise RuntimeSettingsValidationError("合并后的有效设置校验失败") from None
    return llm, retrieval


class RuntimeSettingsManager:
    """以 SQLite revision 为真源，原子发布进程内有效快照。"""

    def __init__(
        self,
        *,
        db_path: str | None = None,
        environment=None,
        store: SettingsStore | None = None,
        secret_store: SecretStore | None = None,
    ):
        self._environment = environment or settings
        effective_db_path = str(db_path or self._environment.METADATA_DB_PATH)
        self._store = store or SettingsStore(effective_db_path)
        self._secret_store = secret_store or SecretStore(
            Path(effective_db_path).with_name("runtime_secrets.bin")
        )
        self._lock = RLock()
        self._snapshot = self._build_snapshot(self._store.load())

    def _build_snapshot(
        self,
        stored: StoredRuntimeSettings,
    ) -> EffectiveSettingsSnapshot:
        environment_payload = _environment_payload(self._environment)
        overrides = _normalize_overrides(stored.payload)
        effective = _merge_nested(environment_payload, overrides)
        llm, retrieval = _validate_effective(effective)

        runtime_key = self._secret_store.get_secret("llm_api_key")
        environment_key = str(
            getattr(self._environment, "DEEPSEEK_API_KEY", "")
        ).strip()
        api_key = runtime_key or environment_key or None
        api_key_source = (
            "runtime_secret"
            if runtime_key
            else "environment"
            if environment_key
            else "none"
        )
        if llm.enabled and api_key is None:
            raise RuntimeSettingsValidationError(
                "启用生成模型时必须存在有效 API Key"
            )
        field_sources = {
            path: "environment"
            for path in _flatten_leaf_paths(environment_payload)
        }
        field_sources.update(
            {path: "runtime" for path in _flatten_leaf_paths(overrides)}
        )
        field_sources.update(
            {
                "llm.api_key": (
                    "runtime"
                    if api_key_source == "runtime_secret"
                    else "environment"
                    if api_key_source == "environment"
                    else "system"
                ),
                "retrieval.refusal_gate.available": "system",
                "retrieval.refusal_gate.reason": "system",
            }
        )
        return EffectiveSettingsSnapshot(
            revision=stored.revision,
            updated_at=stored.updated_at,
            llm=llm,
            retrieval=retrieval,
            api_key=api_key,
            api_key_source=api_key_source,
            field_sources=field_sources,
        )

    def snapshot(self) -> EffectiveSettingsSnapshot:
        with self._lock:
            return self._snapshot

    def reload(self) -> EffectiveSettingsSnapshot:
        candidate = self._build_snapshot(self._store.load())
        with self._lock:
            self._snapshot = candidate
            return self._snapshot

    def update(
        self,
        patch: Mapping | RuntimeSettingsOverrides,
        *,
        expected_revision: int,
    ) -> EffectiveSettingsSnapshot:
        normalized_patch = _normalize_overrides(patch)
        with self._lock:
            current = self._store.load()
            current_overrides = _normalize_overrides(current.payload)
            merged_overrides = _merge_nested(current_overrides, normalized_patch)
            # 写入前验证完整有效配置；失败时 SQLite 与当前快照都不变。
            prepared = self._build_snapshot(
                StoredRuntimeSettings(
                    revision=current.revision + 1,
                    payload=merged_overrides,
                    updated_at=current.updated_at,
                )
            )
            saved = self._store.save(
                merged_overrides,
                expected_revision=expected_revision,
                changed_keys=sorted(_flatten_leaf_paths(normalized_patch)),
            )
            candidate = prepared.model_copy(
                update={
                    "revision": saved.revision,
                    "updated_at": saved.updated_at,
                }
            )
            self._snapshot = candidate
            return candidate
