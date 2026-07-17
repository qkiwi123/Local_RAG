"""从 SQLite 登记的 ready 文档安全重建独立 Chroma 索引。"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def ensure_safe_target(source_index_dir: str | Path, target_index_dir: str | Path) -> None:
    """拒绝原地重建及覆盖已有目录，确保旧索引始终可回滚。"""
    source = Path(source_index_dir).resolve()
    target = Path(target_index_dir).resolve()
    if source == target:
        raise ValueError("新索引目录不能与旧索引相同")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录非空，拒绝覆盖: {target}")


def prepare_documents(
    records: Iterable[dict[str, Any]],
    upload_dir: str | Path,
    *,
    parse_document: Callable[..., list[dict]],
    chunk_document: Callable[..., list[Any]],
    chunk_size: int,
    overlap: int,
) -> list[tuple[dict[str, Any], list[Any]]]:
    """解析并切分全部源文件；块数不一致时在写向量前终止。"""
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
        pages = parse_document(
            str(source_path),
            document_name=record["original_name"],
            document_id=record["document_id"],
        )
        chunks = chunk_document(pages, chunk_size, overlap)
        expected = int(record["chunk_count"])
        actual = len(chunks)
        if actual != expected:
            raise RuntimeError(
                f"文档 {record['document_id']} 在 SQLite 登记为 {expected} chunks，"
                f"实际生成 {actual} chunks；为避免登记与索引不一致，已终止重建"
            )
        prepared.append((record, chunks))
    return prepared


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

    if embeddings is None or len(embeddings) == 0:
        actual_dimension = 0
    else:
        actual_dimension = len(embeddings[0])
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 SQLite ready 文档重建独立 Chroma 索引，不覆盖旧索引。"
    )
    parser.add_argument("--target-dir", required=True, help="新 Chroma 索引目录")
    parser.add_argument("--source-index-dir", help="旧索引目录；默认读取 .env")
    parser.add_argument("--metadata-db", help="SQLite 路径；默认读取 .env")
    parser.add_argument("--upload-dir", help="原始上传目录；默认读取 .env")
    parser.add_argument("--model", help="Embedding 模型；默认读取 .env")
    parser.add_argument("--expected-dimension", type=int, default=1024)
    parser.add_argument("--chunk-size", type=int, help="默认读取 .env 或使用 500")
    parser.add_argument("--chunk-overlap", type=int, help="默认读取 .env 或使用 50")
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
    overlap = args.chunk_overlap or int(os.getenv("CHUNK_OVERLAP", "50"))

    ensure_safe_target(source_index_dir, target_index_dir)
    records = load_ready_documents(metadata_db_path)
    if not records:
        raise RuntimeError(f"SQLite 中没有 ready 文档: {metadata_db_path}")

    # config.py 在导入时创建单例，因此必须先覆盖目标路径和模型，再延迟导入后端。
    os.environ["CHROMA_PERSIST_DIR"] = str(target_index_dir)
    os.environ["METADATA_DB_PATH"] = str(metadata_db_path)
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["EMBEDDING_MODEL"] = model
    os.environ["CHUNK_SIZE"] = str(chunk_size)
    os.environ["CHUNK_OVERLAP"] = str(overlap)

    from backend.services import vector_store
    from backend.services.chunker import chunk_document
    from backend.services.parser import parse_document

    prepared = prepare_documents(
        records,
        upload_dir,
        parse_document=parse_document,
        chunk_document=chunk_document,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    print(
        f"准备重建 {len(prepared)} 份文档、"
        f"{sum(len(chunks) for _, chunks in prepared)} 个 chunks，模型 {model}"
    )
    for record, chunks in prepared:
        added = vector_store.add_chunks(chunks)
        if added != len(chunks):
            raise RuntimeError(
                f"文档 {record['document_id']} 写入数量异常：{added}/{len(chunks)}"
            )
        print(f"已写入 {record['original_name']}: {added} chunks")

    expected_counts = {
        record["document_id"]: int(record["chunk_count"])
        for record in records
    }
    summary = verify_collection(
        vector_store._collection,
        expected_counts=expected_counts,
        expected_dimension=args.expected_dimension,
    )
    summary.update(
        {
            "model": model,
            "source_index_dir": str(source_index_dir),
            "target_index_dir": str(target_index_dir),
        }
    )
    print("索引重建与校验成功：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
