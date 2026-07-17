"""
测试 backend/utils/citation.py — 基于 source ID 的可信引用提取。

覆盖：
- 【来源N】按本次检索位置映射真实 metadata
- 重复来源去重
- 不存在的来源编号不会生成伪 Citation
- 无引用标记时不再回退到检索 Top-K
- Markdown 格式化
"""

from backend.utils.citation import extract_citations, format_answer_with_citations


def _result(
    document_name: str,
    page_number: int,
    content: str,
    chunk_id: str,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "document_name": document_name,
            "page_number": page_number,
        },
        "distance": 0.2,
    }


class TestExtractCitations:
    """引用提取必须由来源编号确定性映射，不能信任模型拼写的文档名。"""

    def test_maps_source_id_to_retrieval_result(self):
        answer = "员工每年享有5天带薪年假。【来源1】"
        retrieval_results = [
            _result(
                "员工手册.pdf",
                3,
                "公司规定员工每年享有5天带薪年假。",
                "doc-1_p3_c0",
            )
        ]

        citations = extract_citations(answer, retrieval_results)

        assert len(citations) == 1
        assert citations[0].source_id == 1
        assert citations[0].chunk_id == "doc-1_p3_c0"
        assert citations[0].document_name == "员工手册.pdf"
        assert citations[0].page_number == 3
        assert "5天带薪年假" in citations[0].excerpt

    def test_deduplicates_same_source_id(self):
        answer = "结论一【来源1】；再次说明【来源1】。"
        retrieval_results = [_result("员工手册", 3, "测试内容", "chunk-1")]

        citations = extract_citations(answer, retrieval_results)

        assert len(citations) == 1

    def test_handles_multiple_sources_in_answer_order(self):
        answer = "加班政策【来源2】，年假政策【来源1】。"
        retrieval_results = [
            _result("员工手册", 3, "年假相关", "chunk-1"),
            _result("劳动法规", 5, "加班相关", "chunk-2"),
        ]

        citations = extract_citations(answer, retrieval_results)

        assert [c.source_id for c in citations] == [2, 1]
        assert [c.document_name for c in citations] == ["劳动法规", "员工手册"]

    def test_no_marker_has_no_fallback(self):
        answer = "员工每年有5天带薪年假。"
        retrieval_results = [
            _result("员工手册", 3, "公司规定员工每年享有5天带薪年假。", "chunk-1")
        ]

        assert extract_citations(answer, retrieval_results) == []

    def test_invalid_source_id_is_rejected(self):
        answer = "模型编造了一个不存在的来源。【来源9】"
        retrieval_results = [_result("员工手册", 3, "测试内容", "chunk-1")]

        assert extract_citations(answer, retrieval_results) == []

    def test_zero_source_id_is_rejected(self):
        answer = "【来源0】"
        retrieval_results = [_result("员工手册", 3, "测试内容", "chunk-1")]

        assert extract_citations(answer, retrieval_results) == []

    def test_legacy_free_form_marker_is_not_trusted(self):
        answer = "详见模型自行拼写的【员工手册，第3页】。"
        retrieval_results = [_result("员工手册", 3, "测试内容", "chunk-1")]

        assert extract_citations(answer, retrieval_results) == []

    def test_missing_metadata_is_rejected(self):
        answer = "【来源1】"
        retrieval_results = [{"content": "测试", "metadata": {}, "distance": 0.2}]

        assert extract_citations(answer, retrieval_results) == []

    def test_empty_answer(self):
        assert extract_citations("", []) == []


class TestFormatAnswerWithCitations:
    def test_formats_with_citations(self):
        from backend.models.schemas import Citation

        answer = "根据公司规定，员工每年享有5天带薪年假。"
        citations = [
            Citation(
                source_id=1,
                chunk_id="doc-1_p3_c0",
                document_name="员工手册",
                page_number=3,
                excerpt="公司规定员工每年享有5天带薪年假。",
            ),
        ]

        formatted = format_answer_with_citations(answer, citations)

        assert answer in formatted
        assert "参考来源" in formatted
        assert "【来源1】" in formatted
        assert "员工手册" in formatted
        assert "第3页" in formatted

    def test_formats_without_citations(self):
        answer = "这是一个简单回答。"
        assert format_answer_with_citations(answer, []) == answer
