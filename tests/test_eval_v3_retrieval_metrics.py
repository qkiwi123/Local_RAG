"""Dataset v3 retrieval metrics and offline gate/trace diagnostics."""

from eval.metrics import (
    aggregate,
    aggregate_unanswerable_gates,
    all_gold_docs_recalled,
    document_hit_at_k,
    document_mrr,
    document_recall_at_k,
    evidence_coverage,
    evidence_coverage_details,
    evidence_interval_coverage,
    exact_evidence_containment,
    score_question,
)
from eval.retrieval_sweep import (
    _recommend_candidate,
    aggregate_trace_metrics,
    build_candidate_result,
    render_retrieval_sweep,
)


def _result(
    document_name,
    content="",
    *,
    start_char=None,
    end_char=None,
    distance=None,
    rrf_score=None,
    reranker_score=None,
    chunk_id="chunk",
):
    metadata = {"document_name": document_name}
    if start_char is not None:
        metadata["start_char"] = start_char
    if end_char is not None:
        metadata["end_char"] = end_char
    result = {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": metadata,
    }
    if distance is not None:
        result["distance"] = distance
    if rrf_score is not None:
        result["rrf_score"] = rrf_score
    if reranker_score is not None:
        result["reranker_score"] = reranker_score
    return result


QUESTION = {
    "id": "v3-q1",
    "category": "multi_doc",
    "answerable": True,
    "evidence": [
        {
            "document_name": "a.txt",
            "snippet": "证据 A",
            "start_char": 10,
            "end_char": 20,
        },
        {
            "document_name": "a.txt",
            "snippet": "证据 A 的第二段",
            "start_char": 30,
            "end_char": 35,
        },
        {
            "document_name": "b.txt",
            "snippet": "证据 B",
            "start_char": 0,
            "end_char": 5,
        },
    ],
    "keypoints": [],
}


def test_document_metrics_dedupe_gold_documents_and_respect_top_k():
    retrieval = [
        _result("noise.txt", "无关", chunk_id="n"),
        _result("a.txt", "证据 A", chunk_id="a1"),
        _result("a.txt", "重复证据", chunk_id="a2"),
        _result("b.txt", "证据 B", chunk_id="b1"),
    ]

    # a.txt appears twice in gold evidence but counts once.
    assert document_hit_at_k(QUESTION, retrieval, k=1) is False
    assert document_hit_at_k(QUESTION, retrieval, k=2) is True
    assert document_recall_at_k(QUESTION, retrieval, k=3) == 0.5
    assert document_recall_at_k(QUESTION, retrieval, k=4) == 1.0
    assert document_mrr(QUESTION, retrieval, k=4) == 0.5
    assert all_gold_docs_recalled(QUESTION, retrieval, k=3) is False
    assert all_gold_docs_recalled(QUESTION, retrieval, k=4) is True


def test_evidence_coverage_uses_union_of_multiple_chunk_intervals():
    evidence = QUESTION["evidence"][0]
    retrieval = [
        _result("a.txt", "左半段", start_char=10, end_char=18, chunk_id="left"),
        # overlaps the prior chunk by three characters; it must not double count.
        _result("a.txt", "右半段", start_char=15, end_char=20, chunk_id="right"),
        _result("other.txt", "同坐标但错误文档", start_char=10, end_char=20, chunk_id="wrong"),
    ]

    assert evidence_interval_coverage(evidence, retrieval) == 1.0
    assert evidence_coverage({"evidence": [evidence]}, retrieval) == 1.0


def test_evidence_coverage_prefers_shared_unit_index_across_id_namespaces():
    evidence = {
        "document_name": "support/doc.txt",
        "unit_id": "support/doc.txt:u0",
        "locator": {"unit_index": 0},
        "start_char": 100,
        "end_char": 200,
    }
    retrieval = [
        {
            "chunk_id": "runtime-chunk",
            "content": "covered",
            "metadata": {
                "document_name": "support/doc.txt",
                "unit_id": "runtime-hash-id",
                "unit_index": 0,
                "start_char": 80,
                "end_char": 220,
            },
        }
    ]

    assert evidence_interval_coverage(evidence, retrieval) == 1.0


def test_missing_offsets_are_none_and_exact_containment_stays_diagnostic():
    evidence = {
        "document_name": "a.txt",
        "snippet": "完整证据",
    }
    retrieval = [_result("a.txt", "完整证据")]

    assert exact_evidence_containment({"evidence": [evidence]}, retrieval) is True
    assert evidence_interval_coverage(evidence, retrieval) is None
    details = evidence_coverage_details({"evidence": [evidence]}, retrieval)
    assert details["coverage"] is None
    assert details["missing_offsets"] == 1
    assert details["all_available"] is False


def test_evidence_coverage_reports_real_zero_for_missing_or_non_overlapping_gold():
    evidence = {
        "document_name": "gold.txt",
        "snippet": "gold",
        "start_char": 10,
        "end_char": 20,
    }
    assert evidence_interval_coverage(evidence, []) == 0.0
    assert evidence_interval_coverage(
        evidence,
        [_result("gold.txt", "other range", start_char=30, end_char=40)],
    ) == 0.0
    # A matching document with no usable offsets remains unavailable.
    assert evidence_interval_coverage(evidence, [_result("gold.txt", "gold")]) is None


def test_score_question_exposes_new_metrics_without_removing_legacy_exact_fields():
    q = {
        **QUESTION,
        "keypoints": [],
    }
    retrieval = [
        _result("a.txt", "左侧片段", start_char=10, end_char=15, chunk_id="a1"),
        _result("a.txt", "右侧片段", start_char=15, end_char=20, chunk_id="a2"),
        _result("a.txt", "第二段", start_char=30, end_char=35, chunk_id="a3"),
        _result("b.txt", "B片段", start_char=0, end_char=5, chunk_id="b"),
    ]
    # No single chunk contains the first evidence snippet, so old hit remains
    # an exact diagnostic while document metrics and coverage pass.
    row = score_question(q, "回答", retrieval)
    assert row["hit"] is False
    assert row["exact_evidence_containment"] is False
    assert row["document_hit_at_k"] is True
    assert row["document_recall_at_k"] == 1.0
    assert row["document_mrr"] == 1.0
    assert row["all_gold_docs_recalled"] is True
    assert row["multi_document_complete_recall"] is True
    assert row["evidence_coverage"] == 1.0
    assert row["evidence_coverage_all_available"] is True


def test_unanswerable_gate_aggregation_never_claims_llm_or_invents_missing_fields():
    rows = [
        {"answerable": False, "llm_entry": False, "refusal_gate": True},
        {"answerable": False, "llm_entry": True, "refusal_gate": False},
    ]
    result = aggregate_unanswerable_gates(rows)
    assert result["unanswerable_llm_entry_rate"] == 0.5
    assert result["unanswerable_refusal_gate_rate"] == 0.5
    assert result["unanswerable_refusal_accuracy"] is None


def test_aggregate_reports_document_metrics_and_multi_document_complete_rate():
    q1 = {
        "id": "q1",
        "category": "single",
        "answerable": True,
        "evidence": [
            {"document_name": "a.txt", "snippet": "A", "start_char": 0, "end_char": 1}
        ],
        "keypoints": [],
    }
    q2 = {
        "id": "q2",
        "category": "multi_doc",
        "answerable": True,
        "evidence": [
            {"document_name": "a.txt", "snippet": "A", "start_char": 0, "end_char": 1},
            {"document_name": "b.txt", "snippet": "B", "start_char": 0, "end_char": 1},
        ],
        "keypoints": [],
    }
    rows = [
        score_question(q1, "", [_result("a.txt", "A", start_char=0, end_char=1)]),
        score_question(q2, "", [_result("a.txt", "A", start_char=0, end_char=1)]),
    ]
    result = aggregate(
        rows,
        [{"retrieval_s": 0, "llm_s": 0, "total_s": 0}] * 2,
    )
    assert result["aggregate"]["document_hit_rate"] == 1.0
    assert result["aggregate"]["document_recall"] == 0.75
    assert result["aggregate"]["all_gold_docs_recalled_rate"] == 0.5
    assert result["aggregate"]["multi_document_complete_recall_rate"] == 0.0


def test_retrieval_sweep_has_top1_score_status_and_optional_trace_metrics():
    questions = [
        {
            "id": "a1",
            "category": "fact",
            "answerable": True,
            "evidence": [{"document_name": "doc.txt", "snippet": "gold"}],
        },
        {
            "id": "u1",
            "category": "unanswerable",
            "answerable": False,
            "evidence": [],
        },
    ]
    retrievals = {
        "a1": [_result("doc.txt", "gold", distance=0.1, reranker_score=1.0)],
        "u1": [_result("noise.txt", "noise", distance=0.9, reranker_score=0.2)],
    }
    traces = {
        "a1": {
            "chunks": {"c1": {"document_name": "doc.txt", "content": "gold"}},
            "stages": [
                {"name": "dense", "candidates": [{"chunk_id": "c1"}]},
                {"name": "rerank", "candidates": []},
            ],
            "timings_ms": {
                "query_embedding": 10,
                "dense_retrieval": 2,
                "sparse_retrieval": 3,
                "rrf": 1,
                "rerank": 4,
                "total": 20,
            },
        }
    }
    result = build_candidate_result(
        candidate_k=15,
        questions=questions,
        retrievals=retrievals,
        timings_s=[0.1, 0.2],
        traces=traces,
    )
    assert result["document_hit_rate"] == 1.0
    assert result["unanswerable_llm_entry_rate"] == 1.0
    assert result["false_refusal_gate_rate"] == 0.0
    assert result["unanswerable_top1_rrf_score"]["available"] is False
    assert result["score_separation"]["distance"]["available"] is True
    assert result["trace_stages"]["dense"]["document_hit_rate"] == 1.0
    assert result["trace_stages"]["dense_raw"]["document_hit_rate"] == 1.0
    assert result["trace_stages"]["dense_eligible"]["document_hit_rate"] == 1.0
    assert result["trace_stages"]["dense"]["all_gold_docs_recalled_rate"] == 1.0
    assert result["trace_stages"]["rerank"]["document_hit_rate"] == 0.0
    assert result["trace_timings_ms"]["total"]["mean"] == 20.0
    assert result["by_split"]["unspecified"]["question_count"] == 2

    direct = aggregate_trace_metrics(questions=questions, traces=traces)
    assert direct["timings_ms"]["query_embedding"]["p95"] == 10.0

    report = render_retrieval_sweep(
        {
            "label": "v3",
            "config": {"reranker_model": "test"},
            "recommended_candidate_k": 15,
            "ingest": {"total_chunks": 1, "ingest_s": 0.1},
            "candidates": [result],
        }
    )
    assert "document Hit@K" in report
    assert "all-gold-docs recalled" in report
    assert "evidence coverage" in report
    assert "不可回答题门控" in report


def test_trace_dense_raw_can_hit_filtered_gold_while_eligible_view_misses():
    question = {
        "id": "a-filtered",
        "category": "fact",
        "answerable": True,
        "evidence": [{"document_name": "gold.txt", "snippet": "gold"}],
    }
    traces = {
        "a-filtered": {
            "chunks": {"gold": {"document_name": "gold.txt", "content": "gold"}},
            "stages": [
                {
                    "name": "dense",
                    "candidates": [
                        {
                            "chunk_id": "gold",
                            "eligible": False,
                            "filtered_reason": "distance",
                        }
                    ],
                }
            ],
        }
    }
    metrics = aggregate_trace_metrics(questions=[question], traces=traces)
    assert metrics["stages"]["dense_raw"]["document_hit_rate"] == 1.0
    assert metrics["stages"]["dense_eligible"]["document_hit_rate"] == 0.0


def test_candidate_recommendation_prefers_corrected_document_quality_over_legacy_exact():
    candidates = [
        {
            "candidate_k": 15,
            # Historical exact metrics look perfect, but corrected document
            # metrics expose a lower-quality candidate.
            "hit_rate": 1.0,
            "recall": 1.0,
            "mrr": 1.0,
            "document_hit_rate": 0.8,
            "document_recall": 0.8,
            "document_mrr": 0.8,
            "all_gold_docs_recalled_rate": 0.8,
            "evidence_coverage": 0.8,
            "latency_s": {"p95": 0.1},
        },
        {
            "candidate_k": 20,
            "hit_rate": 0.5,
            "recall": 0.5,
            "mrr": 0.5,
            "document_hit_rate": 0.9,
            "document_recall": 0.9,
            "document_mrr": 0.9,
            "all_gold_docs_recalled_rate": 0.9,
            "evidence_coverage": 0.9,
            "latency_s": {"p95": 0.2},
        },
    ]
    assert _recommend_candidate(candidates) == 20
