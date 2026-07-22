"""引用预览只读 API — GET /api/citations/{document_id}/chunks/{chunk_id}/preview

路由层只负责开关、异常映射和响应头；一致性逻辑全部在 citation_preview 服务层。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.services.citation_preview import build_citation_preview

router = APIRouter(tags=["citations"])


@router.get("/api/citations/{document_id}/chunks/{chunk_id}/preview")
async def get_citation_preview(document_id: str, chunk_id: str):
    """按需加载引用原文预览——含完整解析页和精确高亮定位。

    状态码：
    - 200: 所有强一致性校验通过
    - 404: 功能关闭、文档不存在、文档非 ready、chunk 不存在或页快照不存在
    - 409: chunk 与页快照版本/offset 不匹配，切片内容 != chunk 内容
    - 422: offset 越界、start >= end 或页文本超过安全上限
    """
    if not settings.CITATION_PREVIEW_ENABLED:
        raise HTTPException(status_code=404, detail="引用预览功能未开启")

    try:
        preview = build_citation_preview(document_id, chunk_id)
    except ValueError as error:
        message = str(error)
        if "找不到文档" in message or "不存在" in message or "缺少页快照" in message:
            raise HTTPException(status_code=404, detail=message)
        if "索引与解析版本不一致" in message:
            raise HTTPException(status_code=409, detail=message)
        if "过大" in message or "超过" in message:
            raise HTTPException(status_code=422, detail=message)
        raise HTTPException(status_code=404, detail=message)

    return JSONResponse(
        content=preview.model_dump(),
        headers={"Cache-Control": "private, no-store"},
    )
