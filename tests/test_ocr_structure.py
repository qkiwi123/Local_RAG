"""OCR 结构分类单元测试：类型识别 + 文本零改写不变量。"""

from backend.services.parsers.ocr_structure import classify_ocr_blocks
from backend.services.parsers.text import split_paragraphs


OCR_TEXT = """四、系统维护与更新

系统升级：进入“设置 > 关于手机 > 系统更新”，可检查并下载 OTA 更新包。

·支持 5G 双卡双待（SA/NSA 全频段），Wi-Fi 7。

3.2 通信与网络

·通话中可开启“AI 降噪”，提升嘈杂环境下的通话音质。"""

MIXED_HEADING_PARAGRAPH = (
    "三、核心功能详解\n"
    "3.1 相机与影像\n"
    "·打开相机 App，默认开启 AI 场景增强。"
)

ALL_BULLETS = "·支持 5G 双卡双待\n·支持 Wi-Fi 7"


def _joined(drafts) -> str:
    return "\n\n".join(draft.text for draft in drafts)


def test_classify_preserves_text_exactly():
    drafts = classify_ocr_blocks(OCR_TEXT, ocr_attrs={"ocr": True})

    assert [draft.text for draft in drafts] == split_paragraphs(OCR_TEXT)
    assert _joined(drafts) == OCR_TEXT


def test_classify_headings_and_lists():
    drafts = classify_ocr_blocks(OCR_TEXT, ocr_attrs={"ocr": True})

    types = [draft.block_type for draft in drafts]
    assert types[0] == "heading"
    assert types[1] == "paragraph"
    assert types[2] == "list_item"
    assert types[3] == "heading"
    assert types[4] == "list_item"

    headings = [draft for draft in drafts if draft.block_type == "heading"]
    assert [draft.text for draft in headings] == [
        "四、系统维护与更新",
        "3.2 通信与网络",
    ]


def test_mixed_heading_paragraph_keeps_text_and_records_leading_heading():
    drafts = classify_ocr_blocks(
        MIXED_HEADING_PARAGRAPH,
        ocr_attrs={"ocr": True},
    )

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.block_type == "paragraph"
    assert draft.text == MIXED_HEADING_PARAGRAPH
    assert draft.attributes["ocr_leading_heading_level"] == 1


def test_all_bullet_lines_classify_as_list_item():
    drafts = classify_ocr_blocks(ALL_BULLETS, ocr_attrs={"ocr": True})

    assert [draft.block_type for draft in drafts] == ["list_item"]
    assert _joined(drafts) == ALL_BULLETS


def test_all_blocks_keep_ocr_markers():
    drafts = classify_ocr_blocks(
        OCR_TEXT,
        ocr_attrs={"ocr": True, "vision_kind": "scanned_page"},
    )

    for draft in drafts:
        assert draft.attributes["ocr"] is True
        assert draft.attributes["vision_kind"] == "scanned_page"
        assert draft.attributes["ocr_structure"] == "heuristic-v1"


def test_allow_headings_false_keeps_only_lists_and_paragraphs():
    drafts = classify_ocr_blocks(
        OCR_TEXT,
        ocr_attrs={"ocr": True},
        allow_headings=False,
    )

    assert all(draft.block_type != "heading" for draft in drafts)
    assert any(draft.block_type == "list_item" for draft in drafts)


def test_long_or_sentence_ending_lines_stay_paragraph():
    text = (
        "1.1 这是一个非常长的标题，长度明显超过四十个字符，"
        "因此不应该被识别为标题而是普通段落，这里继续补充一些内容让它更长。\n\n"
        "三、这句话以句号结尾所以不是标题。"
    )

    drafts = classify_ocr_blocks(text, ocr_attrs={"ocr": True})

    assert all(draft.block_type != "heading" for draft in drafts)


def test_split_into_paragraphs_false_preserves_raw_text():
    raw = "图片文字\n\n\n含连续空行和行尾空格  \n·列表项"

    drafts = classify_ocr_blocks(
        raw,
        ocr_attrs={"ocr": True},
        allow_headings=False,
        split_into_paragraphs=False,
    )

    assert len(drafts) == 1
    assert drafts[0].text == raw


def test_table_text_stays_paragraph_this_round():
    text = "问题现象 可能原因 解决方法\n无法开机 电池电量耗尽 连接充电器至少 10 分钟"

    drafts = classify_ocr_blocks(text, ocr_attrs={"ocr": True})

    assert [draft.block_type for draft in drafts] == ["paragraph"]
    assert _joined(drafts) == text
