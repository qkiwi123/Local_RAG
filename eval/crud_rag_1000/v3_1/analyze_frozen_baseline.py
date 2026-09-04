"""Read-only acceptance/rescoring of saved baseline outputs; never runs models.

Writes new analysis.json and failure-analysis.md inside the existing audit
directory, refusing to overwrite either. Historical artifacts stay untouched.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT))
from eval.dataset_runtime import normalize_for_evaluation
from eval.metrics import gold_document_names, norm_doc_name
from eval.retrieval_sweep import aggregate_rows, build_candidate_result

LABEL = "gpu-baseline-120q-v31-k15-20260828"
RUN = ROOT / "runs" / LABEL
AUDIT = ROOT / "baseline_audits" / LABEL
OLD = ROOT.parent / "v3/runs/gpu-baseline-120q-k15-v3-preflight-20260823"
CPU = ROOT.parent / "v3/runs/cpu-baseline-120q-k15-v3-preflight-20260823"
QUALITY = ["document_hit_rate", "document_recall", "document_mrr",
           "all_gold_docs_recalled_rate", "multi_document_count",
           "multi_document_complete_recall_rate", "evidence_coverage",
           "exact_evidence_containment_rate", "recall",
           "unanswerable_count", "unanswerable_llm_entry_rate"]
EXPECTED_TRACE = {
    "retrieval_mode": "hybrid", "final_top_k": 5, "dense_candidate_k": 15,
    "dense_distance_threshold": 0.67, "bm25_top_k": 15, "bm25_k1": 1.5,
    "bm25_b": 0.75, "rrf_k": 60, "reranker_enabled": True,
    "reranker_candidate_k": 15, "max_chunks_per_document": 3,
    "refusal_gate_enabled": False,
}
# Existing review prose has two one-character ID transcription errors. Resolve
# ONLY report joins after checking query/reference/evidence; never edit QA IDs.
REVIEW_ID_ALIASES = {
    "crud-qa-1doc-64fa9b31b82641eb8ecc0bd6": "crud-qa-1doc-64fa9b32b82641eb8ecc0bd6",
    "crud-qa-1doc-64fa9b2cb82641eb8ecbf3ea": "crud-qa-1doc-64fa9b2db82641eb8ecbf3ea",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def raw(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def compact(rows):
    result = aggregate_rows(rows)
    return {"question_count": len(rows), "answerable_count": sum(r["answerable"] for r in rows),
            **{key: result.get(key) for key in QUALITY}}


def latency(rows):
    values = [row["retrieval_s"] for row in rows]
    ordered = sorted(values)
    return {"sum_s": round(sum(values), 3), "first_s": values[0],
            "mean_s": statistics.mean(values), "warm_mean_s": statistics.mean(values[1:]),
            "p50_s": ordered[round(.50 * (len(values) - 1))],
            "p95_s": ordered[round(.95 * (len(values) - 1))],
            "warm_stage_mean_ms": {
                key: statistics.mean(row["trace"]["timings_ms"][key] for row in rows[1:])
                for key in ["query_embedding", "dense_retrieval", "sparse_retrieval", "rrf", "rerank", "total"]}}


def database_checks():
    results = {}
    for relative, tables in [("metadata.db", ["documents", "document_units", "messages", "runtime_settings"]),
                             ("chroma/chroma.sqlite3", ["embeddings"])]:
        path = (RUN / relative).resolve()
        assert path.is_relative_to(RUN.resolve()) and path.is_file()
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            integrity = connection.execute("PRAGMA quick_check").fetchall()
            assert integrity == [("ok",)]
            results[relative] = {"quick_check": "ok", **{
                table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in tables}}
        finally:
            connection.close()
    assert results["metadata.db"]["documents"] == 1000
    assert results["metadata.db"]["document_units"] == 1000
    assert results["metadata.db"]["messages"] == 0
    assert results["metadata.db"]["runtime_settings"] == 0
    assert results["chroma/chroma.sqlite3"]["embeddings"] == 2914
    return results


def main():
    assert not (AUDIT / "analysis.json").exists()
    assert not (AUDIT / "failure-analysis.md").exists()
    dataset = normalize_for_evaluation(load(ROOT / "questions_v3_120q.json"))
    questions = {q["id"]: q for q in dataset["questions"]}
    report, previous, cpu = [load(path / "retrieval-sweep.json") for path in [RUN, OLD, CPU]]
    candidate, old_candidate = report["candidates"][0], previous["candidates"][0]
    rows, old_rows = raw(RUN / "retrieval-k15.jsonl"), raw(OLD / "retrieval-k15.jsonl")
    current, old = {r["id"]: r for r in rows}, {r["id"]: r for r in old_rows}
    metrics = {r["id"]: r for r in candidate["per_question"]}
    old_metrics = {r["id"]: r for r in old_candidate["per_question"]}
    completion = load(AUDIT / "completion.json")
    preflight = load(AUDIT / "preflight.json")
    assert completion["runner_completed"] and all(completion["unchanged"].values())
    assert completion["llm_module_imported"] is False
    assert not completion["outbound_ip_connections_denied"]
    assert len(rows) == len(current) == 120 and set(current) == set(questions)
    assert len(report["candidates"]) == 1 and candidate["candidate_k"] == 15
    assert report["config"]["llm_called"] is False
    assert report["evaluation_scope"]["workflow"] == "retrieval_only"
    assert report["config"]["dataset_version"] == dataset["version"]
    for path in report["evaluation_scope"]["storage"].values():
        assert Path(path).resolve().is_relative_to(RUN.resolve())
    for name in ["embedding", "reranker"]:
        model = report["config"]["runtime"][name]
        assert model["loaded"] and model["verified"] and model["actual_device"] == "cuda:0"
    for row in rows:
        trace = row["trace"]
        assert trace["effective_config"] == EXPECTED_TRACE
        assert trace["settings_revision"] is None and trace["llm"] is None
        assert trace["timings_ms"]["llm_generation"] == 0
        assert trace["selected_chunk_ids"] == [r["chunk_id"] for r in row["retrieval"]]
        assert len(row["retrieval"]) == 5
        assert max(Counter(r["metadata"]["document_name"] for r in row["retrieval"]).values()) <= 3
        assert [s["name"] for s in trace["stages"]] == ["dense", "bm25", "rrf", "reranker", "selected"]
        assert all(len(s["candidates"]) <= (5 if s["name"] == "selected" else 15) for s in trace["stages"])
    recomputed = build_candidate_result(candidate_k=15, questions=list(questions.values()),
        retrievals={key: row["retrieval"] for key, row in current.items()},
        timings_s=[r["retrieval_s"] for r in rows],
        traces={key: row["trace"] for key, row in current.items()})
    assert recomputed == candidate, "Saved metrics differ from pure rescore"
    mapping = load(ROOT / "id_mapping.json")["mapping"]
    cohorts = {}
    for changed in [False, True]:
        entries = [m for m in mapping if m["content_changed"] == changed]
        same = lambda f: sum(f(current[m["new_id"]]) == f(old[m["old_id"]]) for m in entries)
        cohorts["changed_10" if changed else "unchanged_110"] = {
            "old": compact([old_metrics[m["old_id"]] for m in entries]),
            "new": compact([metrics[m["new_id"]] for m in entries]),
            "top1_chunk_identical": same(lambda r: r["retrieval"][0]["chunk_id"]),
            "top5_set_identical": same(lambda r: {v["chunk_id"] for v in r["retrieval"]}),
            "top5_order_identical": same(lambda r: [v["chunk_id"] for v in r["retrieval"]]),
        }
    risk_text = (ROOT / "AI预审风险队列-v3.1.md").read_text(encoding="utf-8")
    def section_ids(start, end):
        ids = set(re.findall(r"crud-(?:qa|unanswerable)-[a-z0-9-]+",
                  risk_text.split(start, 1)[1].split(end, 1)[0]))
        return {REVIEW_ID_ALIASES.get(qid, qid) for qid in ids}
    assert "纪念碑" in questions[REVIEW_ID_ALIASES["crud-qa-1doc-64fa9b31b82641eb8ecc0bd6"]]["question"]
    assert "台湾有事" in questions[REVIEW_ID_ALIASES["crud-qa-1doc-64fa9b2cb82641eb8ecbf3ea"]]["reference_answer"]
    risk_ids = {
        "corrected_high_answerable_10": {m["new_id"] for m in mapping if m["content_changed"]},
        "high_unanswerable_4": section_ids("## 4.", "## 5."),
        "medium_content_25": section_ids("## 6.", "## 7."),
        "retrieval_metric_stress_21": section_ids("## 5.", "## 6.") | section_ids("## 7.", "## 8."),
    }
    assigned = set().union(*risk_ids.values())
    assert sum(map(len, risk_ids.values())) == len(assigned) == 60
    assert assigned <= set(questions)
    risk_ids["previously_no_obvious_content_issue_60"] = set(questions) - assigned
    failures = []
    for row in candidate["per_question"]:
        if not row["answerable"] or (row["all_gold_docs_recalled"] and row["evidence_coverage"] == 1 and row["exact_evidence_containment"]):
            continue
        q = questions[row["id"]]
        trace = current[row["id"]]["trace"]
        gold = gold_document_names(q)
        stages = {}
        for stage in trace["stages"]:
            names = {norm_doc_name(trace["chunks"][c["chunk_id"]]["document_name"])
                     for c in stage["candidates"] if c.get("eligible", True)}
            stages[stage["name"]] = {"gold_matched": len(gold & names),
                                     "gold_missing": sorted(gold - names)}
        failures.append({"id": row["id"], "question": q["question"],
                         "gold_count": len(gold), "stages": stages, "metrics": row})
    samples = raw(AUDIT / "gpu-samples.jsonl")
    devices = [s["devices"][0] for s in samples if s.get("devices")]
    sample_summary = {"sample_count": len(devices), "interval_s": 5,
        "whole_device_used_mib_min": min(float(d[3]) for d in devices),
        "whole_device_used_mib_max": max(float(d[3]) for d in devices),
        "whole_device_utilization_percent_max": max(float(d[4]) for d in devices),
        "whole_device_power_w_max": max(float(d[5]) for d in devices)}
    result = {
        "acceptance": "passed", "model_inference_repeated": False,
        "checks": {"rows_ids_traces": "120/120", "effective_config": EXPECTED_TRACE,
                   "pure_rescore_exact_match": True, "databases": database_checks(),
                   "production_inputs_code_unchanged": completion["unchanged"],
                   "gpu_models_verified": True, "llm_called": False},
        "old": {"label": previous["label"], "quality": compact(old_candidate["per_question"]),
                "ingest_s": previous["ingest"]["ingest_s"], "latency": latency(old_rows)},
        "new": {"label": LABEL, "quality": compact(candidate["per_question"]),
                "ingest_s": report["ingest"]["ingest_s"], "latency": latency(rows),
                "wall_s": completion["wall_s"], "gpu_samples": sample_summary,
                "runtime": report["config"]["runtime"]},
        "historical_cpu_reference_only": {"label": cpu["label"], "ingest_s": cpu["ingest"]["ingest_s"],
            "latency_s": cpu["candidates"][0]["latency_s"], "not_a_new_v31_cpu_run": True},
        "cohorts": cohorts,
        "review_document_id_aliases_only": REVIEW_ID_ALIASES,
        "by_risk": {name: {"ids": sorted(ids), **compact([metrics[qid] for qid in sorted(ids)])}
                    for name, ids in risk_ids.items()},
        "by_split": {split: compact([r for r in metrics.values() if r["split"] == split])
                     for split in ["development", "validation", "frozen_test"]},
        "failures": failures,
        "dataset_sha256": preflight["snapshots"]["inputs"]["eval/crud_rag_1000/v3_1/questions_v3_120q.json"]["sha256"],
        "limits": ["draft dataset; not a newly unseen blind test", "single pass; not a controlled CPU/GPU benchmark",
                   "historical/current code differ; do not attribute every delta to QA changes",
                   "document ID scoring retains alternative-report false negatives",
                   "nonempty results simulate generation entry; no actual LLM answer/refusal score"],
    }
    lines = ["# v3.1固定基线：失败与指标边界", "", "仅分析已有JSONL和Trace，没有再次检索或调用模型。", "",
             "有6道题未召回全部gold；另有2道单chunk完整包含失败、1道覆盖不完整。", "",
             "| 题目ID | gold数 | Dense过滤后 | BM25 | RRF | Reranker全部候选 | 最终Top5 | 证据覆盖 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for failure in failures:
        counts = [str(failure["stages"][s]["gold_matched"]) for s in ["dense", "bm25", "rrf", "reranker", "selected"]]
        lines.append(f"| `{failure['id']}` | {failure['gold_count']} | " + " | ".join(counts)
                     + f" | {failure['metrics']['evidence_coverage']:.2%} |")
    for failure in failures:
        lines += ["", f"## {failure['id']}", "", failure["question"], "",
                  "- 最终缺少的gold：" + ("、".join(failure["stages"]["selected"]["gold_missing"]) or "无"),
                  f"- 单chunk完整包含至少一条证据：{failure['metrics']['exact_evidence_containment']}。",
                  f"- 全部evidence的逐条exact召回：{failure['metrics']['exact_evidence_recall']:.2%}。",
                  "- 完整候选、分数和原文见对应题目的retrieval-k15.jsonl；本轮未删题或改gold。"]
    with (AUDIT / "analysis.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    with (AUDIT / "failure-analysis.md").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(json.dumps({"acceptance": "passed", "questions": len(rows),
                      "new_quality": result["new"]["quality"], "cohorts": cohorts,
                      "gpu_samples": sample_summary}, ensure_ascii=False, indent=2))


def seal():
    """Freeze hashes after documentation acceptance, without changing old outputs."""
    target = AUDIT / "artifact-manifest.json"
    assert not target.exists()
    assert load(AUDIT / "analysis.json")["acceptance"] == "passed"
    files = {path for base in [RUN, AUDIT] for path in base.rglob("*") if path.is_file()
             and "temp" not in path.relative_to(base).parts}
    files.update(path for path in ROOT.iterdir() if path.is_file())
    files.update(ROOT.glob(".validation/baseline-*/**/*.xml"))
    entries = {}
    for path in sorted(files):
        assert path.resolve().is_relative_to(ROOT.resolve())
        assert not path.is_symlink() and not path.is_junction()
        with path.open("rb") as handle:
            entries[path.relative_to(ROOT).as_posix()] = {
                "sha256": hashlib.file_digest(handle, "sha256").hexdigest(),
                "bytes": path.stat().st_size,
            }
    with target.open("x", encoding="utf-8") as handle:
        json.dump({"label": LABEL, "sealed_at": datetime.now().astimezone().isoformat(),
                   "scope": "new version files, completed run, audit and test XML; excludes temp caches",
                   "files": entries}, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"sealed": True, "file_count": len(entries), "manifest": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:] == ["--seal"]:
        seal()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Usage: analyze_frozen_baseline.py [--seal]")
