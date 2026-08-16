"""回填 OCR 块结构（标题/列表/原图引用），不触碰 Chroma 与 chunk 文本。

安全模型：
- 默认 dry-run，只打印每份文档将要发生的结构变化；
- 显式传 --apply 才写 SQLite，写前复制 metadata.db 到 --backup-dir；
- 逐 unit 强校验：unit_id 必须不变、canonical_text 必须逐字一致、
  重新切分的 chunk 数必须与登记 chunk_count 一致；
- 任一份文档不一致立即终止，不写任何数据。
"""

from __future__ import annotations

import argparse
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_ready_documents(metadata_db_path: Path) -> list[dict]:
    import sqlite3

    connection = sqlite3.connect(metadata_db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT document_id, original_name, stored_name, chunk_count
            FROM documents
            WHERE status = 'ready'
            ORDER BY created_at
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def parse_prepared(document: dict, upload_dir: Path):
    from backend.services.parsers import PARSER_VERSION, parse_document_structure
    from backend.services.unit_store import list_document_units

    source_path = upload_dir / document["stored_name"]
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少原始文件: {source_path}")
    parsed = parse_document_structure(
        str(source_path),
        document_id=document["document_id"],
        document_name=document["original_name"],
    )
    old_units = list_document_units(
        document["document_id"],
        PARSER_VERSION,
        db_path=os.environ["METADATA_DB_PATH"],
    )
    return parsed, old_units


def units_have_ocr(units) -> bool:
    return any(
        block.attributes.get("ocr") is True
        for unit in units
        for block in unit.blocks
    )


def validate_and_summarize(document: dict, parsed, old_units) -> dict:
    from backend.services.chunker import chunk_units
    from backend.services.embedding import count_tokens
    from backend.config import settings

    new_units = parsed.units
    old_by_id = {unit.unit_id: unit for unit in old_units}
    if len(new_units) != len(old_units):
        raise ValueError(
            f"unit 数量变化: 旧 {len(old_units)}，新 {len(new_units)}"
        )
    for new_unit in new_units:
        old_unit = old_by_id.get(new_unit.unit_id)
        if old_unit is None:
            raise ValueError(f"新 unit 不存在于旧数据: {new_unit.unit_id}")
        if old_unit.canonical_text != new_unit.canonical_text:
            raise ValueError(
                f"canonical_text 发生变化，禁止无版本回填: {new_unit.unit_id}"
            )
        if old_unit.locator != new_unit.locator:
            raise ValueError(f"locator 发生变化: {new_unit.unit_id}")

    chunks = chunk_units(
        new_units,
        settings.CHUNK_SIZE,
        settings.CHUNK_OVERLAP,
        max_tokens=settings.CHUNK_MAX_TOKENS,
        token_counter=count_tokens,
        strategy=settings.CHUNK_STRATEGY,
    )
    if len(chunks) != int(document["chunk_count"]):
        raise ValueError(
            f"chunk 数量变化: 登记 {document['chunk_count']}，重算 {len(chunks)}"
        )

    type_counter = Counter(
        block.block_type for unit in new_units for block in unit.blocks
    )
    image_refs = {
        block.attributes.get("ocr_image_ref")
        for unit in new_units
        for block in unit.blocks
        if isinstance(block.attributes.get("ocr_image_ref"), str)
    }
    return {
        "units": len(new_units),
        "chunks": len(chunks),
        "block_types": dict(type_counter),
        "image_refs": sorted(image_refs),
        "warnings": len(parsed.warnings),
    }


def apply_document(document: dict, parsed) -> None:
    from backend.services.parsers import PARSER_VERSION
    from backend.services.unit_store import replace_document_units

    replace_document_units(
        document["document_id"],
        PARSER_VERSION,
        parsed.units,
        db_path=os.environ["METADATA_DB_PATH"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="回填 OCR 结构：标题/列表/原图引用（不改变 canonical_text，不重建索引）"
    )
    parser.add_argument("--metadata-db", help="默认读取 .env 的 METADATA_DB_PATH")
    parser.add_argument("--upload-dir", help="默认读取 .env 的 UPLOAD_DIR")
    parser.add_argument("--only-document", help="只处理指定 document_id")
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式确认后才写 SQLite；缺省为 dry-run",
    )
    parser.add_argument(
        "--skip-canonical-changes",
        action="store_true",
        help="存在 canonical_text 变化的文档时，跳过这些文档并继续回填其余文档",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    from backend.config import settings

    if not args.apply:
        # dry-run 必须无副作用：跳过 OCR 原图落盘，只校验结构变化。
        settings.OCR_IMAGE_PREVIEW_ENABLED = False
    metadata_db_path = _resolve_project_path(
        args.metadata_db or os.environ.get("METADATA_DB_PATH", "data/metadata.db")
    )
    upload_dir = _resolve_project_path(
        args.upload_dir or os.environ.get("UPLOAD_DIR", "data/uploads")
    )
    os.environ["METADATA_DB_PATH"] = str(metadata_db_path)
    os.environ["UPLOAD_DIR"] = str(upload_dir)

    documents = load_ready_documents(metadata_db_path)
    if args.only_document:
        documents = [
            document
            for document in documents
            if document["document_id"] == args.only_document
        ]
        if not documents:
            raise SystemExit(f"未找到 ready 文档: {args.only_document}")
    if not documents:
        raise SystemExit("没有可回填的 ready 文档")

    print(f"模式：{'APPLY' if args.apply else 'dry-run'}；待处理 {len(documents)} 份文档")
    valid: list[tuple[dict, object]] = []
    errors: list[tuple[dict, str]] = []
    for index, document in enumerate(documents, 1):
        try:
            from backend.config import settings
            from backend.services.parsers import PARSER_VERSION
            from backend.services.unit_store import list_document_units

            old_units = list_document_units(
                document["document_id"],
                PARSER_VERSION,
                db_path=str(metadata_db_path),
            )
            saved_ocr_enabled = settings.OCR_ENABLED
            if not units_have_ocr(old_units):
                # 旧数据没有 OCR 文本：本次回填不引入新 OCR 文本，也不调视觉 API。
                settings.OCR_ENABLED = False
            try:
                parsed, _ = parse_prepared(document, upload_dir)
            finally:
                settings.OCR_ENABLED = saved_ocr_enabled
            summary = validate_and_summarize(document, parsed, old_units)
        except Exception as error:
            errors.append((document, str(error)))
            print(
                f"[{index}/{len(documents)}] {document['original_name']} "
                f"跳过：{error}"
            )
            continue
        valid.append((document, parsed))
        print(
            f"[{index}/{len(documents)}] {document['original_name']} "
            f"units={summary['units']} chunks={summary['chunks']} "
            f"blocks={summary['block_types']} images={len(summary['image_refs'])} "
            f"warnings={summary['warnings']}"
        )

    if errors and not args.skip_canonical_changes:
        print()
        print(f"共 {len(errors)} 份文档未通过安全校验，未写入任何数据：")
        for document, error in errors:
            print(f"  - {document['original_name']}: {error}")
        print("这些文档通常是开启 OCR 后 canonical_text 真实变化；")
        print("请重新上传/重建这些文档，或显式传 --skip-canonical-changes 只回填其余文档。")
        return 2

    if not args.apply:
        print("dry-run 完成，未写入任何数据；确认后加 --apply 执行。")
        return 0

    backup_dir = _resolve_project_path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"metadata-before-ocr-structure-{_now().replace(':', '')}.db"
    shutil.copy2(metadata_db_path, backup_path)
    print(f"已备份 metadata: {backup_path}")

    for document, parsed in valid:
        apply_document(document, parsed)
        print(f"已回填: {document['original_name']}")

    skipped = len(errors)
    print(
        f"完成：{len(valid)} 份文档已回填 structured units（Chroma 未改动）"
        f"{f'；跳过 {skipped} 份' if skipped else ''}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
