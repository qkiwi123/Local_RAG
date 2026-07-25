import pytest
from pydantic import ValidationError

from backend.models.document_structure import (
    LocatedBlock,
    ParsedBlock,
    ParsedUnit,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
    make_unit_id,
)


DOCUMENT_ID = "doc-1"
DOCUMENT_NAME = "sample.pdf"
PARSER_VERSION = "structure-v1"


def _page_locator(page_number: int = 1) -> SourceLocator:
    return SourceLocator(
        kind="page",
        unit_index=page_number - 1,
        location_label=f"第 {page_number} 页",
        page_number=page_number,
    )


def _block(
    locator: SourceLocator,
    *,
    sequence: int,
    block_type: str = "paragraph",
    text: str = "正文",
    **kwargs,
) -> ParsedBlock:
    return ParsedBlock(
        block_id=make_block_id(
            DOCUMENT_ID,
            PARSER_VERSION,
            locator,
            sequence,
        ),
        block_type=block_type,
        text=text,
        sequence=sequence,
        **kwargs,
    )


def test_assemble_unit_sorts_blocks_and_builds_exact_offsets():
    locator = _page_locator(2)
    blocks = [
        _block(locator, sequence=1, text="正文"),
        _block(
            locator,
            sequence=0,
            block_type="heading",
            text="标题",
            heading_level=1,
        ),
    ]

    unit = assemble_parsed_unit(
        document_id=DOCUMENT_ID,
        document_name=DOCUMENT_NAME,
        parser_version=PARSER_VERSION,
        locator=locator,
        blocks=blocks,
    )

    assert unit.unit_id == make_unit_id(DOCUMENT_ID, PARSER_VERSION, locator)
    assert unit.canonical_text == "标题\n\n正文"
    assert [block.sequence for block in unit.blocks] == [0, 1]
    assert [(block.start_char, block.end_char) for block in unit.blocks] == [
        (0, 2),
        (4, 6),
    ]
    for block in unit.blocks:
        assert unit.canonical_text[block.start_char:block.end_char] == block.text


def test_assembly_preserves_final_block_text_without_hidden_cleaning():
    locator = _page_locator()
    original_text = "第一行\n第二行"

    unit = assemble_parsed_unit(
        document_id=DOCUMENT_ID,
        document_name=DOCUMENT_NAME,
        parser_version=PARSER_VERSION,
        locator=locator,
        blocks=[_block(locator, sequence=0, text=original_text)],
    )

    assert unit.canonical_text == original_text
    assert unit.blocks[0].text == original_text


def test_unit_and_block_ids_are_deterministic_and_location_sensitive():
    first_page = _page_locator(1)
    second_page = _page_locator(2)

    assert make_unit_id(DOCUMENT_ID, PARSER_VERSION, first_page) == make_unit_id(
        DOCUMENT_ID,
        PARSER_VERSION,
        first_page,
    )
    assert make_unit_id(DOCUMENT_ID, PARSER_VERSION, first_page) != make_unit_id(
        DOCUMENT_ID,
        PARSER_VERSION,
        second_page,
    )
    assert make_block_id(DOCUMENT_ID, PARSER_VERSION, first_page, 0) != make_block_id(
        DOCUMENT_ID,
        PARSER_VERSION,
        first_page,
        1,
    )


def test_duplicate_block_sequence_is_rejected():
    locator = _page_locator()
    blocks = [
        _block(locator, sequence=0, text="第一段"),
        _block(locator, sequence=0, text="第二段"),
    ]

    with pytest.raises(ValueError, match="sequence.*重复"):
        assemble_parsed_unit(
            document_id=DOCUMENT_ID,
            document_name=DOCUMENT_NAME,
            parser_version=PARSER_VERSION,
            locator=locator,
            blocks=blocks,
        )


def test_non_deterministic_block_id_is_rejected():
    locator = _page_locator()
    block = ParsedBlock(
        block_id="random-id",
        block_type="paragraph",
        text="正文",
        sequence=0,
    )

    with pytest.raises(ValueError, match="block_id"):
        assemble_parsed_unit(
            document_id=DOCUMENT_ID,
            document_name=DOCUMENT_NAME,
            parser_version=PARSER_VERSION,
            locator=locator,
            blocks=[block],
        )


def test_block_type_specific_fields_are_validated():
    with pytest.raises(ValidationError, match="heading_level"):
        ParsedBlock(
            block_id="b1",
            block_type="heading",
            text="标题",
            sequence=0,
        )

    with pytest.raises(ValidationError, match="list_level"):
        ParsedBlock(
            block_id="b2",
            block_type="list_item",
            text="列表项",
            sequence=0,
        )

    with pytest.raises(ValidationError, match="table_rows"):
        ParsedBlock(
            block_id="b3",
            block_type="table",
            text="| A |",
            sequence=0,
        )

    with pytest.raises(ValidationError, match="仅 heading"):
        ParsedBlock(
            block_id="b4",
            block_type="paragraph",
            text="正文",
            sequence=0,
            heading_level=1,
        )

    with pytest.raises(ValidationError, match="确定性表示"):
        ParsedBlock(
            block_id="b5",
            block_type="table",
            text="被篡改的表格文本",
            sequence=0,
            table_rows=[["字段", "值"]],
        )


def test_heading_and_list_levels_reject_out_of_range_values():
    with pytest.raises(ValidationError, match="heading_level"):
        ParsedBlock(
            block_id="b1",
            block_type="heading",
            text="标题",
            sequence=0,
            heading_level=7,
        )

    with pytest.raises(ValidationError, match="list_level"):
        ParsedBlock(
            block_id="b2",
            block_type="list_item",
            text="列表项",
            sequence=0,
            list_level=-1,
        )


def test_locator_requires_kind_specific_position_fields():
    with pytest.raises(ValidationError, match="page_number"):
        SourceLocator(
            kind="page",
            unit_index=0,
            location_label="第 1 页",
        )

    with pytest.raises(ValidationError, match="slide_number"):
        SourceLocator(
            kind="slide",
            unit_index=0,
            location_label="第 1 张幻灯片",
        )

    with pytest.raises(ValidationError, match="不能设置 page_number"):
        SourceLocator(
            kind="slide",
            unit_index=0,
            location_label="第 1 张幻灯片",
            page_number=1,
            slide_number=1,
        )


def test_parsed_unit_rejects_corrupted_canonical_text():
    locator = _page_locator()
    valid_unit = assemble_parsed_unit(
        document_id=DOCUMENT_ID,
        document_name=DOCUMENT_NAME,
        parser_version=PARSER_VERSION,
        locator=locator,
        blocks=[_block(locator, sequence=0, text="正文")],
    )
    payload = valid_unit.model_dump()
    payload["canonical_text"] = "被篡改的正文"

    with pytest.raises(ValidationError, match="canonical_text"):
        ParsedUnit.model_validate(payload)


def test_parsed_unit_rejects_corrupted_block_range():
    locator = _page_locator()
    block = _block(locator, sequence=0, text="正文")
    payload = {
        **block.model_dump(),
        "start_char": 1,
        "end_char": 2,
    }

    with pytest.raises(ValidationError, match="切片"):
        ParsedUnit(
            unit_id=make_unit_id(DOCUMENT_ID, PARSER_VERSION, locator),
            document_id=DOCUMENT_ID,
            document_name=DOCUMENT_NAME,
            parser_version=PARSER_VERSION,
            locator=locator,
            blocks=[LocatedBlock.model_validate(payload)],
            canonical_text="正文",
        )
