"""
文档上传接口 — POST /api/upload

接收用户上传的文件，走完完整入库链路：
保存文件 → 解析 → 切分 → 向量化 → 存入 Chroma

路由层保持轻薄：只做参数校验和调用 services，业务逻辑全部在 services 中。
"""

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from backend.config import settings
from backend.models.schemas import UploadResponse
from backend.services.parser import parse_document
from backend.services.chunker import chunk_document
from backend.services.embedding import embed_texts
from backend.services.vector_store import add_chunks

router = APIRouter(tags=["upload"])

# 允许上传的文件类型
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# 文件大小上限：50MB
MAX_FILE_SIZE = 50 * 1024 * 1024


@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """
    上传文档并走完入库链路。

    流程：
    1. 校验文件类型（只允许 pdf / docx / txt）
    2. 校验文件大小（不超过 50MB）
    3. 保存文件到 data/uploads/
    4. 调用 parser → chunker → embedding → vector_store 完成入库
    5. 返回 UploadResponse（状态、文档名、块数量）
    """
    # ── 1. 校验文件类型 ──
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}（当前支持: .pdf, .docx, .txt）"
        )

    # ── 2. 校验文件大小（先读取全部内容）──
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content)} bytes），最大允许 50MB"
        )

    # ── 3. 保存文件到本地 ──
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 处理文件名冲突：重名文件追加数字后缀
    file_path = upload_dir / file.filename
    counter = 1
    while file_path.exists():
        stem = Path(file.filename).stem
        file_path = upload_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    with open(file_path, "wb") as f:
        f.write(content)

    # ── 4. 入库链路：parser → chunker → embedding → vector_store ──
    try:
        # 4a. 解析文档
        pages = parse_document(str(file_path), document_name=file.filename)

        if not pages:
            raise HTTPException(
                status_code=500,
                detail="文档解析失败：未能提取到任何文本内容"
            )

        # 4b. 文本切分
        chunks = chunk_document(
            pages,
            chunk_size=settings.CHUNK_SIZE,
            overlap=settings.CHUNK_OVERLAP,
        )

        if not chunks:
            raise HTTPException(
                status_code=500,
                detail="文档切分失败：未能生成任何文本块"
            )

        # 4c + 4d: 向量化 + 存入 Chroma（add_chunks 内部调用 embedding）
        chunk_count = add_chunks(chunks)

        return UploadResponse(
            message="上传成功，文档已入库",
            document_name=file.filename,
            chunk_count=chunk_count,
            status="success",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"文档处理失败: {str(e)}"
        )


@router.get("/documents")
async def list_documents():
    """
    获取已上传文档列表。

    GET /api/documents → 返回所有已入库的文档名列表。
    供前端侧边栏展示"已上传文档"。
    """
    from backend.services.vector_store import get_document_list
    documents = get_document_list()
    return {"documents": documents}


@router.delete("/documents/{document_name}")
async def delete_document(document_name: str):
    """
    删除文档（同时删除磁盘文件 + Chroma 向量数据）。

    DELETE /api/documents/{document_name}
    """
    from backend.services.vector_store import delete_document as delete_from_chroma

    # 1. 删除磁盘上的原始文件
    upload_dir = Path(settings.UPLOAD_DIR)
    file_path = upload_dir / document_name
    if file_path.exists():
        file_path.unlink()

    # 2. 删除 Chroma 中的向量数据
    success = delete_from_chroma(document_name)

    if success:
        return {"message": f"文档 '{document_name}' 已删除", "status": "success"}
    else:
        raise HTTPException(status_code=500, detail=f"删除文档 '{document_name}' 失败")
