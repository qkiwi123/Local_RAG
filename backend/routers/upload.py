"""文档上传与管理接口。

入库采用稳定 document_id 和版本化文件名：原始文件名只负责展示，磁盘文件、
向量 metadata、chunk ID 与删除操作全部通过 document_id 对齐。
"""

import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from backend.config import settings
from backend.models.schemas import (
    DocumentPreview,
    DocumentTagsUpdate,
    StructuredPreviewUnit,
    UploadResponse,
)
from backend.services.chunker import chunk_units
from backend.services.document_store import (
    create_document,
    delete_record,
    get_document,
    list_documents as list_document_records,
    mark_failed,
    mark_ready,
)
from backend.services.embedding import count_tokens
from backend.services.parsers import (
    ADAPTERS,
    PARSER_VERSION,
    parse_document_structure,
)
from backend.services.tag_store import (
    TagValidationError,
    delete_document_tags,
    get_document_tags,
    normalize_tag_path,
    resolve_document_ids,
    set_document_tags,
)
from backend.services.unit_store import (
    delete_document_units,
    list_document_units,
    replace_document_units,
)
from backend.services.vector_store import (
    add_chunks,
    delete_document_by_id,
)

router = APIRouter(tags=["upload"])
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = frozenset(ADAPTERS)
MAX_FILE_SIZE = 50 * 1024 * 1024


def _validate_filename(filename: str) -> str:
    """只接受纯文件名，拒绝绝对路径和目录穿越。"""
    cleaned = filename.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="未提供文件名")
    if Path(cleaned).name != cleaned or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="文件名不能包含路径")
    if cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名无效")
    return cleaned


def _safe_unlink(path: Path, upload_dir: Path) -> None:
    """仅删除 UPLOAD_DIR 直属文件，避免错误路径造成越界删除。"""
    try:
        resolved = path.resolve()
        if resolved.parent == upload_dir.resolve() and resolved.is_file():
            resolved.unlink()
    except OSError:
        # 清理失败不掩盖原始入库异常；failed 记录中仍保留错误供排查。
        pass


def _cleanup_failed_ingest(
    document_id: str,
    upload_dir: Path,
    paths: tuple[Path, ...],
    error: Exception,
    record_created: bool,
) -> None:
    delete_document_by_id(document_id)
    if record_created:
        try:
            delete_document_units(
                document_id,
                db_path=settings.METADATA_DB_PATH,
            )
        except Exception:
            pass
        try:
            delete_document_tags(
                document_id,
                db_path=settings.METADATA_DB_PATH,
            )
        except Exception:
            pass
    for path in paths:
        _safe_unlink(path, upload_dir)
    if record_created:
        try:
            mark_failed(document_id, str(error))
        except Exception:
            pass


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """规范化并去重标签列表；非法路径抛 422。"""
    try:
        return sorted({normalize_tag_path(tag) for tag in (tags or [])})
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    tags: list[str] | None = Form(default=None),
):
    """保存文件并完成 parse → chunk → embedding → vector store 入库。

    tags 为可选的标签路径列表；缺失的标签会自动创建（含父级节点）。
    """
    normalized_tags = _normalize_tags(tags)
    original_name = _validate_filename(file.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        supported = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}（当前支持: {supported}）",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content)} bytes），最大允许 50MB",
        )

    document_id = uuid4().hex
    stored_name = f"{document_id}{suffix}"
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f".{document_id}.uploading{suffix}"
    final_path = upload_dir / stored_name
    record_created = False

    try:
        with open(temp_path, "xb") as output:
            output.write(content)

        create_document(
            document_id=document_id,
            original_name=original_name,
            stored_name=stored_name,
            size_bytes=len(content),
        )
        record_created = True

        parsed_document = parse_document_structure(
            str(temp_path),
            document_id=document_id,
            document_name=original_name,
        )
        units = parsed_document.units
        if not units:
            raise ValueError("文档解析失败：未能提取到任何文本内容")
        upload_warnings = []
        for warning in parsed_document.warnings:
            logger.warning(
                "Document parse warning: document_id=%s code=%s message=%s",
                document_id,
                warning.code,
                warning.message,
            )
            upload_warnings.append(
                {
                    "code": warning.code,
                    "message": warning.message,
                    "location_label": warning.locator.location_label,
                }
            )

        chunks = chunk_units(
            units,
            chunk_size=settings.CHUNK_SIZE,
            overlap=settings.CHUNK_OVERLAP,
            max_tokens=settings.CHUNK_MAX_TOKENS,
            token_counter=count_tokens,
            strategy=settings.CHUNK_STRATEGY,
        )
        if not chunks:
            raise ValueError("文档切分失败：未能生成任何文本块")

        replace_document_units(
            document_id,
            PARSER_VERSION,
            units,
            db_path=settings.METADATA_DB_PATH,
        )
        chunk_count = add_chunks(chunks)
        set_document_tags(
            document_id,
            normalized_tags,
            db_path=settings.METADATA_DB_PATH,
        )
        os.replace(temp_path, final_path)
        mark_ready(document_id, chunk_count)

        return UploadResponse(
            message="上传成功，文档已入库",
            document_id=document_id,
            document_name=original_name,
            chunk_count=chunk_count,
            status="success",
            warnings=upload_warnings,
        )
    except HTTPException as error:
        _cleanup_failed_ingest(
            document_id,
            upload_dir,
            (temp_path, final_path),
            error,
            record_created,
        )
        raise
    except Exception as error:
        logger.exception(
            "Document ingest failed: document_id=%s original_name=%r size_bytes=%d",
            document_id,
            original_name,
            len(content),
        )
        _cleanup_failed_ingest(
            document_id,
            upload_dir,
            (temp_path, final_path),
            error,
            record_created,
        )
        raise HTTPException(status_code=500, detail=f"文档处理失败: {error}") from error


@router.get("/documents")
async def list_documents(
    tags: list[str] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
):
    """返回结构化登记且已就绪的文档（含标签）。

    tags 为可选筛选：与问答检索共享同一套「父级含子孙、多选取并集」语义；
    q 为可选的文档名称模糊搜索（不区分大小写的子串匹配，通配符按字面处理）；
    两者同时提供时取交集。不传则返回全部（向后兼容）。非法标签路径返回 422。
    """
    records = list_document_records(status="ready")
    query = (q or "").strip()
    if query:
        folded_query = query.casefold()
        records = [
            record
            for record in records
            if folded_query in record["original_name"].casefold()
        ]
    if tags:
        try:
            allowed = set(
                resolve_document_ids(
                    tags,
                    db_path=settings.METADATA_DB_PATH,
                )
            )
        except TagValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        records = [
            record for record in records if record["document_id"] in allowed
        ]
    for record in records:
        record["tags"] = get_document_tags(
            record["document_id"],
            db_path=settings.METADATA_DB_PATH,
        )
    return {
        "documents": [item["original_name"] for item in records],
        "document_items": records,
    }


@router.put("/documents/id/{document_id}/tags")
async def update_document_tags(document_id: str, payload: DocumentTagsUpdate):
    """整体替换文档的标签集。"""
    record = get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")
    try:
        normalized = set_document_tags(
            document_id,
            payload.tags,
            db_path=settings.METADATA_DB_PATH,
        )
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "status": "success",
        "document_id": document_id,
        "tags": normalized,
    }


@router.get("/documents/id/{document_id}/preview", response_model=DocumentPreview)
async def preview_document(document_id: str):
    """返回整份文档的结构化内容预览（知识库管理页）。

    不返回文件系统路径、不加载原文件；文档不存在或非 ready 返回 404。
    """
    record = get_document(document_id)
    if not record or record["status"] != "ready":
        raise HTTPException(status_code=404, detail=f"文档不存在或未就绪: {document_id}")
    units = list_document_units(
        document_id,
        PARSER_VERSION,
        db_path=settings.METADATA_DB_PATH,
    )
    if not units:
        raise HTTPException(status_code=404, detail="文档没有可预览的结构化内容")
    return DocumentPreview(
        document_id=document_id,
        document_name=record["original_name"],
        tags=get_document_tags(
            document_id,
            db_path=settings.METADATA_DB_PATH,
        ),
        parser_version=PARSER_VERSION,
        unit_count=len(units),
        units=[
            StructuredPreviewUnit(
                unit_id=unit.unit_id,
                location=unit.locator,
                canonical_text=unit.canonical_text,
                blocks=unit.blocks,
            )
            for unit in units
        ],
    )


def _delete_registered_document(document_id: str) -> dict:
    record = get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")

    if not delete_document_by_id(document_id):
        raise HTTPException(status_code=500, detail="删除向量数据失败")

    delete_document_units(
        document_id,
        db_path=settings.METADATA_DB_PATH,
    )
    upload_dir = Path(settings.UPLOAD_DIR)
    _safe_unlink(upload_dir / record["stored_name"], upload_dir)
    delete_record(document_id)
    return record


@router.delete("/documents/id/{document_id}")
async def delete_document_by_stable_id(document_id: str):
    """按稳定 ID 删除一份具体文档版本。"""
    record = _delete_registered_document(document_id)
    return {
        "message": f"文档 '{record['original_name']}' 已删除",
        "document_id": document_id,
        "status": "success",
    }


@router.delete("/documents/{document_name}")
async def delete_document(document_name: str):
    """兼容按展示名删除历史登记版本（legacy 兼容入口）。"""
    matching = [
        record
        for record in list_document_records(status=None)
        if record["original_name"] == document_name
    ]
    if matching:
        for record in matching:
            _delete_registered_document(record["document_id"])
        return {
            "message": f"文档 '{document_name}' 的 {len(matching)} 个版本已删除",
            "status": "success",
        }

    raise HTTPException(status_code=404, detail=f"文档不存在: {document_name}")
