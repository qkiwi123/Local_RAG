import json
import sqlite3

import pytest
from pydantic import ValidationError

from backend.models.document_structure import SourceLocator
from backend.services.document_store import create_document, delete_record
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.unit_store import (
    get_document_unit,
    list_document_units,
    replace_document_units,
)


def _create_record(db_path: str, document_id: str = "doc-1") -> None:
    bootstrap_structured_database(
        db_path,
        created_at="2026-07-26T00:00:00+00:00",
    )
    create_document(
        document_id,
        "指南.docx",
        f"{document_id}.docx",
        123,
        db_path,
    )


def _unit(document_id: str = "doc-1", page_number: int = 1):
    return assemble_unit_from_drafts(
        document_id=document_id,
        document_name="指南.docx",
        locator=SourceLocator(
            kind="virtual_page",
            unit_index=page_number - 1,
            location_label=f"逻辑页 {page_number}",
            page_number=page_number,
            heading_path=["部署"],
        ),
        drafts=[
            BlockDraft(
                block_type="heading",
                text="部署",
                heading_level=1,
            ),
            BlockDraft(
                block_type="paragraph",
                text=f"第 {page_number} 页正文",
            ),
        ],
    )


def test_replace_and_read_revalidates_complete_parsed_unit(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_record(db_path)
    units = [_unit(page_number=2), _unit(page_number=1)]

    replace_document_units("doc-1", PARSER_VERSION, units, db_path=db_path)

    loaded = list_document_units("doc-1", PARSER_VERSION, db_path=db_path)
    assert [unit.locator.unit_index for unit in loaded] == [0, 1]
    assert loaded[0] == units[1]
    assert get_document_unit(
        "doc-1",
        PARSER_VERSION,
        units[0].unit_id,
        db_path=db_path,
    ) == units[0]


def test_replace_removes_stale_units_in_one_version(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_record(db_path)
    replace_document_units(
        "doc-1",
        PARSER_VERSION,
        [_unit(page_number=1), _unit(page_number=2)],
        db_path=db_path,
    )

    replace_document_units(
        "doc-1",
        PARSER_VERSION,
        [_unit(page_number=1)],
        db_path=db_path,
    )

    loaded = list_document_units("doc-1", PARSER_VERSION, db_path=db_path)
    assert len(loaded) == 1
    assert loaded[0].locator.page_number == 1


def test_document_foreign_key_cascades_units(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_record(db_path)
    unit = _unit()
    replace_document_units(
        "doc-1",
        PARSER_VERSION,
        [unit],
        db_path=db_path,
    )

    assert delete_record("doc-1", db_path) is True

    assert get_document_unit(
        "doc-1",
        PARSER_VERSION,
        unit.unit_id,
        db_path=db_path,
    ) is None


def test_replace_rejects_mixed_identity_and_duplicate_indexes(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_record(db_path)
    other = _unit(document_id="doc-other")

    with pytest.raises(ValueError, match="document_id 不一致"):
        replace_document_units(
            "doc-1",
            PARSER_VERSION,
            [other],
            db_path=db_path,
        )

    duplicate_index_payload = _unit(page_number=1).model_dump()
    duplicate_index_payload["locator"]["location_label"] = "同序号的另一个位置"
    duplicate_index_payload["unit_id"] = ""
    from backend.models.document_structure import SourceLocator, make_block_id, make_unit_id

    locator = SourceLocator.model_validate(duplicate_index_payload["locator"])
    duplicate_index_payload["unit_id"] = make_unit_id(
        "doc-1",
        PARSER_VERSION,
        locator,
    )
    for block in duplicate_index_payload["blocks"]:
        block["block_id"] = make_block_id(
            "doc-1",
            PARSER_VERSION,
            locator,
            block["sequence"],
        )

    with pytest.raises(ValueError, match="unit_index 不能重复"):
        replace_document_units(
            "doc-1",
            PARSER_VERSION,
            [_unit(page_number=1), duplicate_index_payload],
            db_path=db_path,
        )


def test_corrupted_structure_json_is_rejected_on_read(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_record(db_path)
    unit = _unit()
    replace_document_units(
        "doc-1",
        PARSER_VERSION,
        [unit],
        db_path=db_path,
    )

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT structure_json FROM document_units WHERE unit_id = ?",
            (unit.unit_id,),
        ).fetchone()
        structure = json.loads(row[0])
        structure["blocks"][0]["start_char"] = 1
        connection.execute(
            "UPDATE document_units SET structure_json = ? WHERE unit_id = ?",
            (json.dumps(structure, ensure_ascii=False), unit.unit_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValidationError, match="切片|offset"):
        get_document_unit(
            "doc-1",
            PARSER_VERSION,
            unit.unit_id,
            db_path=db_path,
        )
