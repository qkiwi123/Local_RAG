"""
eval/metrics.py 纯函数单元测试 — 不依赖 backend、不依赖网络。
"""

from eval.metrics import (
    norm,
    norm_doc_name,
    parse_citations,
    is_refusal,
    match_evidence,
    score_question,
    aggregate,
)


# ─── 归一化 ──────────────────────────────────────────────


class TestNorm:
    def test_removes_whitespace(self):
        assert norm("实现 2-4 倍\n显存压缩") == "实现2-4倍显存压缩"

    def test_nfkc_fullwidth(self):
        # 全角百分号/数字折叠为半角
        assert norm("准确率５８％") == "准确率58%"

    def test_lowercase(self):
        assert norm("Google 提出") == "google提出"


class TestNormDocName:
    def test_strips_brackets_and_extension(self):
        assert norm_doc_name("《AI技术概述_RAG测试文档.txt》") == norm_doc_name(
            "AI技术概述_RAG测试文档"
        )

    def test_extension_case_insensitive(self):
        assert norm_doc_name("报告.PDF") == norm_doc_name("报告")

    def test_strips_markdown_extension(self):
        assert norm_doc_name("《课程大纲.md》") == norm_doc_name("课程大纲")

    def test_strips_powerpoint_extension(self):
        assert norm_doc_name("课件.PPTX") == norm_doc_name("课件")


# ─── 引用解析 ────────────────────────────────────────────


class TestParseCitations:
    def test_standard_marker(self):
        retrieval = [
            {
                "chunk_id": "chunk-1",
                "content": "年假规定",
                "metadata": {"document_name": "员工手册.pdf", "page_number": 3},
            }
        ]
        cites = parse_citations("答案见【来源1】。", retrieval)
        assert cites == [
            {
                "source_id": 1,
                "doc": "员工手册.pdf",
                "page": 3,
                "chunk_id": "chunk-1",
                "valid": True,
            }
        ]

    def test_variants(self):
        assert parse_citations("【来源 5】")[0]["source_id"] == 5

    def test_invalid_source_is_preserved_for_scoring(self):
        cite = parse_citations("伪造来源【来源9】", [])[0]
        assert cite["source_id"] == 9
        assert cite["valid"] is False

    def test_no_marker(self):
        assert parse_citations("没有引用的回答") == []


# ─── 拒答判定 ────────────────────────────────────────────


class TestIsRefusal:
    def test_canned_refusal(self):
        assert is_refusal("未找到与您问题相关的文档内容。请确认已上传相关文档。")

    def test_llm_refusal(self):
        assert is_refusal("根据提供的文档内容，文档中没有关于年终奖的信息。")

    def test_normal_answer(self):
        assert not is_refusal("年假为5天，详见【来源1】。")


# ─── 证据匹配 ────────────────────────────────────────────

RETRIEVAL = [
    {
        "content": "自 1956 年达特茅斯会议正式提出人工智能概念以来……",
        "metadata": {"document_name": "AI概述.txt", "page_number": 1},
        "distance": 0.3,
    },
    {
        "content": "Transformer 于 2017 年由 Google 提出。",
        "metadata": {"document_name": "LLM指南.pdf", "page_number": 3},
        "distance": 0.5,
    },
]


class TestMatchEvidence:
    def test_hit_with_whitespace_diff(self):
        # snippet 无空格，chunk 有空格 → 归一化后应命中
        ev = {"document_name": "AI概述.txt", "snippet": "1956年达特茅斯会议"}
        assert match_evidence(ev, RETRIEVAL) == 0

    def test_doc_name_must_match(self):
        ev = {"document_name": "LLM指南.pdf", "snippet": "1956年达特茅斯会议"}
        assert match_evidence(ev, RETRIEVAL) is None

    def test_rank(self):
        ev = {"document_name": "LLM指南.pdf", "snippet": "2017 年由 Google 提出"}
        assert match_evidence(ev, RETRIEVAL) == 1


# ─── 单题打分 ────────────────────────────────────────────

Q_ANSWERABLE = {
    "id": "q1",
    "question": "人工智能概念何时提出？",
    "category": "numeric",
    "answerable": True,
    "evidence": [{"document_name": "AI概述.txt", "snippet": "1956年达特茅斯会议"}],
    "keypoints": [["1956"], ["达特茅斯"]],
    "reference_answer": "1956年达特茅斯会议。",
}

Q_UNANSWERABLE = {
    "id": "q2",
    "question": "年终奖怎么算？",
    "category": "unanswerable",
    "answerable": False,
    "evidence": [],
    "keypoints": [],
    "reference_answer": "应拒答。",
}


class TestScoreQuestion:
    def test_full_pass(self):
        answer = "1956 年达特茅斯会议提出【来源1】。"
        row = score_question(Q_ANSWERABLE, answer, RETRIEVAL)
        assert row["hit"] is True
        assert row["first_match_rank"] == 1
        assert row["reciprocal_rank"] == 1.0
        assert row["keypoint_coverage"] == 1.0
        assert row["strict_pass"] is True
        assert row["citation_precision"] == 1.0
        assert row["citation_recall"] == 1.0
        # 引用 (AI概述, 1) 存在于检索 metadata → 落地
        assert row["citation_grounded"] == 1

    def test_partial_keypoints(self):
        answer = "是1956年提出的。"
        row = score_question(Q_ANSWERABLE, answer, RETRIEVAL)
        assert row["keypoint_satisfied"] == 1
        assert row["strict_pass"] is False
        # 无引用标记时 precision 为 None（不计入均值），而非 0
        assert row["citation_precision"] is None
        assert row["citation_count"] == 0

    def test_retrieval_miss(self):
        row = score_question(Q_ANSWERABLE, "随便回答", [])
        assert row["hit"] is False
        assert row["reciprocal_rank"] == 0.0

    def test_unanswerable_correct_refusal(self):
        row = score_question(Q_UNANSWERABLE, "文档中没有相关信息。", RETRIEVAL)
        assert row["refusal_correct"] is True
        assert row["citation_fp"] is False

    def test_unanswerable_hallucinated_citation(self):
        answer = "年终奖为3个月工资【来源9】。"
        row = score_question(Q_UNANSWERABLE, answer, RETRIEVAL)
        assert row["refusal_correct"] is False
        assert row["citation_fp"] is True

    def test_wrong_citation_doc(self):
        answer = "1956 年达特茅斯会议【来源2】。"
        row = score_question(Q_ANSWERABLE, answer, RETRIEVAL)
        assert row["citation_precision"] == 0.0  # 引用了非 gold 文档
        assert row["citation_grounded"] == 1  # 但该文档确实在检索结果里 → 落地


# ─── 聚合 ────────────────────────────────────────────────


class TestAggregate:
    def test_mixed_aggregate(self):
        rows = [
            score_question(
                Q_ANSWERABLE, "1956 年达特茅斯会议【来源1】。", RETRIEVAL
            ),
            score_question(Q_UNANSWERABLE, "文档中没有相关信息。", RETRIEVAL),
        ]
        timings = [
            {"retrieval_s": 0.1, "llm_s": 2.0, "total_s": 2.1},
            {"retrieval_s": 0.2, "llm_s": 1.0, "total_s": 1.2},
        ]
        result = aggregate(rows, timings)
        agg = result["aggregate"]
        assert agg["hit_rate"] == 1.0
        assert agg["strict_accuracy"] == 1.0
        assert agg["refusal_accuracy"] == 1.0
        assert agg["false_refusal_rate"] == 0.0
        assert agg["question_count"] == 2
        assert result["latency"]["total"]["mean"] == 1.65
        # 分类别拆分
        assert result["by_category"]["numeric"]["hit_rate"] == 1.0
        assert result["by_category"]["unanswerable"]["refusal_accuracy"] == 1.0

    def test_empty_citation_not_counted_as_zero(self):
        # 两题：一题带正确引用、一题无引用 → precision 应为 1.0（None 不计入均值）
        rows = [
            score_question(
                Q_ANSWERABLE, "1956 达特茅斯【来源1】", RETRIEVAL
            ),
            score_question(Q_ANSWERABLE, "1956 达特茅斯，无引用", RETRIEVAL),
        ]
        agg = aggregate(rows, [{"retrieval_s": 0, "llm_s": 0, "total_s": 0}] * 2)
        assert agg["aggregate"]["citation_precision"] == 1.0
        assert agg["aggregate"]["citation_emission_rate"] == 0.5
