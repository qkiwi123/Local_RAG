"""从显式Chroma/SQLite评测快照安全构建BM25S generation。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _canonical_sha(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    return {
        "files": len(rows),
        "bytes": sum(row["bytes"] for row in rows),
        "sha256": _canonical_sha(rows),
    }


def _resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def _configured_production_paths() -> dict[str, Path]:
    from dotenv import dotenv_values

    values: Mapping[str, Any] = {**dotenv_values(PROJECT_ROOT / ".env"), **os.environ}
    return {
        "chroma": _resolve(values.get("CHROMA_PERSIST_DIR") or "data/chroma_db"),
        "metadata": _resolve(values.get("METADATA_DB_PATH") or "data/metadata.db"),
        "sparse_root": _resolve(
            values.get("SPARSE_INDEX_ROOT") or "data/sparse_index"
        ),
    }


def ensure_safe_paths(
    *,
    source_chroma: str | Path,
    source_metadata: str | Path,
    target_root: str | Path,
    allow_production: bool = False,
    production_confirm: str | None = None,
) -> tuple[Path, Path, Path]:
    source_chroma_path = _resolve(source_chroma)
    source_metadata_path = _resolve(source_metadata)
    target_root_path = _resolve(target_root)
    if not source_chroma_path.is_dir():
        raise FileNotFoundError(f"源Chroma目录不存在：{source_chroma_path}")
    if not source_metadata_path.is_file():
        raise FileNotFoundError(f"源SQLite不存在：{source_metadata_path}")
    if _is_within(target_root_path, source_chroma_path) or _is_within(source_chroma_path, target_root_path):
        raise ValueError("源Chroma与目标Sparse目录不能互相包含")
    if _is_within(source_metadata_path, target_root_path):
        raise ValueError("目标Sparse目录不能覆盖源SQLite所在目录")
    if allow_production:
        # P6-A受控生产入口：三条路径必须与.env生产配置精确匹配，且
        # --production-confirm必须逐字填写生产Chroma绝对路径作为确认值。
        production = _configured_production_paths()
        if source_chroma_path != production["chroma"]:
            raise ValueError(
                "受控生产入口要求--source-chroma与生产CHROMA_PERSIST_DIR精确匹配："
                f"{production['chroma']}"
            )
        if source_metadata_path != production["metadata"]:
            raise ValueError(
                "受控生产入口要求--source-metadata与生产METADATA_DB_PATH精确匹配："
                f"{production['metadata']}"
            )
        if target_root_path != production["sparse_root"]:
            raise ValueError(
                "受控生产入口要求--target-root与生产SPARSE_INDEX_ROOT精确匹配："
                f"{production['sparse_root']}"
            )
        if production_confirm != str(production["chroma"]):
            raise ValueError(
                "--production-confirm必须逐字填写生产Chroma目录绝对路径："
                f"{production['chroma']}"
            )
        return source_chroma_path, source_metadata_path, target_root_path
    production = set(_configured_production_paths().values())
    if any(_is_within(source_chroma_path, path) or _is_within(source_metadata_path, path)
           or _is_within(path, source_chroma_path) for path in production):
        raise ValueError("P2构建命令默认拒绝读取项目生产数据路径；生产构建需--allow-production")
    project_data = (PROJECT_ROOT / "data").resolve()
    if (target_root_path == PROJECT_ROOT or _is_within(target_root_path, project_data)
            or any(_is_within(target_root_path, path) or _is_within(path, target_root_path)
                   for path in production)):
        raise ValueError("P2构建命令拒绝把目标写入项目根目录或data生产目录；生产构建需--allow-production")
    return source_chroma_path, source_metadata_path, target_root_path


def _metadata_evidence(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        quick = connection.execute("PRAGMA quick_check").fetchall()
        if [tuple(row) for row in quick] != [("ok",)]:
            raise RuntimeError("源SQLite quick_check失败")
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT document_id, original_name, stored_name, status, chunk_count
                FROM documents WHERE status = 'ready' ORDER BY document_id
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    return {
        "ready_document_count": len(rows),
        "ready_documents_sha256": _canonical_sha(rows),
        "file_sha256": _sha256_file(path),
        "document_chunk_counts": {row["document_id"]: row["chunk_count"] for row in rows},
    }


def _verify_registered_chunks(chunks, metadata):
    counts = {}
    for row in chunks:
        document_id = str(row["metadata"].get("document_id") or "")
        counts[document_id] = counts.get(document_id, 0) + 1
    if counts != metadata["document_chunk_counts"]:
        raise RuntimeError("Chroma document_id及逐文档chunk数与SQLite ready记录不一致")


def _metadata_fingerprint(path):
    # SQLite WAL也属于源快照，不能只比较主文件。
    return {suffix: _sha256_file(candidate) if candidate.is_file() else None
            for suffix in ("", "-wal") for candidate in [Path(str(path) + suffix)]}


def _install_network_guard(events: list[dict[str, Any]], started: float) -> None:
    def deny(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            events.append({"event": event, "at_s": time.perf_counter() - started})
            raise PermissionError("Sparse索引构建禁止出站网络")

    sys.addaudithook(deny)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建隔离BM25S持久化generation")
    parser.add_argument("--source-chroma", required=True)
    parser.add_argument("--source-metadata", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--oracle-questions", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--generation-id")
    parser.add_argument("--collection", default="knowledge_base")
    parser.add_argument("--parser-version", required=True)
    parser.add_argument("--chunk-strategy", choices=("fixed", "recursive"), required=True)
    parser.add_argument("--chunk-size", type=int, required=True)
    parser.add_argument("--chunk-overlap", type=int, required=True)
    parser.add_argument("--chunk-max-tokens", type=int, required=True)
    parser.add_argument("--k1", type=float, default=1.5)
    parser.add_argument("--b", type=float, default=0.75)
    parser.add_argument("--epsilon", type=float, default=0.25)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--replace-active", action="store_true")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="P6-A受控生产入口：源/目标必须与.env生产配置精确匹配",
    )
    parser.add_argument(
        "--production-confirm",
        help="确认值：必须逐字填写生产Chroma目录绝对路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source_chroma, source_metadata, target_root = ensure_safe_paths(
        source_chroma=args.source_chroma,
        source_metadata=args.source_metadata,
        target_root=args.target_root,
        allow_production=args.allow_production,
        production_confirm=args.production_confirm,
    )
    if args.replace_active and not args.publish:
        raise ValueError("--replace-active只能与--publish一起使用")
    if args.publish and (target_root / "active.json").exists() and not args.replace_active:
        raise FileExistsError("已有active.json；发布新generation需显式--replace-active")
    if args.chunk_size <= 0 or args.chunk_overlap < 0 or args.chunk_overlap >= args.chunk_size:
        raise ValueError("chunk-size/overlap参数无效")
    if args.chunk_max_tokens <= 0:
        raise ValueError("chunk-max-tokens必须大于0")
    oracle_path = _resolve(args.oracle_questions)
    if not oracle_path.is_file():
        raise FileNotFoundError(f"oracle题库不存在：{oracle_path}")
    report_path = _resolve(args.report)
    if report_path.exists():
        raise FileExistsError(f"报告已存在，拒绝覆盖：{report_path}")
    if _is_within(report_path, source_chroma) or report_path == source_metadata:
        raise ValueError("报告不能写入源存储")
    oracle_payload = json.loads(oracle_path.read_text(encoding="utf-8"))
    oracle_questions = oracle_payload.get("questions")
    if not isinstance(oracle_questions, list) or not oracle_questions:
        raise ValueError("oracle题库必须包含非空questions数组")
    if args.allow_production:
        if len(oracle_questions) != 120:
            raise ValueError("生产构建必须提供120道oracle问题")
        if not args.corpus_id.startswith("production-"):
            raise ValueError("生产corpus-id必须以production-开头，禁止复用评测语料标识")
    if any(not isinstance(q, dict) or not isinstance(q.get("question"), str)
           or not q["question"].strip() for q in oracle_questions):
        raise ValueError("oracle问题必须包含非空question文本")
    if _is_within(report_path, target_root) and not _is_within(report_path, target_root / "reports"):
        raise ValueError("目标索引内的报告只能写入reports子目录")
    production_paths = _configured_production_paths()
    if report_path == PROJECT_ROOT / ".env" or any(
        _is_within(report_path, path) for key, path in production_paths.items()
        if key != "sparse_root" or not args.allow_production
    ):
        raise ValueError("报告不能写入生产源或配置路径")

    started = time.perf_counter()
    network_events: list[dict[str, Any]] = []
    _install_network_guard(network_events, started)
    os.environ.update(
        {
            "ANONYMIZED_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PREWARM_MODELS": "false",
        }
    )
    source_before = {
        "chroma": _tree_fingerprint(source_chroma),
        "metadata": _metadata_fingerprint(source_metadata),
    }
    metadata = _metadata_evidence(source_metadata)
    target_root.mkdir(parents=True, exist_ok=True)
    snapshot_parent = Path(tempfile.mkdtemp(prefix=".source-snapshot-", dir=target_root))
    client = None
    try:
        snapshot_chroma = snapshot_parent / "chroma"
        shutil.copytree(source_chroma, snapshot_chroma)
        os.environ["CHROMA_PERSIST_DIR"] = str(snapshot_chroma)
        os.environ["METADATA_DB_PATH"] = str(source_metadata)
        os.environ["UPLOAD_DIR"] = str(snapshot_parent / "uploads")

        import chromadb
        from backend.services.bm25s_store import (
            TOKENIZER_CONTRACT,
            activate_generation,
            build_generation,
        )
        from backend.services.sparse_tokenizer import tokenize

        client = chromadb.PersistentClient(path=str(snapshot_chroma))
        collection = client.get_collection(args.collection)
        payload = collection.get(include=["documents", "metadatas"])
        chunks = [
            {"chunk_id": chunk_id, "content": content, "metadata": item_metadata or {}}
            for chunk_id, content, item_metadata in zip(
                payload.get("ids") or [],
                payload.get("documents") or [],
                payload.get("metadatas") or [],
            )
        ]
        document_ids = {
            str(row["metadata"].get("document_id") or "") for row in chunks
        }
        document_ids.discard("")
        if len(chunks) != collection.count():
            raise RuntimeError("Chroma get结果与collection.count不一致")
        if len(document_ids) != metadata["ready_document_count"]:
            raise RuntimeError("Chroma文档数与SQLite ready文档数不一致")
        _verify_registered_chunks(chunks, metadata)
        # 取完内存快照后立即停止唯一的Chroma客户端，避免Windows文件锁阻止清理。
        client._system.stop()
        client = None
        source_contract = {
            "chroma_collection": args.collection,
            "chroma_chunk_count": len(chunks),
            "chroma_source_sha256": source_before["chroma"]["sha256"],
            "metadata_ready_documents_sha256": metadata["ready_documents_sha256"],
            "metadata_file_sha256": metadata["file_sha256"],
        }
        result = build_generation(
            chunks=chunks,
            target_root=target_root,
            corpus_id=args.corpus_id,
            tokenize=tokenize,
            tokenizer={
                "contract": TOKENIZER_CONTRACT,
                "jieba_version": __import__("jieba").__version__,
                "user_dictionary_sha256": None,
                "stopwords_sha256": None,
            },
            chunking={
                "parser_version": args.parser_version,
                "strategy": args.chunk_strategy,
                "size": args.chunk_size,
                "overlap": args.chunk_overlap,
                "max_tokens": args.chunk_max_tokens,
            },
            source=source_contract,
            k1=args.k1,
            b=args.b,
            epsilon=args.epsilon,
            generation_id=args.generation_id,
            # 源存储最终指纹复核之前只构建，不提前切换活动指针。
            publish=False,
            replace_active=False,
            oracle_questions=oracle_questions,
        )
    finally:
        if client is not None:
            client._system.stop()
        shutil.rmtree(snapshot_parent)
    source_after = {
        "chroma": _tree_fingerprint(source_chroma),
        "metadata": _metadata_fingerprint(source_metadata),
    }
    if source_before != source_after:
        raise RuntimeError("构建期间源Chroma或SQLite发生变化")
    if network_events:
        raise RuntimeError(f"构建期间发生出站网络尝试：{network_events}")
    if args.publish:
        activate_generation(
            target_root,
            result["generation_id"],
            allow_replace=args.replace_active,
        )
        result["published"] = True
    result.update(
        {
            "status": "passed",
            "source_unchanged": True,
            "network_events": [],
            "source_chroma": str(source_chroma),
            "source_metadata": str(source_metadata),
            "target_root": str(target_root),
            "finished_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "total_wall_s": time.perf_counter() - started,
            "oracle_questions": str(oracle_path),
            "oracle_questions_sha256": _sha256_file(oracle_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
