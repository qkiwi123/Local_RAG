"""引用预览只读 API — GET /api/citations/{document_id}/chunks/{chunk_id}/preview

路由层只负责开关、异常映射和响应头；一致性逻辑全部在 citation_preview 服务层。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.services.structured_citation_preview import (
    CitationPreviewConflictError,
    CitationPreviewNotFoundError,
    CitationPreviewValidationError,
    build_structured_citation_preview,
)

router = APIRouter(tags=["citations"])


@router.get("/api/citations/{document_id}/chunks/{chunk_id}/preview")
async def get_citation_preview(document_id: str, chunk_id: str):
    """按需加载引用原文预览——含完整解析页和精确高亮定位。"""
    if not settings.CITATION_PREVIEW_ENABLED:
        raise HTTPException(status_code=404, detail="引用预览功能未开启")

    try:
        preview = build_structured_citation_preview(document_id, chunk_id)
    except CitationPreviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except CitationPreviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except CitationPreviewValidationError as error:
        raise HTTPException(status_code=422, detail=str(error))

    return JSONResponse(
        content=preview.model_dump(),
        headers={"Cache-Control": "private, no-store"},
    )
