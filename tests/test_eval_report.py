"""eval/report.py 报告格式测试。"""

from pathlib import Path

from eval.report import METRIC_LABELS, render_compare, write_report_md, write_review_md


EXPECTED_METRICS = [
    ("hit_rate", "检索命中率 hit_rate", "检索 top-k 中至少命中 1 条 gold 证据的可回答题占比"),
    ("recall", "检索召回率 recall", "每道可回答题命中的 gold 证据占该题全部 gold 证据的比例，再取平均"),
    ("mrr", "MRR", "每道可回答题首个命中 chunk 的排名倒数，再取平均"),
    ("keypoint_coverage", "关键点覆盖率", "回答命中的关键点组占全部关键点组的比例，再取平均"),
    ("strict_accuracy", "严格准确率", "全部关键点命中且未拒答的可回答题占比"),
    ("citation_precision", "引用精确率", "有效引用文档中属于 gold 文档的比例，再取平均"),
    ("citation_recall", "引用召回率", "被有效引用的 gold 文档占全部 gold 文档的比例，再取平均"),
    ("citation_emission_rate", "引用输出率", "包含至少一个引用标记的可回答题占比"),
    ("refusal_accuracy", "拒答准确率", "不可回答题中被正确拒答的题目占比"),
    ("false_refusal_rate", "误拒答率(↓)", "可回答题中被判定为拒答的题目占比，越低越好"),
    ("unanswerable_citation_fp", "拒答题伪引用数(↓)", "不可回答题中出现引用标记的题目数量，越低越好"),
]


def _payload(label="test"):
    aggregate = {key: (0 if key == "unanswerable_citation_fp" else 0.5) for key, _, _ in EXPECTED_METRICS}
    return {
        "label": label,
        "timestamp": "2026-07-17T12:00:00",
        "config": {"dataset_version": 1},
        "ingest": {"total_chunks": 1, "ingest_s": 0.1},
        "aggregate": aggregate,
        "latency": {
            "retrieval": {"mean": 0.1, "p50": 0.1, "p95": 0.1},
            "llm": {"mean": 0.2, "p50": 0.2, "p95": 0.2},
            "total": {"mean": 0.3, "p50": 0.3, "p95": 0.3},
        },
        "by_category": {},
        "per_question": [],
    }


def test_summary_table_uses_all_metric_labels_and_meanings(tmp_path):
    assert METRIC_LABELS == EXPECTED_METRICS

    payload = _payload()
    write_report_md(tmp_path, payload, [], {})
    report = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert "| 指标 | 指标含义 | 值 |" in report
    assert "|---|---|---|" in report
    for _, label, meaning in EXPECTED_METRICS:
        assert f"| {label} | {meaning} |" in report

    # compare 仍复用同一份指标定义，但不展示含义列。
    comparison = render_compare(payload, _payload("next"))
    for _, label, _ in EXPECTED_METRICS:
        assert f"| {label} |" in comparison


def test_readme_documents_all_metric_meanings():
    readme = (Path(__file__).parents[1] / "eval" / "README.md").read_text(encoding="utf-8")

    for key, _, meaning in EXPECTED_METRICS:
        assert f"| {key} | {meaning} |" in readme


def test_review_blockquotes_do_not_emit_trailing_spaces(tmp_path):
    payload = _payload()
    payload["per_question"] = [
        {
            "id": "q01",
            "answerable": True,
            "hit": True,
            "keypoint_satisfied": 1,
            "keypoint_total": 1,
            "citation_count": 1,
        }
    ]
    questions = [
        {
            "id": "q01",
            "category": "fact",
            "question": "测试问题",
            "reference_answer": "标准答案",
            "evidence": [],
        }
    ]
    answers = {"q01": {"answer": "第一段\n\n第二段"}}

    write_review_md(tmp_path, payload, questions, answers)
    review = (tmp_path / "review.md").read_text(encoding="utf-8")

    assert ">\n" in review
    assert not any(line.endswith(" ") for line in review.splitlines())


def test_format_retrieval_debug_handles_sparse_only_result():
    from eval.report import _format_retrieval_debug

    row = {
        "chunk_id": "c1",
        "content": "RBAC 访问控制",
        "metadata": {"document_name": "doc.pdf", "page_number": 7},
        "distance": None,
        "bm25_score": 2.3,
        "bm25_rank": 1,
    }

    text = _format_retrieval_debug(row)

    assert "《doc.pdf》第7页" in text
    assert "dist=—" in text
    assert "bm25=2.300" in text


def test_format_retrieval_debug_prefers_explicit_location_label():
    from eval.report import _format_retrieval_debug

    row = {
        "content": "标题",
        "metadata": {
            "document_name": "slides.pptx",
            "location_label": "第 8 张幻灯片 · RAG 评测",
            "slide_number": 8,
            "page_number": 99,
        },
        "distance": 0.2,
    }

    assert "《slides.pptx》第 8 张幻灯片 · RAG 评测" in _format_retrieval_debug(
        row
    )


def test_format_retrieval_debug_uses_slide_fallback():
    from eval.report import _format_retrieval_debug

    row = {
        "content": "标题",
        "metadata": {"document_name": "slides.pptx", "slide_number": 8},
        "distance": 0.2,
    }

    assert "《slides.pptx》第 8 张幻灯片" in _format_retrieval_debug(row)


def test_generated_report_does_not_claim_unmeasured_five_percent_noise(tmp_path):
    payload = _payload()

    write_report_md(tmp_path, payload, [], {})

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "波动 <5%" not in report
    assert "实际波动需通过重复运行测量" in report


def test_readme_does_not_claim_unmeasured_five_percent_noise():
    readme = (
        Path(__file__).parents[1] / "eval" / "README.md"
    ).read_text(encoding="utf-8")

    assert "波动 <5%" not in readme
    assert "实际波动需通过重复运行测量" in readme
