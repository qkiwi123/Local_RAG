from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from backend.services.parsers import PARSER_VERSION
from backend.services.parsers.pptx import parse_pptx_document


def test_pptx_preserves_title_positioned_text_lists_and_table(tmp_path):
    source = tmp_path / "slides.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "检索评测"

    lower = slide.shapes.add_textbox(Inches(1), Inches(3), Inches(4), Inches(1))
    lower.text_frame.paragraphs[0].text = "后出现但位置较下"
    upper = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    upper.text_frame.paragraphs[0].text = "后创建但位置较上"
    nested = upper.text_frame.add_paragraph()
    nested.text = "嵌套项"
    nested.level = 1

    table_shape = slide.shapes.add_table(
        2,
        2,
        Inches(1),
        Inches(4),
        Inches(5),
        Inches(1),
    )
    table_shape.table.cell(0, 0).text = "指标"
    table_shape.table.cell(0, 1).text = "结果"
    table_shape.table.cell(1, 0).text = "MRR"
    table_shape.table.cell(1, 1).text = "0.8"
    presentation.save(source)

    result = parse_pptx_document(
        str(source),
        document_id="doc-slides",
        document_name="课件.pptx",
    )

    assert result.warnings == []
    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.parser_version == PARSER_VERSION
    assert unit.locator.kind == "slide"
    assert unit.locator.slide_number == 1
    assert unit.locator.location_label == "第 1 张幻灯片"
    assert unit.locator.heading_path == ["检索评测"]
    assert [block.block_type for block in unit.blocks] == [
        "heading",
        "paragraph",
        "list_item",
        "paragraph",
        "table",
    ]
    assert [block.text for block in unit.blocks[1:4]] == [
        "后创建但位置较上",
        "嵌套项",
        "后出现但位置较下",
    ]
    assert unit.blocks[2].list_level == 1
    assert unit.blocks[-1].table_rows == [
        ["指标", "结果"],
        ["MRR", "0.8"],
    ]


def test_pptx_image_only_slide_emits_warning_without_fabricated_unit(tmp_path):
    source = tmp_path / "image-only.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    # 生成一个最小图片后插入；解析器只应看到 picture shape 并跳过。
    from PIL import Image

    image_path = tmp_path / "pixel.png"
    Image.new("RGB", (1, 1), "white").save(image_path)
    picture = slide.shapes.add_picture(str(image_path), 0, 0)
    assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
    presentation.save(source)

    result = parse_pptx_document(str(source), document_id="doc-image")

    assert result.units == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.code == "pptx_no_native_text"
    assert warning.locator.slide_number == 1
    assert "未提取到原生文本" in warning.message


def test_pptx_slide_unit_indexes_keep_original_gaps(tmp_path):
    source = tmp_path / "gaps.pptx"
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "第二张有文字"
    presentation.save(source)

    result = parse_pptx_document(str(source), document_id="doc-gaps")

    assert len(result.warnings) == 1
    assert len(result.units) == 1
    assert result.units[0].locator.unit_index == 1
    assert result.units[0].locator.slide_number == 2
