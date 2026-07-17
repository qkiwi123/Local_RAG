"""文档上传与管理接口。

入库采用稳定 document_id 和版本化文件名：原始文件名只负责展示，磁盘文件、
向量 metadata、chunk ID 与删除操作全部通过 document_id 对齐。
"""

import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.config import settings
from backend.models.schemas import UploadResponse
from backend.services.chunker import chunk_document
from backend.services.document_store import (
    create_document,
    delete_record,
    get_document,
    list_documents as list_document_records,
    mark_failed,
    mark_ready,
)
from backend.services.parser import parse_document
from backend.services.vector_store import (
    add_chunks,
    delete_document as delete_legacy_vectors,
    delete_document_by_id,
    get_document_list as get_legacy_document_names,
)

router = APIRouter(tags=["upload"])
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
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
    for path in paths:
        _safe_unlink(path, upload_dir)
    if record_created:
        try:
            mark_failed(document_id, str(error))
        except Exception:
            pass


@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """保存文件并完成 parse → chunk → embedding → vector store 入库。"""
    original_name = _validate_filename(file.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}（当前支持: .pdf, .docx, .txt）",
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

        pages = parse_document(
            str(temp_path),
            document_name=original_name,
            document_id=document_id,
        )
        if not pages:
            raise ValueError("文档解析失败：未能提取到任何文本内容")

        chunks = chunk_document(
            pages,
            chunk_size=settings.CHUNK_SIZE,
            overlap=settings.CHUNK_OVERLAP,
        )
        if not chunks:
            raise ValueError("文档切分失败：未能生成任何文本块")

        chunk_count = add_chunks(chunks)
        os.replace(temp_path, final_path)
        mark_ready(document_id, chunk_count)

        return UploadResponse(
            message="上传成功，文档已入库",
            document_id=document_id,
            document_name=original_name,
            chunk_count=chunk_count,
            status="success",
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
async def list_documents():
    """返回新 document_id 记录，同时兼容迁移前的 Chroma 文档。"""
    records = list_document_records(status="ready")
    registered_names = {record["original_name"] for record in records}
    legacy_items = [
        {
            "document_id": None,
            "original_name": name,
            "stored_name": name,
            "size_bytes": None,
            "status": "legacy",
            "chunk_count": None,
            "created_at": None,
        }
        for name in get_legacy_document_names()
        if name not in registered_names
    ]
    items = records + legacy_items
    return {
        # 保留旧字段，旧前端和外部调用仍可读取名称列表。
        "documents": [item["original_name"] for item in items],
        "document_items": items,
    }


def _delete_registered_document(document_id: str) -> dict:
    record = get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")

    if not delete_document_by_id(document_id):
        raise HTTPException(status_code=500, detail="删除向量数据失败")

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
    """兼容旧 API：按展示名删除该名称的所有登记版本或旧 Chroma 数据。"""
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

    upload_dir = Path(settings.UPLOAD_DIR)
    legacy_path = upload_dir / document_name
    if legacy_path.resolve().parent != upload_dir.resolve():
        raise HTTPException(status_code=400, detail="文档名无效")
    _safe_unlink(legacy_path, upload_dir)

    if delete_legacy_vectors(document_name):
        return {"message": f"文档 '{document_name}' 已删除", "status": "success"}
    raise HTTPException(status_code=500, detail=f"删除文档 '{document_name}' 失败")
