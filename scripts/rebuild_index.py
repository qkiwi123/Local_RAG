"""从结构化 SQLite 登记的 ready 文档安全重建独立 Chroma 索引。"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.models.document_structure import ParsedDocument, ParsedUnit


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PreparedDocument:
    """单个文档重建产物：登记记录、结构化来源单元和 chunks。"""

    record: dict[str, Any]
    chunks: list[Any]
    parsed_units: list[ParsedUnit]
    warnings: list[Any]


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_ready_documents(metadata_db_path: str | Path) -> list[dict[str, Any]]:
    """读取可重建的文档登记，不修改现有 SQLite。"""
    connection = sqlite3.connect(Path(metadata_db_path))
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


def ensure_safe_target(
    source_index_dir: str | Path,
    target_index_dir: str | Path,
) -> None:
    """拒绝原地重建及覆盖已有目录，确保源索引不被修改。"""
    source = Path(source_index_dir).resolve()
    target = Path(target_index_dir).resolve()
    if source == target:
        raise ValueError("新索引目录不能与旧索引相同")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录非空，拒绝覆盖: {target}")


def _validate_chunks_against_units(
    chunks: list[Any],
    units: list[ParsedUnit],
) -> None:
    unit_by_id = {unit.unit_id: unit for unit in units}
    for chunk in chunks:
        metadata = chunk.metadata
        unit = unit_by_id.get(metadata.unit_id)
        if unit is None:
            raise ValueError(f"chunk 引用了不存在的 unit: {metadata.unit_id}")
        if metadata.document_id != unit.document_id:
            raise ValueError("chunk document_id 与 unit 不一致")
        if metadata.parser_version != unit.parser_version:
            raise ValueError("chunk parser_version 与 unit 不一致")
        start = metadata.start_char
        end = metadata.end_char
        if start < 0 or end <= start or end > len(unit.canonical_text):
            raise ValueError("chunk offset 非法")
        if unit.canonical_text[start:end] != chunk.content:
            raise ValueError("chunk 与 unit canonical_text 切片不一致")
        if metadata.chunk_id != f"{unit.unit_id}_s{start}_e{end}":
            raise ValueError("chunk_id 不符合结构化稳定位置规则")


def prepare_documents(
    records: Iterable[dict[str, Any]],
    upload_dir: str | Path,
    *,
    parse_document: Callable[..., ParsedDocument],
    chunk_document: Callable[..., list[Any]],
    chunk_size: int,
    overlap: int,
    strategy: str | None = None,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    enforce_registered_counts: bool = True,
) -> list[PreparedDocument]:
    """重新解析 ready 文档，并在写入前验证结构化强不变量。"""
    records = list(records)
    upload_dir = Path(upload_dir)
    missing = [
        str(upload_dir / record["stored_name"])
        for record in records
        if not (upload_dir / record["stored_name"]).is_file()
    ]
    if missing:
        raise FileNotFoundError("缺少原始上传文件: " + ", ".join(missing))

    prepared = []
    for record in records:
        source_path = upload_dir / record["stored_name"]
        parsed = parse_document(
            str(source_path),
            document_name=record["original_name"],
            document_id=record["document_id"],
        )
        if not isinstance(parsed, ParsedDocument):
            raise TypeError("parse_document 必须返回 ParsedDocument")
        if not parsed.units:
            raise ValueError(f"文档未解析出来源单元: {record['document_id']}")

        chunk_options = {}
        if strategy is not None:
            chunk_options["strategy"] = strategy
        if max_tokens is not None:
            chunk_options["max_tokens"] = max_tokens
        if token_counter is not None:
            chunk_options["token_counter"] = token_counter
        chunks = chunk_document(
            parsed.units,
            chunk_size,
            overlap,
            **chunk_options,
        )
        if not chunks:
            raise ValueError(f"文档未生成 chunk: {record['document_id']}")
        _validate_chunks_against_units(chunks, parsed.units)

        expected = int(record["chunk_count"])
        actual = len(chunks)
        if enforce_registered_counts and actual != expected:
            raise RuntimeError(
                f"文档 {record['document_id']} 在 SQLite 登记为 {expected} chunks，"
                f"实际生成 {actual} chunks；为避免登记与索引不一致，已终止重建"
            )
        prepared.append(
            PreparedDocument(
                record=record,
                chunks=chunks,
                parsed_units=parsed.units,
                warnings=parsed.warnings,
            )
        )
    return prepared


def update_document_chunk_counts(
    metadata_db_path: str | Path,
    chunk_counts: dict[str, int],
) -> None:
    """在新索引校验成功后，事务化同步 ready 文档的 chunk 数。"""
    connection = sqlite3.connect(Path(metadata_db_path), timeout=10)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        for document_id, chunk_count in chunk_counts.items():
            cursor = connection.execute(
                """
                UPDATE documents
                SET chunk_count = ?, updated_at = ?
                WHERE document_id = ? AND status = 'ready'
                """,
                (chunk_count, timestamp, document_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"无法同步 ready 文档的 chunk 数: {document_id}"
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify_collection(
    collection: Any,
    *,
    expected_counts: dict[str, int],
    expected_dimension: int,
) -> dict[str, Any]:
    """核对总块数、逐文档块数、document_id 和向量维度。"""
    result = collection.get(include=["embeddings", "metadatas"])
    metadatas = result.get("metadatas") or []
    embeddings = result.get("embeddings")
    actual_count = collection.count()
    expected_count = sum(expected_counts.values())
    if actual_count != expected_count or len(metadatas) != expected_count:
        raise RuntimeError(
            f"索引总块数不一致：实际 {actual_count}，期望 {expected_count}"
        )

    chunks_by_document: dict[str, int] = {}
    for metadata in metadatas:
        document_id = (metadata or {}).get("document_id")
        chunks_by_document[document_id] = chunks_by_document.get(document_id, 0) + 1
    if chunks_by_document != expected_counts:
        raise RuntimeError(
            "逐文档 chunk 数或 document_id 不一致："
            f"实际 {chunks_by_document}，期望 {expected_counts}"
        )

    actual_dimension = (
        0 if embeddings is None or len(embeddings) == 0
        else len(embeddings[0])
    )
    if actual_dimension != expected_dimension:
        raise RuntimeError(
            f"向量维度不一致：实际 {actual_dimension}，期望 {expected_dimension}"
        )
    return {
        "chunk_count": actual_count,
        "document_count": len(chunks_by_document),
        "embedding_dimension": actual_dimension,
        "chunks_by_document": chunks_by_document,
    }


def _checked_write_document_units(
    prepared: list[PreparedDocument],
    metadata_db_path: str | Path,
    *,
    expected_counts: dict[str, int],
    expected_dimension: int,
    _fake_collection: Any | None = None,
) -> None:
    """仅在目标 Chroma 全量校验成功后替换结构化来源单元。"""
    if _fake_collection is not None:
        verify_collection(
            _fake_collection,
            expected_counts=expected_counts,
            expected_dimension=expected_dimension,
        )

    from backend.services.parsers.constants import PARSER_VERSION
    from backend.services.unit_store import replace_document_units

    db_path = str(metadata_db_path)
    for item in prepared:
        replace_document_units(
            item.record["document_id"],
            PARSER_VERSION,
            item.parsed_units,
            db_path=db_path,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从结构化 SQLite ready 文档重建独立 Chroma 索引。"
    )
    parser.add_argument("--target-dir", required=True, help="新 Chroma 索引目录")
    parser.add_argument("--source-index-dir", help="源索引目录；默认读取 .env")
    parser.add_argument("--metadata-db", help="SQLite 路径；默认读取 .env")
    parser.add_argument("--upload-dir", help="原始上传目录；默认读取 .env")
    parser.add_argument("--model", help="Embedding 模型；默认读取 .env")
    parser.add_argument("--expected-dimension", type=int, default=1024)
    parser.add_argument("--chunk-size", type=int, help="默认读取 .env 或使用 500")
    parser.add_argument("--chunk-overlap", type=int, help="默认读取 .env 或使用 50")
    parser.add_argument(
        "--chunk-strategy",
        choices=("fixed", "recursive"),
        help="默认读取 .env 或使用 fixed",
    )
    parser.add_argument("--chunk-max-tokens", type=int, help="默认读取 .env 或使用 480")
    parser.add_argument(
        "--accept-chunk-count-change",
        action="store_true",
        help="接受 parser 变化产生的新 chunk 数，并在索引校验后同步 SQLite",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    args = _build_parser().parse_args(argv)
    source_index_dir = _resolve_project_path(
        args.source_index_dir or os.getenv("CHROMA_PERSIST_DIR", "data/chroma_db")
    )
    target_index_dir = _resolve_project_path(args.target_dir)
    metadata_db_path = _resolve_project_path(
        args.metadata_db or os.getenv("METADATA_DB_PATH", "data/metadata.db")
    )
    upload_dir = _resolve_project_path(
        args.upload_dir or os.getenv("UPLOAD_DIR", "data/uploads")
    )
    model = args.model or os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
    chunk_size = args.chunk_size or int(os.getenv("CHUNK_SIZE", "500"))
    overlap = (
        args.chunk_overlap
        if args.chunk_overlap is not None
        else int(os.getenv("CHUNK_OVERLAP", "50"))
    )
    strategy = (args.chunk_strategy or os.getenv("CHUNK_STRATEGY", "fixed")).strip().lower()
    max_tokens = args.chunk_max_tokens or int(os.getenv("CHUNK_MAX_TOKENS", "480"))

    ensure_safe_target(source_index_dir, target_index_dir)
    records = load_ready_documents(metadata_db_path)
    if not records:
        raise RuntimeError(f"SQLite 中没有 ready 文档: {metadata_db_path}")

    os.environ["CHROMA_PERSIST_DIR"] = str(target_index_dir)
    os.environ["METADATA_DB_PATH"] = str(metadata_db_path)
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["EMBEDDING_MODEL"] = model
    # 独立Chroma重建不得通过vector_store的生命周期钩子标脏生产Sparse。
    os.environ["SPARSE_INDEX_ROOT"] = str(target_index_dir / ".sparse-shadow")
    os.environ["SPARSE_BACKEND"] = "rank_bm25_memory"
    os.environ["SPARSE_AUTO_REBUILD"] = "false"

    from backend.services import vector_store
    from backend.services.chunker import chunk_units
    from backend.services.embedding import count_tokens
    from backend.services.parsers import PARSER_VERSION, parse_document_structure
    from backend.services.runtime_settings import RuntimeSettingsManager
    from backend.services.vision import vision_config_from_snapshot
    from functools import partial

    # 重解析与上传共享已保存的视觉配置；一次重建固定使用同一快照。
    vision_config = vision_config_from_snapshot(RuntimeSettingsManager(db_path=str(metadata_db_path)).snapshot())

    prepared = prepare_documents(
        records,
        upload_dir,
        parse_document=partial(parse_document_structure, vision_config=vision_config),
        chunk_document=chunk_units,
        chunk_size=chunk_size,
        overlap=overlap,
        strategy=strategy,
        max_tokens=max_tokens,
        token_counter=count_tokens,
        enforce_registered_counts=not args.accept_chunk_count_change,
    )
    print(
        f"准备重建 {len(prepared)} 份文档、"
        f"{sum(len(item.chunks) for item in prepared)} 个 chunks，模型 {model}"
    )
    for item in prepared:
        added = vector_store.add_chunks(item.chunks)
        if added != len(item.chunks):
            raise RuntimeError(
                f"文档 {item.record['document_id']} 写入数量异常："
                f"{added}/{len(item.chunks)}"
            )
        print(f"已写入 {item.record['original_name']}: {added} chunks")

    registered_counts = {
        record["document_id"]: int(record["chunk_count"])
        for record in records
    }
    actual_counts = {
        item.record["document_id"]: len(item.chunks)
        for item in prepared
    }
    summary = verify_collection(
        vector_store._collection,
        expected_counts=actual_counts,
        expected_dimension=args.expected_dimension,
    )
    _checked_write_document_units(
        prepared,
        metadata_db_path,
        expected_counts=actual_counts,
        expected_dimension=args.expected_dimension,
    )

    changed_counts = {
        document_id: {
            "before": registered_counts[document_id],
            "after": actual_count,
        }
        for document_id, actual_count in actual_counts.items()
        if registered_counts[document_id] != actual_count
    }
    if changed_counts:
        update_document_chunk_counts(metadata_db_path, actual_counts)
    summary.update(
        {
            "parser_version": PARSER_VERSION,
            "unit_count": sum(len(item.parsed_units) for item in prepared),
            "warning_count": sum(len(item.warnings) for item in prepared),
            "model": model,
            "chunk_strategy": strategy,
            "chunk_size": chunk_size,
            "chunk_overlap": overlap,
            "chunk_max_tokens": max_tokens,
            "source_index_dir": str(source_index_dir),
            "target_index_dir": str(target_index_dir),
            "chunk_count_changes": changed_counts,
            "metadata_updated": bool(changed_counts),
        }
    )
    print("索引重建与校验成功：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
