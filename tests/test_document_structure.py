from backend.models.document_structure import (
    ParsedBlock,
    SourceLocator,
    build_canonical_text,
    make_unit_id,
)


def test_build_canonical_text_and_offsets():
    text, blocks = build_canonical_text([
        ParsedBlock(block_id="b1", block_type="heading", text="Title", sequence=0),
        ParsedBlock(block_id="b2", block_type="paragraph", text="Body", sequence=1),
    ])
    assert text == "Title\n\nBody"
    assert blocks[0].start_char == 0
    assert blocks[0].end_char == 5
    assert blocks[1].start_char == 7
    assert text[blocks[1].start_char:blocks[1].end_char] == "Body"


def test_unit_id_is_deterministic():
    locator = SourceLocator(
        kind="section",
        unit_index=1,
        location_label="Intro",
    )
    assert make_unit_id("doc", "v1", locator) == make_unit_id("doc", "v1", locator)


def test_invalid_heading_level_rejected():
    try:
        ParsedBlock(
            block_id="b",
            block_type="heading",
            text="x",
            sequence=0,
            heading_level=7,
        )
    except ValueError:
        return
    raise AssertionError("invalid heading level accepted")
