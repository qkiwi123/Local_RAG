"""S2 只读设置与本地运行状态 API。"""

from fastapi import APIRouter, HTTPException, Request

from backend.models.settings_schemas import (
    SettingsReadResponse,
    SettingsRuntimeStatusResponse,
)
from backend.services.runtime_settings import RuntimeSettingsManager
from backend.services.settings_status import (
    build_runtime_status,
    build_settings_response,
)


router = APIRouter(tags=["settings"])


def _manager(request: Request) -> RuntimeSettingsManager:
    manager = getattr(request.app.state, "runtime_settings_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="运行时设置尚未初始化")
    return manager


@router.get("/settings", response_model=SettingsReadResponse)
async def get_settings(request: Request):
    """返回当前有效配置；API Key 永不返回明文。"""
    return build_settings_response(_manager(request).snapshot())


@router.get(
    "/settings/runtime-status",
    response_model=SettingsRuntimeStatusResponse,
)
async def get_runtime_status(request: Request):
    """只读查看模型、Torch 和索引状态，不触发模型冷加载。"""
    return build_runtime_status(_manager(request).snapshot())
