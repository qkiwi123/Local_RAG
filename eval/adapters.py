"""
管道接缝层 — eval 与 backend 之间唯一的耦合点。

设计约束：
1. 所有 backend import 必须放在函数内部（lazy import）。
   backend 的 Chroma 客户端 / SentenceTransformer / OpenAI 客户端都是模块级单例，
   在 import 时初始化——eval.py 必须先设好环境变量（CHROMA_PERSIST_DIR 等）
   再触发首次 import，否则会写入生产向量库。
2. 向量库通过 _get_store() 一处间接层解析：后续 P0-2 迁移 Milvus 时
   新建 backend/services/milvus_store.py 即自动切换，eval 零改动。
3. answer_question() 逐行复刻 backend/routers/chat.py 的问答链路
   （检索为空时的固定拒答串必须与 chat.py 完全一致，且不调用 LLM）。
"""

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

# 与 backend/routers/chat.py 中检索为空时的回复保持逐字一致
REFUSAL_CANNED = "未找到与您问题相关的文档内容。请确认已上传相关文档，或尝试换个问法。"
_LEGACY_PARSER_SUFFIXES = {".pdf", ".docx", ".txt"}


def _structured_document_id(relative_name: str) -> str:
    digest = hashlib.sha256(relative_name.encode("utf-8")).hexdigest()[:24]
    return f"eval-{digest}"


def _get_store():
    """向量存储模块的间接层：优先使用未来的 milvus_store，回退到 vector_store。"""
    try:
        from backend.services import milvus_store as store  # P0-2 迁移后自动生效
    except ImportError:
        from backend.services import vector_store as store
    return store


def assert_isolation():
    """
    断言环境隔离生效：settings 读到的目录必须与 eval 预设的环境变量一致。

    防御场景：config.py 若被改为 load_dotenv(override=True)，
    .env 会覆盖 eval 预设的环境变量，语料将被写入生产库 data/chroma_db。
    """
    import os
    from backend.config import settings

    expected = os.environ["CHROMA_PERSIST_DIR"]
    actual = settings.CHROMA_PERSIST_DIR
    if Path(actual).resolve() != Path(expected).resolve():
        raise RuntimeError(
            f"环境隔离失效！settings.CHROMA_PERSIST_DIR={actual}，"
            f"预期 {expected}。检查 config.py 的 load_dotenv 是否为 override=False。"
        )
    expected_metadata = os.environ["METADATA_DB_PATH"]
    actual_metadata = settings.METADATA_DB_PATH
    if Path(actual_metadata).resolve() != Path(expected_metadata).resolve():
        raise RuntimeError(
            f"环境隔离失效！settings.METADATA_DB_PATH={actual_metadata}，"
            f"预期 {expected_metadata}。"
        )


def get_run_config() -> dict:
    """快照本次运行的关键配置，写入 report.json 供跨 run 追溯。"""
    from backend.config import settings

    return {
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_query_instruction": settings.EMBEDDING_QUERY_INSTRUCTION,
        "chunk_size": settings.CHUNK_SIZE,
        "chunk_overlap": settings.CHUNK_OVERLAP,
        "chunk_strategy": settings.CHUNK_STRATEGY,
        "chunk_max_tokens": settings.CHUNK_MAX_TOKENS,
        "top_k": settings.RETRIEVAL_TOP_K,
        "retrieval_mode": settings.RETRIEVAL_MODE,
        "retrieval_distance_threshold": settings.RETRIEVAL_DISTANCE_THRESHOLD,
        "bm25_top_k": settings.BM25_TOP_K,
        "bm25_k1": settings.BM25_K1,
        "bm25_b": settings.BM25_B,
        "rrf_k": settings.RRF_K,
        "reranker_enabled": settings.RERANKER_ENABLED,
        "reranker_model": settings.RERANKER_MODEL,
        "reranker_candidate_k": settings.RERANKER_CANDIDATE_K,
        "reranker_device": settings.RERANKER_DEVICE,
        "reranker_batch_size": settings.RERANKER_BATCH_SIZE,
        "reranker_max_length": settings.RERANKER_MAX_LENGTH,
        "llm_model": settings.DEEPSEEK_MODEL,
    }


def parse_corpus(corpus_dir: Path, *, structured: bool = False) -> dict:
    """解析语料，返回 {document_name: 全文文本}，供 dataset snippet 自检。"""
    if structured:
        from backend.services.parsers import ADAPTERS, parse_document_structure

        texts = {}
        for source in sorted(corpus_dir.iterdir()):
            if not source.is_file() or source.suffix.lower() not in ADAPTERS:
                continue
            relative_name = source.relative_to(corpus_dir).as_posix()
            parsed = parse_document_structure(
                str(source),
                document_id=_structured_document_id(relative_name),
                document_name=relative_name,
            )
            texts[relative_name] = "\n\n".join(
                unit.canonical_text for unit in parsed.units
            )
        return texts

    from backend.services.parser import parse_document

    texts = {}
    for f in sorted(corpus_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _LEGACY_PARSER_SUFFIXES:
            continue
        pages = parse_document(str(f))
        texts[f.name] = "".join(p["text"] for p in pages)
    return texts


def ingest_corpus(corpus_dir: Path, *, structured: bool = False) -> dict:
    """
    复刻 upload router 的入库链路：parse → chunk → add_chunks。

    Returns:
        {"documents": {文档名: chunk数}, "total_chunks": int, "ingest_s": float}
    """
    if structured:
        return _ingest_structured_corpus(corpus_dir)

    from backend.config import settings
    from backend.services.parser import parse_document
    from backend.services.chunker import chunk_document
    from backend.services.embedding import count_tokens

    store = _get_store()
    t0 = time.perf_counter()
    doc_chunks = {}
    for f in sorted(corpus_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _LEGACY_PARSER_SUFFIXES:
            continue
        pages = parse_document(str(f))
        chunks = chunk_document(
            pages,
            settings.CHUNK_SIZE,
            settings.CHUNK_OVERLAP,
            max_tokens=settings.CHUNK_MAX_TOKENS,
            token_counter=count_tokens,
            strategy=settings.CHUNK_STRATEGY,
        )
        n = store.add_chunks(chunks)
        doc_chunks[f.name] = n
    return {
        "documents": doc_chunks,
        "total_chunks": sum(doc_chunks.values()),
        "ingest_s": round(time.perf_counter() - t0, 2),
    }


def _ingest_structured_corpus(corpus_dir: Path) -> dict:
    """在隔离库中贯通 structured-v1 的五格式 parse/store/chunk/index。"""
    from backend.config import settings
    from backend.services.chunker import chunk_units
    from backend.services.document_store import (
        create_document,
        mark_failed,
        mark_ready,
    )
    from backend.services.embedding import count_tokens
    from backend.services.metadata_schema import bootstrap_structured_database
    from backend.services.parsers import (
        ADAPTERS,
        PARSER_VERSION,
        parse_document_structure,
    )
    from backend.services.unit_store import replace_document_units

    store = _get_store()
    t0 = time.perf_counter()
    doc_chunks: dict[str, int] = {}
    doc_units: dict[str, int] = {}
    warning_count = 0
    bootstrap_structured_database(
        settings.METADATA_DB_PATH,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    for source in sorted(corpus_dir.iterdir()):
        if not source.is_file() or source.suffix.lower() not in ADAPTERS:
            continue
        relative_name = source.relative_to(corpus_dir).as_posix()
        document_id = _structured_document_id(relative_name)
        create_document(
            document_id,
            relative_name,
            f"{document_id}{source.suffix.lower()}",
            source.stat().st_size,
            settings.METADATA_DB_PATH,
        )
        try:
            parsed = parse_document_structure(
                str(source),
                document_id=document_id,
                document_name=relative_name,
            )
            chunks = chunk_units(
                parsed.units,
                settings.CHUNK_SIZE,
                settings.CHUNK_OVERLAP,
                max_tokens=settings.CHUNK_MAX_TOKENS,
                token_counter=count_tokens,
                strategy=settings.CHUNK_STRATEGY,
            )
            replace_document_units(
                document_id,
                PARSER_VERSION,
                parsed.units,
                db_path=settings.METADATA_DB_PATH,
            )
            count = store.add_chunks(chunks)
            mark_ready(
                document_id,
                count,
                settings.METADATA_DB_PATH,
            )
        except Exception as error:
            mark_failed(
                document_id,
                str(error),
                settings.METADATA_DB_PATH,
            )
            raise
        doc_chunks[relative_name] = count
        doc_units[relative_name] = len(parsed.units)
        warning_count += len(parsed.warnings)

    return {
        "parser_mode": "structured-v1",
        "documents": doc_chunks,
        "units": doc_units,
        "total_units": sum(doc_units.values()),
        "total_chunks": sum(doc_chunks.values()),
        "parse_warnings": warning_count,
        "ingest_s": round(time.perf_counter() - t0, 2),
    }


def answer_question(question: str) -> dict:
    """
    复刻 chat router 的问答链路：retrieve → format → ask。

    Returns:
        {
            "answer": 原始回答文本（拒答短路时为 REFUSAL_CANNED）,
            "retrieval": retrieve() 的原始结果（content/metadata/distance）,
            "empty_retrieval": 是否检索为空短路,
            "retrieval_s" / "llm_s" / "total_s": 各阶段耗时（秒）,
        }
    """
    from backend.services.retriever import retrieve, format_retrieval_context
    from backend.services.llm import ask

    t0 = time.perf_counter()
    results: List[dict] = retrieve(question)
    t_retrieval = time.perf_counter() - t0

    if not results:
        # 与 chat.py 一致：检索为空直接返回固定回复，不调用 LLM
        return {
            "answer": REFUSAL_CANNED,
            "retrieval": [],
            "empty_retrieval": True,
            "retrieval_s": round(t_retrieval, 3),
            "llm_s": 0.0,
            "total_s": round(t_retrieval, 3),
        }

    context = format_retrieval_context(results)
    t1 = time.perf_counter()
    answer = ask(question, context)
    t_llm = time.perf_counter() - t1

    return {
        "answer": answer,
        "retrieval": results,
        "empty_retrieval": False,
        "retrieval_s": round(t_retrieval, 3),
        "llm_s": round(t_llm, 3),
        "total_s": round(time.perf_counter() - t0, 3),
    }


def retrieve_only(question: str) -> dict:
    """只执行检索链路，供 Reranker 候选实验复用，不调用 LLM。"""
    from backend.services.retriever import retrieve

    started = time.perf_counter()
    retrieval = retrieve(question)
    return {
        "retrieval": retrieval,
        "retrieval_s": round(time.perf_counter() - started, 3),
    }
