"""多级树形标签管理 API。"""

from fastapi import APIRouter, HTTPException

from backend.models.schemas import (
    TagCreate,
    TagInfo,
    TagRename,
)
from backend.services.tag_store import (
    TagNotFoundError,
    TagValidationError,
    create_tag,
    delete_tag,
    list_tag_tree,
    rename_tag,
)

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[TagInfo])
async def list_tags():
    """返回全部标签及子树文档数（树形结构由前端按路径组装）。"""
    return list_tag_tree()


@router.post("/tags", response_model=TagInfo)
async def create(payload: TagCreate):
    """创建标签；自动补齐父级节点，已存在时幂等返回。"""
    try:
        row = create_tag(payload.tag_path)
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    count = next(
        (
            tag["document_count"]
            for tag in list_tag_tree()
            if tag["tag_path"] == row["tag_path"]
        ),
        0,
    )
    return TagInfo(
        tag_path=row["tag_path"],
        name=row["name"],
        document_count=count,
    )


@router.patch("/tags/{tag_path:path}")
async def rename(tag_path: str, payload: TagRename):
    """重命名标签及其整个子树，文档关联同步更新。"""
    try:
        rename_tag(tag_path, payload.new_path)
    except TagNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"status": "success"}


@router.delete("/tags/{tag_path:path}")
async def delete(tag_path: str):
    """删除标签及其整个子树；文档本身保留，仅解除关联。"""
    try:
        removed = delete_tag(tag_path)
    except TagNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"status": "success", "removed_tag_count": removed}
