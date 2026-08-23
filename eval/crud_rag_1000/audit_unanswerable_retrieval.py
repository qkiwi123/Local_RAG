"""Audit the 20 draft unanswerable questions against an isolated index clone.

This command never opens the production Chroma directory and never calls an
LLM.  It clones the frozen 1000-document evaluation index into ``v3/runs`` and
runs unthresholded Dense, BM25 and RRF Hybrid Top50 retrieval.  The result is
automatic review evidence only: it deliberately keeps every question in draft
and never sets ``whole_corpus_checked`` to true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
V3_DIR = ROOT / "v3"
DEFAULT_SOURCE_RUN = ROOT / "runs" / "crud-rag-1000-retrieval-v1"
DEFAULT_RUN_DIR = V3_DIR / "runs" / "unanswerable-audit-v1"
DATASET_PATH = V3_DIR / "questions_v3_120q.json"
SELECTION_PATH = V3_DIR / "unanswerable_selection_manifest.json"
STATIC_AUDIT_PATH = V3_DIR / "whole_corpus_audit.json"
CORPUS_MANIFEST_PATH = ROOT / "corpus_v3_manifest.json"
TOP_K = 50
RRF_K = 60
EXPECTED_CHUNKS = 2914
EXPECTED_DOCUMENTS = 1000
EXPECTED_MODEL = "BAAI/bge-large-zh-v1.5"
EXPECTED_QUERY_INSTRUCTION = ""
EXPECTED_SOURCE_LABEL = "crud-rag-1000-retrieval-v1"
AUDIT_VERSION = "crud-rag-1000-unanswerable-retrieval-v1-20260823"
REVIEW_DIR = V3_DIR / "retrieval_review"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _directory_manifest(path: Path) -> dict[str, Any]:
    files = []
    for item in sorted(
        (value for value in path.rglob("*") if value.is_file()),
        key=lambda value: value.relative_to(path).as_posix(),
    ):
        files.append(
            {
                "path": item.relative_to(path).as_posix(),
                "bytes": item.stat().st_size,
                "sha256": _sha256_file(item),
            }
        )
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "manifest_sha256": _sha256_bytes(canonical),
        "files": files,
    }


def _safe_replace_run_dir(run_dir: Path) -> None:
    resolved = run_dir.resolve()
    root = (V3_DIR / "runs").resolve()
    if resolved.parent != root or resolved == root:
        raise ValueError(f"拒绝清理非 v3/runs 直属目录: {resolved}")
    if run_dir.exists():
        shutil.rmtree(run_dir)


def _validate_source_run(source_run: Path) -> dict[str, Any]:
    source_report = source_run / "retrieval-sweep.json"
    source_chroma = source_run / "chroma"
    if not source_report.is_file() or not source_chroma.is_dir():
        raise FileNotFoundError(f"缺少冻结评测索引或报告: {source_run}")
    report = _read_json(source_report)
    config = report.get("config", {})
    ingest = report.get("ingest", {})
    checks = {
        "source_label": report.get("label") == EXPECTED_SOURCE_LABEL,
        "embedding_model": config.get("embedding_model") == EXPECTED_MODEL,
        "query_instruction": config.get("embedding_query_instruction", "")
        == EXPECTED_QUERY_INSTRUCTION,
        "chunk_size": config.get("chunk_size") == 400,
        "chunk_overlap": config.get("chunk_overlap") == 80,
        "chunk_strategy": config.get("chunk_strategy") == "recursive",
        "chunk_count": ingest.get("total_chunks") == EXPECTED_CHUNKS,
        "document_count": len(ingest.get("documents", {})) == EXPECTED_DOCUMENTS,
        "dataset_version": config.get("dataset_version") == "crud-rag-1000-v1",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("冻结索引配置校验失败: " + ", ".join(failed))
    return {"report": report, "checks": checks, "chroma": source_chroma}


def _configure_environment(run_dir: Path, device: str) -> None:
    os.environ["CHROMA_PERSIST_DIR"] = str((run_dir / "chroma").resolve())
    os.environ["UPLOAD_DIR"] = str((run_dir / "uploads").resolve())
    os.environ["METADATA_DB_PATH"] = str((run_dir / "metadata.db").resolve())
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["EMBEDDING_MODEL"] = EXPECTED_MODEL
    os.environ["EMBEDDING_QUERY_INSTRUCTION"] = EXPECTED_QUERY_INSTRUCTION
    os.environ["EMBEDDING_DEVICE"] = device
    os.environ["RERANKER_ENABLED"] = "false"
    os.environ["BM25_K1"] = "1.5"
    os.environ["BM25_B"] = "0.75"
    os.environ["RRF_K"] = str(RRF_K)


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(normalized.split())


def _question_anchors(question: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\u4e00-\u9fff]{4,}", _compact(question))))


def _long_gold_needles(gold_documents: Sequence[dict[str, Any]]) -> list[str]:
    needles: list[str] = []
    for gold in gold_documents:
        text = gold.get("text", "")
        for sentence in re.split(r"[。！？!?；;]", text):
            value = _compact(sentence)
            if len(value) >= 60:
                needles.append(value)
        compact = _compact(text)
        for start in range(0, max(0, min(len(compact) - 79, 1200)), 80):
            needles.append(compact[start : start + 80])
    return list(dict.fromkeys(value for value in needles if len(value) >= 60))


def _rank_rows(rows: Sequence[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    output = []
    for rank, row in enumerate(rows, 1):
        metadata = row.get("metadata") or {}
        output.append(
            {
                "rank": rank,
                "chunk_id": row["chunk_id"],
                "document_name": metadata.get("document_name"),
                "document_id": metadata.get("document_id"),
                "chunk_index": metadata.get("chunk_index"),
                "distance": row.get("distance"),
                "bm25_score": row.get("bm25_score"),
                "dense_rank": row.get("dense_rank"),
                "bm25_rank": row.get("bm25_rank"),
                "rrf_score": row.get("rrf_score"),
                "content": row.get("content", ""),
                "retrieval_mode": mode,
            }
        )
    return output


def _build_risk_summary(
    *,
    question: str,
    answer: str,
    key_facts: Sequence[str],
    gold_documents: Sequence[dict[str, Any]],
    rankings: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    answer_compact = _compact(answer)
    anchors = _question_anchors(question)
    long_needles = _long_gold_needles(gold_documents)
    flags = []
    for mode, rows in rankings.items():
        seen: set[str] = set()
        mode_texts: list[str] = []
        for row in rows:
            chunk_id = row["chunk_id"]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            compact = _compact(row["content"])
            mode_texts.append(compact)
            row_flags: list[str] = []
            if len(answer_compact) >= 6 and answer_compact in compact:
                row_flags.append("upstream_answer_exact")
            if any(needle in compact for needle in long_needles):
                row_flags.append("excluded_gold_long_overlap")
            matched_facts = [fact for fact in key_facts if _compact(fact) in compact]
            matched_anchors = [anchor for anchor in anchors if anchor in compact]
            if len(key_facts) >= 2 and len(matched_facts) == len(key_facts) and matched_anchors:
                row_flags.append("all_key_facts_with_question_anchor")
            if row_flags:
                flags.append(
                    {
                        "mode": mode,
                        "rank": row["rank"],
                        "chunk_id": chunk_id,
                        "document_name": row["document_name"],
                        "flags": row_flags,
                        "matched_key_facts": matched_facts,
                        "matched_question_anchors": matched_anchors,
                    }
                )
        combined = "\n".join(mode_texts)
        if len(answer_compact) >= 6 and answer_compact in combined:
            flags.append({"mode": mode, "scope": "top50_combined", "flags": ["upstream_answer_exact"]})
        combined_facts = [fact for fact in key_facts if _compact(fact) in combined]
        combined_anchors = [anchor for anchor in anchors if anchor in combined]
        if (
            len(key_facts) >= 2
            and len(combined_facts) == len(key_facts)
            and combined_anchors
        ):
            flags.append(
                {
                    "mode": mode,
                    "scope": "top50_combined",
                    "flags": ["all_key_facts_with_question_anchor"],
                    "matched_key_facts": combined_facts,
                    "matched_question_anchors": combined_anchors,
                }
            )
    return {
        "automatic_risk_flag_count": len(flags),
        "automatic_risk_flags": flags,
        "requires_human_semantic_review": True,
        "automatic_result": "needs_review" if flags else "no_obvious_leak_detected",
        "note": "无自动风险标记不等于不可回答；同义改写和跨文档拼接仍需人工审核。",
    }


def _render_review(item: dict[str, Any]) -> str:
    lines = [
        f"# 检索审核附录：{item['id']}",
        "",
        "> 自动证据，不是人工通过结论；whole_corpus_checked 仍为 false。",
        "",
        f"- 问题：{item['question']}",
        f"- 不可回答类型：`{item['unanswerable_type']}`",
        f"- 上游答案（仅供泄漏核查）：{item['upstream_answer']}",
        f"- 自动结果：`{item['risk_summary']['automatic_result']}`",
        f"- 自动风险标记数：{item['risk_summary']['automatic_risk_flag_count']}",
        "- 检索：Dense/BM25/Hybrid 均为无阈值 Top50；Hybrid 使用 RRF，未运行 reranker。",
        "",
    ]
    if item["risk_summary"]["automatic_risk_flags"]:
        lines.extend(
            [
                "## 自动风险标记",
                "",
                "```json",
                json.dumps(item["risk_summary"]["automatic_risk_flags"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    for mode in ("dense", "bm25", "hybrid"):
        lines.extend([f"## {mode.upper()} Top10", ""])
        for row in item["rankings"][mode][:10]:
            score = (
                f"distance={row['distance']:.6f}"
                if row.get("distance") is not None and mode == "dense"
                else f"bm25={row['bm25_score']:.6f}"
                if row.get("bm25_score") is not None and mode == "bm25"
                else f"rrf={row['rrf_score']:.8f}"
                if row.get("rrf_score") is not None
                else "score=n/a"
            )
            lines.extend(
                [
                    f"### {row['rank']}. `{row['document_name']}` / `{row['chunk_id']}` / {score}",
                    "",
                    "```text",
                    row["content"],
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "## 人工结论",
            "",
            "- 结论：`approve / revise / reject`",
            "- 是否存在完整答案或可拼接答案：",
            "- reviewer：",
            "- reviewed_at：",
            "- 备注：",
            "",
        ]
    )
    return "\n".join(lines)


def _render_summary(payload: dict[str, Any]) -> str:
    runtime = payload["runtime"]
    summary = payload["summary"]
    isolation = payload["database_isolation"]
    type_counts = Counter(item["unanswerable_type"] for item in payload["questions"])
    return "\n".join(
        [
            "# 20 道不可回答题隔离检索审计摘要",
            "",
            "> 本轮完成的是自动检索与 AI 辅助语义复核证据，不是人工 verified 审核；whole_corpus_checked 仍为 false。",
            "",
            "## 结果",
            "",
            f"- 题目：{summary['question_count']} 道",
            f"- 类型：out_of_scope={type_counts.get('out_of_scope', 0)}、insufficient_detail={type_counts.get('insufficient_detail', 0)}",
            f"- 自动风险标记：{summary['total_automatic_risk_flags']}",
            f"- 状态：`{payload['status']}`",
            "- 检索：Dense/BM25/Hybrid 均为无阈值 Top50；Hybrid 使用 RRF(k=60)",
            "- Reranker：未调用（本阶段检查三条前级检索路径）",
            "- LLM：未调用",
            "",
            "## 设备与耗时",
            "",
            f"- Embedding：`{runtime['embedding_model']}` / `{runtime['embedding_device']}` / {runtime['cuda_device_name']}",
            f"- GPU 峰值 allocated：{runtime['cuda_peak_allocated_bytes'] / 1024 / 1024:.1f} MiB",
            f"- GPU 峰值 reserved：{runtime['cuda_peak_reserved_bytes'] / 1024 / 1024:.1f} MiB",
            f"- BM25：{runtime['bm25_device']}（算法本身运行于 CPU）",
            f"- 总耗时：{summary['elapsed_s']:.3f} s",
            f"- 平均每题 embedding：{summary['timings_ms_mean']['embedding_ms']:.3f} ms",
            f"- 平均每题 Dense：{summary['timings_ms_mean']['dense_ms']:.3f} ms",
            f"- 平均每题 BM25：{summary['timings_ms_mean']['bm25_ms']:.3f} ms",
            f"- 平均每题 RRF：{summary['timings_ms_mean']['hybrid_rrf_ms']:.3f} ms",
            "",
            "## 数据库隔离",
            "",
            f"- 生产数据库使用：`{str(isolation['production_database_used']).lower()}`",
            f"- 冻结源索引直接打开：`{str(isolation['source_frozen_eval_index_opened']).lower()}`",
            f"- 隔离副本：`{isolation['isolated_clone_path']}`",
            f"- 索引：{payload['index_counts']['documents']} documents / {payload['index_counts']['chunks']} chunks",
            "- 复制前源索引与副本逐文件 SHA-256 一致；查询仅打开 v3/runs 下的副本。",
            "",
            "## 限制",
            "",
            "- Top50 不能从逻辑上证明整个 corpus 不含任何同义信息。",
            "- 无自动风险标记不等于已通过人工审核。",
            "- 同义改写与跨文档拼接仍需逐题语义复核；正式 strict Dataset v3 仍会因 draft 状态失败。",
            "",
        ]
    )


def run_audit(source_run: Path, run_dir: Path, device: str, overwrite: bool) -> dict[str, Any]:
    source = _validate_source_run(source_run)
    dataset = _read_json(DATASET_PATH)
    selection = _read_json(SELECTION_PATH)
    static_audit = _read_json(STATIC_AUDIT_PATH)
    corpus_manifest = _read_json(CORPUS_MANIFEST_PATH)
    unanswerable = [item for item in dataset["questions"] if item.get("answerable") is False]
    if len(unanswerable) != 20:
        raise ValueError(f"预期20道不可回答题，实际{len(unanswerable)}道")
    if static_audit.get("retrieval", {}).get("status") != "pending":
        raise ValueError("静态审计原件不是 pending 状态，拒绝覆盖式审计")

    if run_dir.exists():
        if not overwrite:
            raise FileExistsError(f"运行目录已存在，请换 --run-dir 或显式加 --overwrite: {run_dir}")
        _safe_replace_run_dir(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)

    source_manifest = _directory_manifest(source["chroma"])
    shutil.copytree(source["chroma"], run_dir / "chroma")
    clone_manifest_before = _directory_manifest(run_dir / "chroma")
    if source_manifest != clone_manifest_before:
        raise RuntimeError("隔离索引复制后逐文件 hash 不一致")

    _configure_environment(run_dir, device)
    sys.path.insert(0, str(PROJECT_ROOT))
    import torch
    from backend.config import settings
    from backend.services import embedding, sparse_retriever, vector_store
    from backend.services.fusion import reciprocal_rank_fusion

    if Path(settings.CHROMA_PERSIST_DIR).resolve() != (run_dir / "chroma").resolve():
        raise RuntimeError("Chroma 隔离目录注入失败")
    if Path(settings.CHROMA_PERSIST_DIR).resolve() == (PROJECT_ROOT / "data" / "chroma_db").resolve():
        raise RuntimeError("拒绝使用生产 Chroma 目录")
    if not device.startswith("cuda") or not torch.cuda.is_available():
        raise RuntimeError(f"本审计要求 CUDA embedding，当前 device={device}, cuda={torch.cuda.is_available()}")

    chunks = vector_store.get_all_chunks()
    document_names = sorted({(item.get("metadata") or {}).get("document_name") for item in chunks})
    expected_names = sorted(item["file"] for item in corpus_manifest["documents"])
    if len(chunks) != EXPECTED_CHUNKS or document_names != expected_names:
        raise RuntimeError(
            f"隔离索引内容不符：chunks={len(chunks)}, documents={len(document_names)}"
        )

    selection_by_id = {
        item["id"]: item for item in selection["selection"]["questions"]
    }
    static_by_id = {item["id"]: item for item in static_audit["questions"]}
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    questions_out = []
    timings = []
    for index, question in enumerate(unanswerable, 1):
        item_id = question["id"]
        selected = selection_by_id[item_id]
        static = static_by_id[item_id]
        query = question["question"]

        torch.cuda.synchronize()
        step = time.perf_counter()
        query_embedding = embedding.embed_query(query)
        torch.cuda.synchronize()
        embedding_ms = (time.perf_counter() - step) * 1000

        step = time.perf_counter()
        dense_raw = vector_store.search_by_embedding(query_embedding, top_k=TOP_K)
        dense_ms = (time.perf_counter() - step) * 1000
        for rank, row in enumerate(dense_raw, 1):
            row["dense_rank"] = rank

        step = time.perf_counter()
        bm25_raw = sparse_retriever.search(query, top_k=TOP_K)
        bm25_ms = (time.perf_counter() - step) * 1000

        step = time.perf_counter()
        hybrid_raw = reciprocal_rank_fusion(dense_raw, bm25_raw, rrf_k=RRF_K, top_k=TOP_K)
        hybrid_ms = (time.perf_counter() - step) * 1000

        rankings = {
            "dense": _rank_rows(dense_raw, "dense"),
            "bm25": _rank_rows(bm25_raw, "bm25"),
            "hybrid": _rank_rows(hybrid_raw, "hybrid"),
        }
        risk = _build_risk_summary(
            question=query,
            answer=selected["upstream_answer"],
            key_facts=static.get("key_facts", []),
            gold_documents=selected["excluded_gold_documents"],
            rankings=rankings,
        )
        question_timing = {
            "embedding_ms": round(embedding_ms, 3),
            "dense_ms": round(dense_ms, 3),
            "bm25_ms": round(bm25_ms, 3),
            "hybrid_rrf_ms": round(hybrid_ms, 3),
        }
        timings.append(question_timing)
        questions_out.append(
            {
                "id": item_id,
                "split": question["split"],
                "category": selected["category"],
                "question": query,
                "unanswerable_type": question["unanswerable_type"],
                "upstream_answer": selected["upstream_answer"],
                "excluded_gold_hashes": [value["sha256"] for value in selected["excluded_gold_documents"]],
                "rankings": rankings,
                "risk_summary": risk,
                "timings_ms": question_timing,
                "review": {
                    "status": "draft",
                    "whole_corpus_checked": False,
                    "reviewer": None,
                    "reviewed_at": None,
                },
            }
        )
        print(f"[{index:02d}/20] {item_id}: flags={risk['automatic_risk_flag_count']}", flush=True)

    torch.cuda.synchronize()
    elapsed_s = time.perf_counter() - started
    clone_manifest_after = _directory_manifest(run_dir / "chroma")
    count_by_result = Counter(item["risk_summary"]["automatic_result"] for item in questions_out)
    payload = {
        "schema_version": 1,
        "audit_version": AUDIT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset["dataset_id"],
        "corpus_id": dataset["corpus_id"],
        "status": "automatic_retrieval_complete_human_review_pending",
        "whole_corpus_checked": False,
        "llm_called": False,
        "reranker_called": False,
        "database_used": True,
        "database_isolation": {
            "production_database_used": False,
            "source_frozen_eval_index_opened": False,
            "source_index_path": source["chroma"].relative_to(PROJECT_ROOT).as_posix(),
            "isolated_clone_path": (run_dir / "chroma").relative_to(PROJECT_ROOT).as_posix(),
            "source_clone_equal_before_open": True,
        },
        "source_index": {
            "checks": source["checks"],
            "source_manifest": source_manifest,
            "clone_manifest_before_open": clone_manifest_before,
            "clone_manifest_after_open": clone_manifest_after,
        },
        "runtime": {
            "python": sys.version,
            "embedding_model": settings.EMBEDDING_MODEL,
            "embedding_query_instruction": settings.EMBEDDING_QUERY_INSTRUCTION,
            "embedding_device": settings.EMBEDDING_DEVICE,
            "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "cuda_device_name": torch.cuda.get_device_name(0),
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "bm25_device": "cpu",
            "hybrid_method": "RRF",
            "rrf_k": RRF_K,
            "top_k": TOP_K,
            "distance_threshold": None,
            "reranker_enabled": False,
        },
        "index_counts": {"chunks": len(chunks), "documents": len(document_names)},
        "summary": {
            "question_count": len(questions_out),
            "automatic_results": dict(sorted(count_by_result.items())),
            "total_automatic_risk_flags": sum(
                item["risk_summary"]["automatic_risk_flag_count"] for item in questions_out
            ),
            "elapsed_s": round(elapsed_s, 3),
            "timings_ms_mean": {
                key: round(sum(item[key] for item in timings) / len(timings), 3)
                for key in timings[0]
            },
        },
        "questions": questions_out,
        "limitations": [
            "Top50 retrieval is an audit aid, not exhaustive semantic proof.",
            "No automatic result is allowed to set whole_corpus_checked=true.",
            "Semantic paraphrases and evidence assembled across documents require human review.",
            "The cloned index was originally embedded on CPU; query embeddings run on GPU with the same model/config.",
        ],
    }

    output_path = V3_DIR / "retrieval_top50.json"
    audit_path = V3_DIR / "whole_corpus_retrieval_audit.json"
    _write_json(output_path, payload)
    retrieval_audit = {
        key: payload[key]
        for key in (
            "schema_version",
            "audit_version",
            "created_at",
            "dataset_id",
            "corpus_id",
            "status",
            "whole_corpus_checked",
            "llm_called",
            "reranker_called",
            "database_used",
            "database_isolation",
            "runtime",
            "index_counts",
            "summary",
            "limitations",
        )
    }
    retrieval_audit["questions"] = [
        {
            "id": item["id"],
            "automatic_result": item["risk_summary"]["automatic_result"],
            "automatic_risk_flag_count": item["risk_summary"]["automatic_risk_flag_count"],
            "automatic_risk_flags": item["risk_summary"]["automatic_risk_flags"],
            "dense_top50_count": len(item["rankings"]["dense"]),
            "bm25_top50_count": len(item["rankings"]["bm25"]),
            "hybrid_top50_count": len(item["rankings"]["hybrid"]),
            "review": item["review"],
        }
        for item in questions_out
    ]
    _write_json(audit_path, retrieval_audit)

    summary_path = V3_DIR / "retrieval_summary.md"
    summary_path.write_text(_render_summary(payload), encoding="utf-8", newline="\n")

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    expected_review_names = {f"{item['id']}.md" for item in questions_out}
    for stale_path in REVIEW_DIR.glob("*.md"):
        if stale_path.name not in expected_review_names:
            stale_path.unlink()
    for item in questions_out:
        (REVIEW_DIR / f"{item['id']}.md").write_text(
            _render_review(item), encoding="utf-8", newline="\n"
        )

    provenance = {
        "schema_version": 1,
        "audit_version": AUDIT_VERSION,
        "inputs": {
            "dataset": {"path": "questions_v3_120q.json", "sha256": _sha256_file(DATASET_PATH)},
            "selection": {"path": "unanswerable_selection_manifest.json", "sha256": _sha256_file(SELECTION_PATH)},
            "static_audit": {"path": "whole_corpus_audit.json", "sha256": _sha256_file(STATIC_AUDIT_PATH)},
            "corpus_manifest": {"path": "../corpus_v3_manifest.json", "sha256": _sha256_file(CORPUS_MANIFEST_PATH)},
            "source_index_manifest_sha256": source_manifest["manifest_sha256"],
        },
        "outputs": {
            "retrieval_top50": {"path": "retrieval_top50.json", "sha256": _sha256_file(output_path)},
            "retrieval_audit": {"path": "whole_corpus_retrieval_audit.json", "sha256": _sha256_file(audit_path)},
            "retrieval_summary": {"path": "retrieval_summary.md", "sha256": _sha256_file(summary_path)},
            "review_packet_count": len(list(REVIEW_DIR.glob("*.md"))),
            "isolated_index": (run_dir / "chroma").relative_to(V3_DIR).as_posix(),
        },
        "claims": {
            "automatic_retrieval_complete": True,
            "human_review_complete": False,
            "whole_corpus_checked": False,
            "llm_called": False,
            "production_database_used": False,
        },
    }
    _write_json(V3_DIR / "retrieval_provenance.json", provenance)
    return {
        "output": output_path,
        "audit": audit_path,
        "question_count": len(questions_out),
        "summary": payload["summary"],
        "whole_corpus_checked": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = run_audit(args.source_run, args.run_dir, args.device, args.overwrite)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({**result, "output": str(result["output"]), "audit": str(result["audit"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
