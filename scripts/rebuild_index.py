"""从 SQLite 登记的 ready 文档安全重建独立 Chroma 索引，并补齐页快照。"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PreparedDocument:
    """单个文档重建的全部产出：登记记录、chunks 和解析出的规范页。"""
    record: dict[str, Any]
    chunks: list[Any]
    parsed_pages: list[Any]


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
    strategy: str | None = None,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    enforce_registered_counts: bool = True,
) -> list[PreparedDocument]:
    """解析并切分全部源文件；产出 PreparedDocument（含 chunks 和规范页）。

    每个文档在切分后立即验证全部 chunk/page 切片强不变量，
    验证失败即终止，不等待写入 Chroma。
    """
    from backend.services.source_page import assemble_source_pages, validate_chunks_against_pages

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
        chunk_options = {}
        if strategy is not None:
            chunk_options["strategy"] = strategy
        if max_tokens is not None:
            chunk_options["max_tokens"] = max_tokens
        if token_counter is not None:
            chunk_options["token_counter"] = token_counter
        chunks = chunk_document(pages, chunk_size, overlap, **chunk_options)
        expected = int(record["chunk_count"])
        actual = len(chunks)
        if enforce_registered_counts and actual != expected:
            raise RuntimeError(
                f"文档 {record['document_id']} 在 SQLite 登记为 {expected} chunks，"
                f"实际生成 {actual} chunks；为避免登记与索引不一致，已终止重建"
            )

        # 组装规范页并在写入 Chroma 前校验 chunk/page 切片强不变量
        parsed_pages = assemble_source_pages(pages)
        validate_chunks_against_pages(chunks, parsed_pages)

        prepared.append(PreparedDocument(
            record=record,
            chunks=chunks,
            parsed_pages=parsed_pages,
        ))
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


def _checked_write_page_snapshots(
    prepared: list[PreparedDocument],
    metadata_db_path: str | Path,
    *,
    expected_counts: dict[str, int],
    expected_dimension: int,
    _fake_collection_count: int | None = None,
    _fake_collection_metadatas: list[dict] | None = None,
    _fake_collection_embeddings: list[list[float]] | None = None,
) -> None:
    """在目标 Chroma 通过全量校验后，事务写入当前 parser 版本的页快照。

    校验失败时页表完全不变。仅替换当前 parser 版本，不删除旧版本。
    _fake_collection_* 参数仅用于测试；生产环境由 main() 在调用前完成 verify_collection。
    """
    from backend.services.page_store import replace_document_pages
    from backend.services.parser import PARSER_VERSION

    # 仅通过 _fake_collection_* 参数显式注入时才执行二次校验（测试路径）
    if _fake_collection_count is not None or _fake_collection_metadatas is not None:
        class _Wrapper:
            def __init__(self, count, metadatas, embeddings):
                self._count = count
                self._metadatas = metadatas
                self._embeddings = embeddings

            def count(self):
                return self._count

            def get(self, include):
                return {
                    "embeddings": self._embeddings,
                    "metadatas": self._metadatas,
                }

        verify_collection(
            _Wrapper(
                _fake_collection_count or sum(len(pd.chunks) for pd in prepared),
                _fake_collection_metadatas if _fake_collection_metadatas is not None else [],
                _fake_collection_embeddings if _fake_collection_embeddings is not None else [],
            ),
            expected_counts=expected_counts,
            expected_dimension=expected_dimension,
        )

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_path = str(metadata_db_path)
    for pd in prepared:
        document_id = pd.record["document_id"]
        page_dicts = []
        for page in pd.parsed_pages:
            page_dicts.append({
                "document_id": page.document_id,
                "parser_version": page.parser_version,
                "page_number": page.page_number,
                "parsed_page_text": page.parsed_page_text,
                "content_type": page.content_type,
                "created_at": now_iso,
            })
        replace_document_pages(document_id, PARSER_VERSION, page_dicts, db_path=db_path)


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
    parser.add_argument(
        "--chunk-strategy",
        choices=("fixed", "recursive"),
        help="默认读取 .env 或使用 fixed",
    )
    parser.add_argument(
        "--chunk-max-tokens",
        type=int,
        help="默认读取 .env 或使用 480",
    )
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
    chunk_size = (
        args.chunk_size
        if args.chunk_size is not None
        else int(os.getenv("CHUNK_SIZE", "500"))
    )
    overlap = (
        args.chunk_overlap
        if args.chunk_overlap is not None
        else int(os.getenv("CHUNK_OVERLAP", "50"))
    )
    strategy = (
        args.chunk_strategy or os.getenv("CHUNK_STRATEGY", "fixed")
    ).strip().lower()
    max_tokens = (
        args.chunk_max_tokens
        if args.chunk_max_tokens is not None
        else int(os.getenv("CHUNK_MAX_TOKENS", "480"))
    )

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
    os.environ["CHUNK_STRATEGY"] = strategy
    os.environ["CHUNK_MAX_TOKENS"] = str(max_tokens)

    from backend.services import vector_store
    from backend.services.chunker import chunk_document
    from backend.services.embedding import count_tokens
    from backend.services.parser import parse_document

    prepared = prepare_documents(
        records,
        upload_dir,
        parse_document=parse_document,
        chunk_document=chunk_document,
        chunk_size=chunk_size,
        overlap=overlap,
        strategy=strategy,
        max_tokens=max_tokens,
        token_counter=count_tokens,
        enforce_registered_counts=not args.accept_chunk_count_change,
    )
    print(
        f"准备重建 {len(prepared)} 份文档、"
        f"{sum(len(pd.chunks) for pd in prepared)} 个 chunks，模型 {model}"
    )
    for pd in prepared:
        added = vector_store.add_chunks(pd.chunks)
        if added != len(pd.chunks):
            raise RuntimeError(
                f"文档 {pd.record['document_id']} 写入数量异常：{added}/{len(pd.chunks)}"
            )
        print(f"已写入 {pd.record['original_name']}: {added} chunks")

    registered_counts = {
        record["document_id"]: int(record["chunk_count"])
        for record in records
    }
    actual_counts = {
        pd.record["document_id"]: len(pd.chunks)
        for pd in prepared
    }
    summary = verify_collection(
        vector_store._collection,
        expected_counts=actual_counts,
        expected_dimension=args.expected_dimension,
    )

    # Chroma 全量校验通过后，事务写入页快照
    from backend.services.parser import PARSER_VERSION
    _checked_write_page_snapshots(
        prepared,
        metadata_db_path,
        expected_counts=actual_counts,
        expected_dimension=args.expected_dimension,
    )
    page_count = sum(len(pd.parsed_pages) for pd in prepared)
    summary["parser_version"] = PARSER_VERSION
    summary["page_count"] = page_count

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
