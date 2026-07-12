"""
测试 backend/utils/citation.py — 引用来源提取。

覆盖：
- 正则匹配正确提取 【文档名，第X页】 格式
- 多个引用去重
- 无引用标记时的回退行为
- 格式化输出 Markdown
"""

import pytest
from backend.utils.citation import (
    extract_citations,
    format_answer_with_citations,
)


class TestExtractCitations:
    """引用提取测试。"""

    def test_extracts_standard_format(self):
        """
        标准格式 【文档名，第X页】 应被正确提取。

        注意：正则写的是 【文档名，第X页】（中文逗号 + 可选的"第"和"页"），
        且 LLM 输出的文档名可能包含/不包含书名号。
        测试覆盖实际 LLM 可能输出的格式。
        """
        answer = "根据《员工手册》第3页，员工每年享有5天带薪年假。【员工手册，第3页】"
        retrieval_results = [
            {
                "content": "公司规定员工每年享有5天带薪年假。",
                "metadata": {
                    "document_name": "员工手册",
                    "page_number": 3,
                },
                "distance": 0.2,
            },
        ]

        citations = extract_citations(answer, retrieval_results)

        assert len(citations) >= 1
        assert citations[0].document_name == "员工手册"
        assert citations[0].page_number == 3

    def test_deduplicates_same_citation(self):
        """同一文档 + 同一页码的重复引用应去重。"""
        answer = "【员工手册，第3页】...【员工手册，第3页】"
        retrieval_results = [
            {
                "content": "测试内容",
                "metadata": {"document_name": "员工手册", "page_number": 3},
                "distance": 0.2,
            },
        ]

        citations = extract_citations(answer, retrieval_results)
        assert len(citations) == 1  # 去重后只有 1 条

    def test_handles_multiple_sources(self):
        """多个不同来源应全部被提取。"""
        answer = (
            "年假规定请参见【员工手册，第3页】。"
            "加班政策请参见【劳动法规，第5页】。"
        )
        retrieval_results = [
            {
                "content": "年假相关...",
                "metadata": {"document_name": "员工手册", "page_number": 3},
                "distance": 0.2,
            },
            {
                "content": "加班相关...",
                "metadata": {"document_name": "劳动法规", "page_number": 5},
                "distance": 0.3,
            },
        ]

        citations = extract_citations(answer, retrieval_results)

        assert len(citations) == 2
        doc_names = [c.document_name for c in citations]
        assert "员工手册" in doc_names
        assert "劳动法规" in doc_names

    def test_fallback_when_no_citation_markers(self):
        """
        当 LLM 没有生成引用标记时，
        应使用回退策略：附上所有检索来源。
        """
        answer = "员工每年有5天带薪年假。"  # 没有 【】 格式的引用
        retrieval_results = [
            {
                "content": "公司规定员工每年享有5天带薪年假。",
                "metadata": {"document_name": "员工手册", "page_number": 3},
                "distance": 0.2,
            },
            {
                "content": "年假须提前一周申请。",
                "metadata": {"document_name": "员工手册", "page_number": 5},
                "distance": 0.3,
            },
        ]

        citations = extract_citations(answer, retrieval_results)

        # 回退：应返回检索结果中的来源
        assert len(citations) >= 1

    def test_handles_llm_variations(self):
        """
        测试 LLM 可能输出的多种引用格式变体：
        - 《文档名》，第X页  ← LLM 可能在正文中这样写
        - 正则只匹配 【...】 格式 → 需要 LLM 在末尾用该格式明确标记
        """
        answer = "详见【employee_handbook.pdf，第1页】。"
        retrieval_results = [
            {
                "content": "5 days of paid annual leave.",
                "metadata": {"document_name": "employee_handbook.pdf", "page_number": 1},
                "distance": 0.1,
            },
        ]

        citations = extract_citations(answer, retrieval_results)

        # 正则能匹配到引用标记
        assert len(citations) >= 1
        assert citations[0].page_number == 1

    def test_clean_empty_answer(self):
        """空回答不应报错，返回空引用列表。"""
        citations = extract_citations("", [])
        assert citations == []


class TestFormatAnswerWithCitations:
    """引用格式化测试。"""

    def test_formats_with_citations(self):
        """有引用时应包含参考来源区域。"""
        from backend.models.schemas import Citation

        answer = "根据公司规定，员工每年享有5天带薪年假。"
        citations = [
            Citation(
                document_name="员工手册",
                page_number=3,
                excerpt="公司规定员工每年享有5天带薪年假。",
            ),
        ]

        formatted = format_answer_with_citations(answer, citations)

        assert answer in formatted
        assert "参考来源" in formatted
        assert "员工手册" in formatted
        assert "第3页" in formatted

    def test_formats_without_citations(self):
        """无引用时直接返回原回答。"""
        answer = "这是一个简单回答。"
        formatted = format_answer_with_citations(answer, [])
        assert formatted == answer
