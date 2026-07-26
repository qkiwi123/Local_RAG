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
from backend.services.parsers.constants import PARSER_VERSION


def _result(
    document_name: str,
    page_number: int | None,
    content: str,
    chunk_id: str,
    *,
    document_id: str = "doc-1",
    parser_version: str = PARSER_VERSION,
    content_type: str = "paragraph",
    start_char: int = 0,
    end_char: int | None = None,
    unit_id: str = "unit-1",
    unit_index: int = 0,
    locator_kind: str = "page",
    location_label: str | None = None,
    slide_number: int | None = None,
    heading_path: list[str] | None = None,
) -> dict:
    meta = {
        "document_name": document_name,
        "document_id": document_id,
        "parser_version": parser_version,
        "content_type": content_type,
        "start_char": start_char,
        "end_char": len(content) if end_char is None else end_char,
        "unit_id": unit_id,
        "unit_index": unit_index,
        "locator_kind": locator_kind,
        "location_label": location_label or (
            f"第 {page_number} 页" if page_number is not None else "章节：测试"
        ),
        "heading_path": heading_path or [],
    }
    if page_number is not None:
        meta["page_number"] = page_number
    if slide_number is not None:
        meta["slide_number"] = slide_number

    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": meta,
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

    def test_preview_available_when_metadata_complete_and_enabled(self):
        """metadata 完整 + 开关开启 = preview_available=True。"""
        answer = "【来源1】"
        retrieval_results = [
            _result(
                "doc.pdf", 1, "chunk content", "chunk-1",
                document_id="doc-1",
                parser_version=PARSER_VERSION,
                content_type="paragraph",
                start_char=0,
                end_char=12,
            ),
        ]
        citations = extract_citations(answer, retrieval_results, preview_enabled=True)
        assert len(citations) == 1
        assert citations[0].preview_available is True
        assert citations[0].document_id == "doc-1"
        assert citations[0].parser_version == PARSER_VERSION
        assert citations[0].content_type == "paragraph"
        assert citations[0].start_char == 0
        assert citations[0].end_char == 12

    def test_preview_not_available_when_disabled(self):
        """开关关闭时 preview_available=False 无论 metadata 多完整。"""
        answer = "【来源1】"
        retrieval_results = [
            _result(
                "doc.pdf", 1, "chunk content", "chunk-1",
                document_id="doc-1",
                parser_version=PARSER_VERSION,
                start_char=0,
                end_char=12,
            ),
        ]
        citations = extract_citations(answer, retrieval_results, preview_enabled=False)
        assert len(citations) == 1
        assert citations[0].preview_available is False

    def test_preview_not_available_for_legacy_metadata(self):
        """旧 metadata 缺少必要字段时 preview_available=False。"""
        answer = "【来源1】"
        retrieval_results = [
            {
                "chunk_id": "chunk-1",
                "content": "chunk content",
                "metadata": {"document_name": "doc.pdf", "page_number": 1},
            }
        ]
        citations = extract_citations(answer, retrieval_results, preview_enabled=True)
        assert citations == []

    def test_preview_not_available_with_zero_start_char(self):
        """start_char=0 是合法值，不应被 truthy 判断误判。"""
        answer = "【来源1】"
        retrieval_results = [
            _result(
                "doc.pdf", 1, "chunk content", "chunk-1",
                document_id="doc-1",
                parser_version=PARSER_VERSION,
                start_char=0,
                end_char=12,
            ),
        ]
        citations = extract_citations(answer, retrieval_results, preview_enabled=True)
        assert len(citations) == 1
        assert citations[0].preview_available is True
        assert citations[0].start_char == 0

    def test_excerpt_remains_truncated(self):
        """excerpt 最多 200 字，不包含完整页文本。"""
        long_content = "x" * 300
        answer = "【来源1】"
        retrieval_results = [
            _result(
                "doc.pdf", 1, long_content, "chunk-1",
                document_id="doc-1",
                parser_version=PARSER_VERSION,
                start_char=10,
                end_char=80,
            ),
        ]
        citations = extract_citations(answer, retrieval_results, preview_enabled=True)
        assert len(citations) == 1
        assert len(citations[0].excerpt) == 200

    def test_old_format_citation_is_rejected(self):
        """生产链路不双读旧格式 metadata。"""
        answer = "【来源1】"
        retrieval_results = [
            {
                "chunk_id": "old-chunk",
                "content": "旧格式内容. " * 10,
                "metadata": {"document_name": "old.pdf", "page_number": 5},
                "distance": 0.3,
            },
        ]
        citations = extract_citations(answer, retrieval_results)
        assert citations == []

    def test_section_citation_does_not_fabricate_page_number(self):
        citations = extract_citations(
            "部署步骤见【来源1】",
            [
                _result(
                    "部署指南.md",
                    None,
                    "先安装依赖。",
                    "section-chunk",
                    locator_kind="section",
                    location_label="章节：部署",
                    heading_path=["部署"],
                )
            ],
        )

        assert citations[0].page_number is None
        assert citations[0].location_label == "章节：部署"

    def test_slide_citation_uses_slide_location(self):
        citations = extract_citations(
            "指标定义见【来源1】",
            [
                _result(
                    "课件.pptx",
                    None,
                    "MRR 衡量排序质量。",
                    "slide-chunk",
                    locator_kind="slide",
                    location_label="第 3 张幻灯片",
                    slide_number=3,
                )
            ],
        )

        assert citations[0].slide_number == 3
        assert citations[0].location_label == "第 3 张幻灯片"


class TestFormatAnswerWithCitations:
    def test_formats_with_citations(self):
        from backend.models.schemas import Citation

        answer = "根据公司规定，员工每年享有5天带薪年假。"
        citations = [
            Citation(
                source_id=1,
                chunk_id="doc-1_p3_c0",
                document_name="员工手册",
                location_label="第 3 页",
                page_number=3,
                excerpt="公司规定员工每年享有5天带薪年假。",
            ),
        ]

        formatted = format_answer_with_citations(answer, citations)

        assert answer in formatted
        assert "参考来源" in formatted
        assert "【来源1】" in formatted
        assert "员工手册" in formatted
        assert "第 3 页" in formatted

    def test_formats_without_citations(self):
        answer = "这是一个简单回答。"
        assert format_answer_with_citations(answer, []) == answer
