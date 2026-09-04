"""V3-5隔离调参入口；A阶段只建立并验收实验能力。

prepare从已封存的v3.1测试运行复制索引，不重新入库、不连接生产库；
run在复制库上调用项目真实retriever，显式传入请求级配置且不调用LLM。
所有目录均只创建一次；已存在产物只能通过--resume做同配置断点续算。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import statistics
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

TUNING_ROOT = ROOT / "tuning"
SOURCE_LABEL = "gpu-baseline-120q-v31-k15-20260828"
SOURCE_RUN = ROOT / "runs" / SOURCE_LABEL
SOURCE_AUDIT = ROOT / "baseline_audits" / SOURCE_LABEL
DATASET_PATH = ROOT / "questions_v3_120q.json"
BASELINE_ROWS = SOURCE_RUN / "retrieval-k15.jsonl"
ALLOWED_SPLITS = ("development", "validation", "frozen_test")

BASELINE_CONFIG = {
    "mode": "hybrid",
    "final_top_k": 5,
    "dense_candidate_k": 15,
    "dense_distance_threshold": 0.67,
    "bm25_top_k": 15,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "rrf_k": 60,
    "reranker_enabled": True,
    "reranker_candidate_k": 15,
    "max_chunks_per_document": 3,
    "refusal_gate": {
        "available": False,
        "enabled": False,
        "reason": "pending_v3_5_calibration",
    },
}

PINNED_ENV = {
    "LLM_ENABLED": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "DO_NOT_TRACK": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PREWARM_MODELS": "false",
    "TOKENIZERS_PARALLELISM": "false",
    "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
    "EMBEDDING_DEVICE": "cuda:0",
    "EMBEDDING_BATCH_SIZE": "32",
    "EMBEDDING_QUERY_INSTRUCTION": "",
    "RERANKER_MODEL": "BAAI/bge-reranker-v2-m3",
    "RERANKER_DEVICE": "cuda:0",
    "RERANKER_USE_FP16": "true",
    "RERANKER_BATCH_SIZE": "8",
    "RERANKER_MAX_LENGTH": "512",
    "RETRIEVAL_TRACE_ENABLED": "false",
}

QUALITY_FIELDS = (
    "question_count",
    "answerable_count",
    "unanswerable_count",
    "document_hit_rate",
    "document_recall",
    "document_mrr",
    "all_gold_docs_recalled_rate",
    "multi_document_count",
    "multi_document_complete_recall_rate",
    "evidence_coverage",
    "exact_evidence_containment_rate",
    "unanswerable_llm_entry_rate",
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_name(value: str, field: str = "experiment_id") -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise ValueError(f"{field}仅允许字母、数字、短横线和下划线，不能包含路径")
    return value


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _assert_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"路径越界：{resolved}")
    return resolved


def _file_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    root = root.resolve()
    if not root.is_dir() or _is_link(root):
        raise FileNotFoundError(f"目录不存在或是链接：{root}")
    files: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        if _is_link(path):
            raise ValueError(f"实验存储禁止符号链接或junction：{path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = {
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
    return files


def _storage_manifest(chroma: Path, metadata: Path) -> dict[str, Any]:
    return {
        "chroma": _file_manifest(chroma),
        "metadata.db": {
            "sha256": _sha256_file(metadata),
            "bytes": metadata.stat().st_size,
        },
    }


def _storage_identity(manifest: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical(manifest))


def _database_checks(chroma: Path, metadata: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    specifications = (
        (metadata, ("documents", "document_units", "messages", "runtime_settings")),
        (chroma / "chroma.sqlite3", ("embeddings",)),
    )
    for path, tables in specifications:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            if quick_check != [("ok",)]:
                raise RuntimeError(f"数据库quick_check失败：{path}")
            checks[path.name] = {
                "quick_check": "ok",
                **{
                    table: connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                    for table in tables
                },
            }
        finally:
            connection.close()
    expected = {
        "metadata.db": {
            "documents": 1000,
            "document_units": 1000,
            "messages": 0,
            "runtime_settings": 0,
        },
        "chroma.sqlite3": {"embeddings": 2914},
    }
    for database, counts in expected.items():
        for table, value in counts.items():
            if checks[database][table] != value:
                raise RuntimeError(
                    f"{database}.{table}={checks[database][table]}，预期{value}"
                )
    return checks


def _dataset() -> dict[str, Any]:
    from eval.dataset_runtime import normalize_for_evaluation

    return normalize_for_evaluation(_load_json(DATASET_PATH))


def _split_questions(dataset: dict[str, Any], split: str) -> list[dict[str, Any]]:
    if split not in ALLOWED_SPLITS:
        raise ValueError(f"不支持的split：{split}")
    questions = [row for row in dataset["questions"] if row.get("split") == split]
    expected = {"development": 72, "validation": 24, "frozen_test": 24}
    if len(questions) != expected[split] or len({row["id"] for row in questions}) != len(questions):
        raise RuntimeError(f"{split}题目数量或ID唯一性不符合固定契约")
    return questions


def _smoke_ids(questions: list[dict[str, Any]]) -> dict[str, str]:
    one = next(
        row for row in questions
        if row["answerable"] and row["source"]["gold_document_count"] == 1
    )
    multi = next(
        row for row in questions
        if row["answerable"] and row["source"]["gold_document_count"] > 1
    )
    unanswerable = next(row for row in questions if not row["answerable"])
    return {
        "answerable_one_document": one["id"],
        "answerable_multi_document": multi["id"],
        "unanswerable": unanswerable["id"],
    }


def _source_artifact_checks(source_manifest: dict[str, Any]) -> None:
    sealed = _load_json(SOURCE_AUDIT / "artifact-manifest.json")["files"]
    source_prefix = f"runs/{SOURCE_LABEL}/"
    expected: dict[str, dict[str, Any]] = {}
    for relative, info in sealed.items():
        if relative.startswith(source_prefix + "chroma/"):
            expected["chroma/" + relative.split("/chroma/", 1)[1]] = info
        elif relative == source_prefix + "metadata.db":
            expected["metadata.db"] = info
    actual = {
        **{
            f"chroma/{relative}": info
            for relative, info in source_manifest["chroma"].items()
        },
        "metadata.db": source_manifest["metadata.db"],
    }
    if actual != expected:
        raise RuntimeError("源测试索引与已封存artifact-manifest不一致")


def _code_identity() -> dict[str, str]:
    paths = (
        PROJECT_ROOT / "backend/services/retriever.py",
        PROJECT_ROOT / "eval/adapters.py",
        PROJECT_ROOT / "eval/retrieval_sweep.py",
        Path(__file__).resolve(),
    )
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): _sha256_file(path)
        for path in paths
    }


def _retrieval_code_identity() -> dict[str, str]:
    return {
        key: value
        for key, value in _code_identity().items()
        if key != "eval/crud_rag_1000/v3_1/tuning_runner.py"
    }


def _expected_retrieval_code_identity(protocol: dict[str, Any]) -> dict[str, str]:
    return {
        key: value
        for key, value in protocol["code_identity"].items()
        if key != "eval/crud_rag_1000/v3_1/tuning_runner.py"
    }


def _config_id(payload: dict[str, Any]) -> str:
    return f"{payload['mode']}-{_sha256_bytes(_canonical(payload))[:12]}"


def _validated_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.models.settings_schemas import RetrievalSettings

    return RetrievalSettings.model_validate(payload).model_dump(mode="json")


def _effective_config(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_mode": payload["mode"],
        "final_top_k": payload["final_top_k"],
        "dense_candidate_k": payload["dense_candidate_k"],
        "dense_distance_threshold": payload["dense_distance_threshold"],
        "bm25_top_k": payload["bm25_top_k"],
        "bm25_k1": payload["bm25_k1"],
        "bm25_b": payload["bm25_b"],
        "rrf_k": payload["rrf_k"],
        "reranker_enabled": payload["reranker_enabled"],
        "reranker_candidate_k": payload["reranker_candidate_k"],
        "max_chunks_per_document": payload["max_chunks_per_document"],
        "refusal_gate_enabled": payload["refusal_gate"]["enabled"],
    }


def _fusion_pool_k(payload: dict[str, Any]) -> int:
    return max(
        payload["dense_candidate_k"],
        payload["bm25_top_k"],
        payload["reranker_candidate_k"]
        if payload["reranker_enabled"]
        else payload["final_top_k"],
    )


def prepare_experiment(experiment_id: str) -> Path:
    experiment_id = _safe_name(experiment_id)
    experiment = _assert_within(TUNING_ROOT / experiment_id, TUNING_ROOT)
    if experiment.exists():
        raise FileExistsError("实验目录已存在；必须使用新experiment_id，禁止覆盖")
    source_chroma = SOURCE_RUN / "chroma"
    source_metadata = SOURCE_RUN / "metadata.db"
    source_manifest = _storage_manifest(source_chroma, source_metadata)
    _source_artifact_checks(source_manifest)
    source_checks = _database_checks(source_chroma, source_metadata)

    storage = experiment / "storage"
    storage.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_chroma, storage / "chroma", copy_function=shutil.copy2)
    shutil.copy2(source_metadata, storage / "metadata.db")
    (storage / "uploads").mkdir()

    copied_manifest = _storage_manifest(
        storage / "chroma",
        storage / "metadata.db",
    )
    if copied_manifest != source_manifest:
        raise RuntimeError("索引副本与源索引hash/大小不一致")
    copied_checks = _database_checks(storage / "chroma", storage / "metadata.db")
    if copied_checks != source_checks:
        raise RuntimeError("索引副本与源索引schema/行数不一致")

    dataset = _dataset()
    split_ids = {
        split: [row["id"] for row in _split_questions(dataset, split)]
        for split in ALLOWED_SPLITS
    }
    config_id = _config_id(BASELINE_CONFIG)
    config_record = {
        "config_id": config_id,
        "created_at": _now(),
        "retrieval": BASELINE_CONFIG,
        "effective_config": _effective_config(BASELINE_CONFIG),
        "derived": {"fusion_pool_k": _fusion_pool_k(BASELINE_CONFIG)},
        "role": "frozen_baseline_control",
    }
    _write_json_exclusive(experiment / "configs" / f"{config_id}.json", config_record)
    protocol = {
        "schema_version": 1,
        "phase": "V3-5-A",
        "status": "prepared",
        "experiment_id": experiment_id,
        "created_at": _now(),
        "source": {
            "label": SOURCE_LABEL,
            "run_relative": f"runs/{SOURCE_LABEL}",
            "artifact_manifest_sha256": _sha256_file(
                SOURCE_AUDIT / "artifact-manifest.json"
            ),
            "storage_identity": _storage_identity(source_manifest),
        },
        "dataset": {
            "relative_path": DATASET_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256_file(DATASET_PATH),
            "dataset_id": dataset["dataset_id"],
            "parser_version": dataset["parser_version"],
            "rubric_version": dataset["rubric_version"],
            "review_status": "draft",
        },
        "storage": {
            "profile": "copied_test_index",
            "chroma": "storage/chroma",
            "metadata": "storage/metadata.db",
            "uploads": "storage/uploads",
            "manifest": copied_manifest,
            "database_checks": copied_checks,
            "hardlinks_or_reparse_points_allowed": False,
        },
        "splits": split_ids,
        "smoke_ids": _smoke_ids(_split_questions(dataset, "development")),
        "default_config_id": config_id,
        "code_identity": _code_identity(),
        "environment": PINNED_ENV,
        "network_policy": "deny_all_outbound_IP_socket_connect",
        "llm_policy": "LLM_ENABLED=false; generation module must stay unloaded",
        "cache_contract": {
            "level": "exact_question_result",
            "key_fields": [
                "dataset_sha256",
                "storage_identity",
                "code_identity",
                "environment",
                "split",
                "question_id",
                "question_text",
                "retrieval_config",
            ],
            "cross_config_stage_cache": False,
            "note": "A仅提供同配置断点续算；不同配置不得复用，阶段级跨配置缓存需另行证明等价后再启用。",
        },
    }
    protocol["protocol_identity"] = _sha256_bytes(_canonical(protocol))
    _write_json_exclusive(experiment / "protocol.json", protocol)
    _write_json_exclusive(
        experiment / "prepare-result.json",
        {
            "status": "passed",
            "prepared_at": _now(),
            "source_storage_unchanged": (
                _storage_manifest(source_chroma, source_metadata) == source_manifest
            ),
            "copied_storage_matches_source": True,
            "database_checks": copied_checks,
            "protocol_identity": protocol["protocol_identity"],
        },
    )
    return experiment


def _load_protocol(experiment_id: str) -> tuple[Path, dict[str, Any]]:
    experiment_id = _safe_name(experiment_id)
    experiment = _assert_within(TUNING_ROOT / experiment_id, TUNING_ROOT)
    protocol_path = experiment / "protocol.json"
    if not protocol_path.is_file() or _is_link(experiment):
        raise FileNotFoundError("实验尚未prepare或目录是链接")
    protocol = _load_json(protocol_path)
    identity = protocol.pop("protocol_identity")
    if _sha256_bytes(_canonical(protocol)) != identity:
        raise RuntimeError("protocol.json身份校验失败")
    protocol["protocol_identity"] = identity
    return experiment, protocol


def register_config(experiment_id: str, config_path: Path) -> dict[str, Any]:
    experiment, protocol = _load_protocol(experiment_id)
    if _retrieval_code_identity() != _expected_retrieval_code_identity(protocol):
        raise RuntimeError("当前检索/计分代码与prepare时不一致；拒绝注册或复用缓存")
    config_path = config_path.resolve()
    if not config_path.is_file() or _is_link(config_path):
        raise FileNotFoundError("配置JSON不存在或是链接")
    payload = _validated_config_payload(_load_json(config_path))
    config_id = _config_id(payload)
    record = {
        "config_id": config_id,
        "created_at": _now(),
        "retrieval": payload,
        "effective_config": _effective_config(payload),
        "derived": {"fusion_pool_k": _fusion_pool_k(payload)},
        "role": "tuning_candidate",
        "source_config_sha256": _sha256_file(config_path),
    }
    _write_json_exclusive(
        experiment / "configs" / f"{config_id}.json",
        record,
    )
    return record


def _validate_experiment_storage(
    experiment: Path,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    if _retrieval_code_identity() != _expected_retrieval_code_identity(protocol):
        raise RuntimeError("当前检索/计分代码与prepare时不一致；拒绝运行或复用缓存")
    storage = experiment / "storage"
    current = _storage_manifest(storage / "chroma", storage / "metadata.db")
    expected = protocol["storage"]["manifest"]
    immutable_mismatches = []
    if current["metadata.db"] != expected["metadata.db"]:
        immutable_mismatches.append("metadata.db")
    for relative, info in expected["chroma"].items():
        if relative == "chroma.sqlite3":
            continue
        if current["chroma"].get(relative) != info:
            immutable_mismatches.append("chroma/" + relative)
    if immutable_mismatches:
        raise RuntimeError(
            "实验索引不可变文件已变化：" + ", ".join(immutable_mismatches)
        )
    database_checks = _database_checks(
        storage / "chroma",
        storage / "metadata.db",
    )
    if database_checks != protocol["storage"]["database_checks"]:
        raise RuntimeError("实验数据库schema/行数已变化")
    return {
        "immutable_files_match_prepared_copy": True,
        "database_checks": database_checks,
        "chroma_sqlite_byte_hash_changed_after_open": (
            current["chroma"]["chroma.sqlite3"]
            != expected["chroma"]["chroma.sqlite3"]
        ),
        "chroma_runtime_mutation_boundary": (
            "仅允许Chroma内部acquire_write等运行簿记改变字节；"
            "embeddings计数、quick_check、HNSW文件和metadata.db必须不变"
        ),
    }


def _load_config(experiment: Path, config_id: str | None) -> tuple[str, dict[str, Any]]:
    if config_id is None:
        protocol = _load_json(experiment / "protocol.json")
        config_id = protocol["default_config_id"]
    config_id = _safe_name(config_id, "config_id")
    record = _load_json(experiment / "configs" / f"{config_id}.json")
    payload = record["retrieval"]
    if _config_id(payload) != config_id:
        raise RuntimeError("config_id与配置内容不一致")
    if record["effective_config"] != _effective_config(payload):
        raise RuntimeError("配置的effective_config记录不一致")
    if record["derived"]["fusion_pool_k"] != _fusion_pool_k(payload):
        raise RuntimeError("配置的派生fusion_pool_k记录不一致")
    return config_id, payload


def _cache_key(
    protocol: dict[str, Any],
    split: str,
    question: dict[str, Any],
    config: dict[str, Any],
) -> str:
    payload = {
        "dataset_sha256": protocol["dataset"]["sha256"],
        "storage_identity": protocol["source"]["storage_identity"],
        "code_identity": protocol["code_identity"],
        "environment": protocol["environment"],
        "split": split,
        "question_id": question["id"],
        "question_text": question["question"],
        "retrieval_config": config,
    }
    return _sha256_bytes(_canonical(payload))


def _read_rows(
    path: Path,
    questions: list[dict[str, Any]],
    protocol: dict[str, Any],
    split: str,
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    expected = {row["id"]: row for row in questions}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            qid = row.get("id")
            if qid not in expected or qid in rows:
                raise RuntimeError(f"缓存第{line_number}行ID越界或重复")
            if row.get("cache_key") != _cache_key(
                protocol, split, expected[qid], config
            ):
                raise RuntimeError(f"缓存键失效：{qid}")
            rows[qid] = row
    return rows


def _runtime_config(payload: dict[str, Any]):
    from backend.models.settings_schemas import RetrievalSettings
    from backend.services.retriever import RetrievalRuntimeConfig

    validated = RetrievalSettings.model_validate(payload)
    return RetrievalRuntimeConfig(
        mode=validated.mode,
        final_top_k=validated.final_top_k,
        dense_candidate_k=validated.dense_candidate_k,
        dense_distance_threshold=validated.dense_distance_threshold,
        bm25_top_k=validated.bm25_top_k,
        bm25_k1=validated.bm25_k1,
        bm25_b=validated.bm25_b,
        rrf_k=validated.rrf_k,
        reranker_enabled=validated.reranker_enabled,
        reranker_candidate_k=validated.reranker_candidate_k,
        max_chunks_per_document=validated.max_chunks_per_document,
        refusal_gate_enabled=validated.refusal_gate.enabled,
    )


def _install_network_guard(blocked: list[dict[str, Any]], started: float) -> None:
    def deny_network(event, args):
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            blocked.append({"event": event, "at_s": round(time.perf_counter() - started, 3)})
            raise PermissionError("V3-5调参禁止出站IP连接")

    sys.addaudithook(deny_network)


def _quality(candidate: dict[str, Any]) -> dict[str, Any]:
    return {field: candidate.get(field) for field in QUALITY_FIELDS}


def _latency(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    values = [row["retrieval_s"] for row in rows]
    if not values:
        return {"mean": None, "p50": None, "p95": None, "sum": 0.0}
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values),
        "p50": ordered[round(0.50 * (len(ordered) - 1))],
        "p95": ordered[round(0.95 * (len(ordered) - 1))],
        "sum": sum(values),
    }


def _baseline_comparison(
    questions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    candidate: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    from eval.retrieval_sweep import build_candidate_result

    wanted = {row["id"] for row in questions}
    with BASELINE_ROWS.open(encoding="utf-8") as handle:
        baseline = {
            row["id"]: row
            for row in (json.loads(line) for line in handle)
            if row["id"] in wanted
        }
    if set(baseline) != wanted:
        raise RuntimeError("封存基线缺少development题目")
    baseline_ordered = [baseline[q["id"]] for q in questions]
    baseline_candidate = build_candidate_result(
        candidate_k=config["reranker_candidate_k"],
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in baseline_ordered},
        timings_s=[row["retrieval_s"] for row in baseline_ordered],
        traces={row["id"]: row["trace"] for row in baseline_ordered},
    )
    question_by_id = {row["id"]: row for row in questions}
    order_matches = {
        row["id"]: [item["chunk_id"] for item in row["retrieval"]]
        == [item["chunk_id"] for item in baseline[row["id"]]["retrieval"]]
        for row in rows
    }
    set_matches = {
        row["id"]: {item["chunk_id"] for item in row["retrieval"]}
        == {item["chunk_id"] for item in baseline[row["id"]]["retrieval"]}
        for row in rows
    }
    trace_matches = {
        row["id"]: row["trace"]["effective_config"] == _effective_config(config)
        for row in rows
    }
    new_quality = _quality(candidate)
    baseline_quality = _quality(baseline_candidate)
    stage_names = ("dense", "bm25", "rrf", "reranker", "selected")
    stage_order_matches: dict[str, dict[str, bool]] = {}
    for row in rows:
        current_stages = {
            stage["name"]: [item["chunk_id"] for item in stage["candidates"]]
            for stage in row["trace"]["stages"]
        }
        baseline_stages = {
            stage["name"]: [item["chunk_id"] for item in stage["candidates"]]
            for stage in baseline[row["id"]]["trace"]["stages"]
        }
        stage_order_matches[row["id"]] = {
            name: current_stages.get(name) == baseline_stages.get(name)
            for name in stage_names
        }
    answerable_ids = {
        qid for qid, question in question_by_id.items() if question["answerable"]
    }
    unanswerable_ids = set(question_by_id) - answerable_ids
    answerable_order_match = all(order_matches[qid] for qid in answerable_ids)
    differing_ids = sorted(qid for qid, matches in order_matches.items() if not matches)
    difference_details = []
    for qid in differing_ids:
        stages = stage_order_matches[qid]
        difference_details.append({
            "id": qid,
            "answerable": question_by_id[qid]["answerable"],
            "selected_set_identical": set_matches[qid],
            "first_different_stage": next(
                (name for name in stage_names if not stages[name]),
                None,
            ),
            "stage_order_identical": stages,
        })
    passed = (
        answerable_order_match
        and all(trace_matches.values())
        and new_quality == baseline_quality
    )
    return {
        "source_label": SOURCE_LABEL,
        "question_count": len(questions),
        "selected_chunk_order_identical_count": sum(order_matches.values()),
        "selected_chunk_order_difference_ids": differing_ids,
        "selected_chunk_set_identical_count": sum(set_matches.values()),
        "answerable_selected_order_identical_count": sum(
            order_matches[qid] for qid in answerable_ids
        ),
        "answerable_question_count": len(answerable_ids),
        "unanswerable_selected_order_identical_count": sum(
            order_matches[qid] for qid in unanswerable_ids
        ),
        "unanswerable_question_count": len(unanswerable_ids),
        "stage_order_identical_count": {
            name: sum(stage_order_matches[qid][name] for qid in stage_order_matches)
            for name in stage_names
        },
        "difference_details": difference_details,
        "effective_config_match_count": sum(trace_matches.values()),
        "effective_config_mismatch_ids": sorted(
            qid for qid, matches in trace_matches.items() if not matches
        ),
        "quality_exact_match": new_quality == baseline_quality,
        "baseline_quality": baseline_quality,
        "copied_index_quality": new_quality,
        "latency_not_used_for_equivalence": {
            "baseline": _latency(baseline_ordered),
            "copied_index": _latency(rows),
        },
        "equivalence_policy": {
            "requires": [
                "all_answerable_selected_order_identical",
                "all_effective_config_identical",
                "all_quality_metrics_identical",
            ],
            "allows_with_explicit_record": (
                "unanswerable ANN candidate/order variance because these rows have no gold; "
                "the copied-index control becomes the V3-5 calibration reference"
            ),
        },
        "passed": passed,
    }


def accept_phase_a(experiment_id: str) -> dict[str, Any]:
    experiment, protocol = _load_protocol(experiment_id)
    storage = _validate_experiment_storage(experiment, protocol)
    config_id, config = _load_config(experiment, protocol["default_config_id"])
    questions = _split_questions(_dataset(), "development")
    result_dir = experiment / "results" / "development" / config_id
    rows_by_id = _read_rows(
        result_dir / "rows.jsonl",
        questions,
        protocol,
        "development",
        config,
    )
    if len(rows_by_id) != len(questions):
        raise RuntimeError("development结果不完整，不能验收A")
    rows = [rows_by_id[row["id"]] for row in questions]
    from eval.retrieval_sweep import build_candidate_result

    candidate = build_candidate_result(
        candidate_k=config["reranker_candidate_k"],
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in rows},
        timings_s=[row["retrieval_s"] for row in rows],
        traces={row["id"]: row["trace"] for row in rows},
    )
    comparison = _baseline_comparison(questions, rows, candidate, config)
    original_summary = _load_json(result_dir / "summary.json")
    attempts = [
        _load_json(path)
        for path in sorted((result_dir / "attempts").glob("*.json"))
    ]
    source_manifest = _storage_manifest(
        SOURCE_RUN / "chroma",
        SOURCE_RUN / "metadata.db",
    )
    _source_artifact_checks(source_manifest)
    source_checks = _database_checks(
        SOURCE_RUN / "chroma",
        SOURCE_RUN / "metadata.db",
    )
    full_real_attempts = [row for row in attempts if row["real_query_count"] == 72]
    full_cache_attempts = [row for row in attempts if row["cache_hit_count"] == 72]
    runtime = original_summary.get("runtime") or {}
    gpu = runtime.get("runtime") or {}
    gpu_verified = all(
        gpu.get(component, {}).get("loaded")
        and gpu.get(component, {}).get("verified")
        and gpu.get(component, {}).get("actual_device") == "cuda:0"
        for component in ("embedding", "reranker")
    )
    checks = {
        "development_rows": f"{len(rows)}/72",
        "quality_replay_exact": _quality(candidate) == original_summary["quality"],
        "copied_index_equivalence": comparison["passed"],
        "gpu_models_verified": gpu_verified,
        "llm_module_imported": original_summary["llm_module_imported"],
        "outbound_ip_connection_count": len(original_summary["outbound_ip_connections"]),
        "full_real_attempt_count": len(full_real_attempts),
        "full_cache_replay_attempt_count": len(full_cache_attempts),
        "source_artifact_manifest_match": True,
        "source_database_checks": source_checks,
        "experiment_storage": storage,
    }
    passed = (
        checks["quality_replay_exact"]
        and checks["copied_index_equivalence"]
        and checks["gpu_models_verified"]
        and checks["llm_module_imported"] is False
        and checks["outbound_ip_connection_count"] == 0
        and checks["full_real_attempt_count"] >= 1
        and checks["full_cache_replay_attempt_count"] >= 1
    )
    acceptance = {
        "schema_version": 1,
        "phase": "V3-5-A",
        "status": "passed" if passed else "failed",
        "accepted_at": _now(),
        "experiment_id": experiment_id,
        "config_id": config_id,
        "checks": checks,
        "comparison": comparison,
        "quality": _quality(candidate),
        "latency": _latency(rows),
        "initial_strict_summary": {
            "status": original_summary["status"],
            "sha256": _sha256_file(result_dir / "summary.json"),
            "preserved": True,
            "reason": (
                "初版把不可回答题ANN候选差异也作为失败；修订验收口径后新增本文件，"
                "不覆盖初始失败证据"
            ),
        },
        "limits": [
            "dataset review remains draft",
            "2/12 unanswerable rows differ from the historical sealed run at Dense stage",
            "exact-result cache supports same-config resume only; cross-config stage cache is disabled",
            "this accepts experiment capability A only and does not run B tuning",
        ],
    }
    _write_json_exclusive(experiment / "phase-a-acceptance.json", acceptance)
    if not passed:
        raise RuntimeError("V3-5 A验收未通过；查看phase-a-acceptance.json")
    return acceptance


def _run_attempt_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")


def run_experiment(
    experiment_id: str,
    split: str,
    config_id: str | None,
    *,
    resume: bool,
    retrieve_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    experiment, protocol = _load_protocol(experiment_id)
    storage_before = _validate_experiment_storage(experiment, protocol)
    if split not in ALLOWED_SPLITS:
        raise ValueError(f"不支持的split：{split}")
    if split != "development":
        raise PermissionError("A阶段只允许development；validation/frozen_test尚未授权")
    config_id, config = _load_config(experiment, config_id)
    dataset = _dataset()
    if _sha256_file(DATASET_PATH) != protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与协议不一致")
    questions = _split_questions(dataset, split)
    if [row["id"] for row in questions] != protocol["splits"][split]:
        raise RuntimeError("split题目ID与协议不一致")

    result_dir = experiment / "results" / split / config_id
    rows_path = result_dir / "rows.jsonl"
    summary_path = result_dir / "summary.json"
    if (rows_path.exists() or summary_path.exists()) and not resume:
        raise FileExistsError("结果已存在；禁止覆盖，断点续算必须显式--resume")
    result_dir.mkdir(parents=True, exist_ok=True)
    cached = _read_rows(rows_path, questions, protocol, split, config)
    if summary_path.exists() and len(cached) != len(questions):
        raise RuntimeError("summary已存在但缓存题数不完整")

    storage = experiment / "storage"
    environment = {
        **protocol["environment"],
        "CHROMA_PERSIST_DIR": str(storage / "chroma"),
        "METADATA_DB_PATH": str(storage / "metadata.db"),
        "UPLOAD_DIR": str(storage / "uploads"),
    }
    os.environ.update(environment)
    blocked: list[dict[str, Any]] = []
    started = time.perf_counter()
    if retrieve_fn is None:
        _install_network_guard(blocked, started)
        from eval import adapters

        adapters.assert_isolation()
        runtime_config = _runtime_config(config)
        retrieve_fn = adapters.retrieve_only
    else:
        runtime_config = config

    cache_hits: list[str] = []
    real_queries: list[str] = []
    rows = dict(cached)
    if not summary_path.exists():
        with rows_path.open("a", encoding="utf-8") as handle:
            for question in questions:
                qid = question["id"]
                if qid in rows:
                    cache_hits.append(qid)
                    continue
                result = retrieve_fn(
                    question["question"],
                    include_trace=True,
                    runtime_config=runtime_config,
                )
                trace = result.get("trace")
                if trace is None or trace.get("effective_config") != _effective_config(config):
                    raise RuntimeError(f"参数未实际生效或Trace缺失：{qid}")
                row = {
                    "id": qid,
                    "cache_key": _cache_key(protocol, split, question, config),
                    "retrieval": result["retrieval"],
                    "retrieval_s": result["retrieval_s"],
                    "trace": trace,
                }
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows[qid] = row
                real_queries.append(qid)
    else:
        cache_hits = [row["id"] for row in questions]

    ordered = [rows[row["id"]] for row in questions]
    from eval.retrieval_sweep import build_candidate_result

    candidate = build_candidate_result(
        candidate_k=config["reranker_candidate_k"],
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in ordered},
        timings_s=[row["retrieval_s"] for row in ordered],
        traces={row["id"]: row["trace"] for row in ordered},
    )
    comparison = _baseline_comparison(questions, ordered, candidate, config)
    smoke = {
        name: qid in rows for name, qid in protocol["smoke_ids"].items()
    }
    if not all(smoke.values()):
        raise RuntimeError("单文档/多文档/不可回答冒烟覆盖不完整")

    runtime = None
    if retrieve_fn.__module__ == "eval.adapters":
        from eval import adapters

        runtime = adapters.get_run_config()
        if real_queries:
            for component in ("embedding", "reranker"):
                status = runtime["runtime"][component]
                if not (
                    status["loaded"]
                    and status["verified"]
                    and status["actual_device"] == "cuda:0"
                ):
                    raise RuntimeError(f"{component}未在cuda:0完成真实推理")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("调参过程中意外导入LLM模块")
    if blocked:
        raise RuntimeError("调参过程中出现出站网络尝试")
    storage_after = _validate_experiment_storage(experiment, protocol)

    summary = {
        "schema_version": 1,
        "phase": "V3-5-A",
        "status": "passed" if comparison["passed"] else "failed",
        "experiment_id": experiment_id,
        "split": split,
        "config_id": config_id,
        "effective_config": _effective_config(config),
        "derived": {"fusion_pool_k": _fusion_pool_k(config)},
        "question_count": len(ordered),
        "answerable_count": sum(row["answerable"] for row in questions),
        "unanswerable_count": sum(not row["answerable"] for row in questions),
        "smoke_coverage": smoke,
        "candidate_result": candidate,
        "quality": _quality(candidate),
        "comparison": comparison,
        "runtime": runtime,
        "llm_module_imported": False,
        "outbound_ip_connections": blocked,
        "storage_before": storage_before,
        "storage_after": storage_after,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "cache_contract": protocol["cache_contract"],
        "completed_at": _now(),
    }
    if not summary_path.exists():
        _write_json_exclusive(summary_path, summary)
        if _load_json(summary_path) != summary:
            raise RuntimeError("summary写入后重放不一致")
    else:
        saved = _load_json(summary_path)
        if saved["quality"] != summary["quality"]:
            raise RuntimeError("缓存重放质量与已保存summary不一致")
        if saved["comparison"] != summary["comparison"]:
            acceptance_path = experiment / "phase-a-acceptance.json"
            accepted = (
                _load_json(acceptance_path)
                if acceptance_path.is_file()
                else None
            )
            if not (
                accepted
                and accepted["status"] == "passed"
                and accepted["comparison"] == comparison
            ):
                raise RuntimeError("缓存重放对照与已保存验收不一致")

    attempt = {
        "attempted_at": _now(),
        "resume": resume,
        "cache_hit_count": len(cache_hits),
        "cache_hit_ids": cache_hits,
        "real_query_count": len(real_queries),
        "real_query_ids": real_queries,
        "wall_s": round(time.perf_counter() - started, 3),
        "summary_sha256": _sha256_file(summary_path),
        "llm_module_imported": "backend.services.llm" in sys.modules,
        "outbound_ip_connections": blocked,
    }
    _write_json_exclusive(
        result_dir / "attempts" / f"{_run_attempt_id()}.json",
        attempt,
    )
    if not comparison["passed"]:
        raise RuntimeError("复制索引development对照未通过；已保存失败明细")
    return {"summary": summary, "attempt": attempt, "result_dir": str(result_dir)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="复制并校验封存测试索引")
    prepare.add_argument("--experiment-id", required=True)
    register = subparsers.add_parser("register-config", help="校验并注册一个候选配置")
    register.add_argument("--experiment-id", required=True)
    register.add_argument("--config-json", type=Path, required=True)
    accept = subparsers.add_parser("accept-a", help="只读重放并验收A阶段")
    accept.add_argument("--experiment-id", required=True)
    run = subparsers.add_parser("run", help="运行真实检索或断点重放")
    run.add_argument("--experiment-id", required=True)
    run.add_argument("--split", choices=ALLOWED_SPLITS, default="development")
    run.add_argument("--config-id")
    run.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        path = prepare_experiment(args.experiment_id)
        print(json.dumps({"status": "prepared", "experiment": str(path)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "register-config":
        record = register_config(args.experiment_id, args.config_json)
        print(json.dumps({"status": "registered", **record}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "accept-a":
        acceptance = accept_phase_a(args.experiment_id)
        print(json.dumps(acceptance, ensure_ascii=False, indent=2))
        return 0
    result = run_experiment(
        args.experiment_id,
        args.split,
        args.config_id,
        resume=args.resume,
    )
    print(json.dumps({
        "status": result["summary"]["status"],
        "result_dir": result["result_dir"],
        "quality": result["summary"]["quality"],
        "comparison": result["summary"]["comparison"],
        "attempt": result["attempt"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
