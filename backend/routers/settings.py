"""S2/S3 设置读取、LLM 保存与草稿连接测试 API。"""

from fastapi import APIRouter, HTTPException, Request

from backend.models.settings_schemas import (
    APIKeyReplace,
    LLMConnectionTestRequest,
    LLMConnectionTestResponse,
    SettingsReadResponse,
    SettingsPatchRequest,
    SettingsPatchResponse,
    SettingsRuntimeStatusResponse,
)
from backend.services.llm import (
    call_config_from_snapshot,
    clear_llm_client_cache,
    test_llm_connection,
)
from backend.services.runtime_settings import (
    RuntimeSettingsCommitError,
    RuntimeSettingsManager,
    RuntimeSettingsValidationError,
)
from backend.services.secret_store import SecretStoreError
from backend.services.settings_status import (
    build_runtime_status,
    build_settings_response,
)
from backend.services.settings_store import SettingsRevisionConflict


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


@router.patch("/settings", response_model=SettingsPatchResponse)
async def patch_settings(payload: SettingsPatchRequest, request: Request):
    """原子保存 S3 LLM 设置；密钥只接受保留、替换或清除语义。"""
    manager = _manager(request)
    key_update = payload.llm.api_key_update
    action = key_update.action if key_update is not None else "keep"
    key_value = (
        key_update.value.get_secret_value()
        if isinstance(key_update, APIKeyReplace)
        else None
    )
    try:
        snapshot, changed_fields = manager.update_llm(
            payload.llm.runtime_payload(),
            expected_revision=payload.expected_revision,
            api_key_action=action,
            api_key_value=key_value,
        )
    except SettingsRevisionConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "settings_revision_conflict",
                "message": "设置已被其他页面更新，请刷新后重试",
                "expected_revision": error.expected_revision,
                "actual_revision": error.actual_revision,
            },
        ) from error
    except RuntimeSettingsValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SecretStoreError as error:
        raise HTTPException(
            status_code=503,
            detail="运行时密钥存储当前不可用",
        ) from error
    except RuntimeSettingsCommitError as error:
        raise HTTPException(
            status_code=500,
            detail="设置保存失败且无法安全恢复",
        ) from error

    effects: list[str] = []
    warnings: list[str] = []
    if changed_fields:
        clear_llm_client_cache()
        effects.extend(["llm_client_cache_cleared", "next_request"])
    if action == "clear" and snapshot.api_key_source == "environment":
        warnings.append("已清除运行时密钥，当前继续使用环境变量 API Key")
    if not snapshot.llm.enabled:
        warnings.append("生成模型已关闭，问答将使用 retrieval-only 模式")
    return SettingsPatchResponse(
        revision=snapshot.revision,
        settings=build_settings_response(snapshot),
        changed_fields=changed_fields,
        effects=effects,
        warnings=warnings,
    )


@router.post(
    "/settings/test-llm",
    response_model=LLMConnectionTestResponse,
)
async def test_llm(
    payload: LLMConnectionTestRequest,
    request: Request,
):
    """使用未保存草稿执行一次最小真实请求；自动化测试必须 mock。"""
    snapshot = _manager(request).snapshot()
    draft = payload.llm
    overrides = draft.model_dump(
        exclude={"api_key"},
        exclude_none=True,
        exclude_unset=True,
    )
    config = call_config_from_snapshot(
        snapshot,
        overrides=overrides,
        api_key=draft.api_key or snapshot.api_key,
        use_snapshot_key=False,
    )
    return test_llm_connection(config)


@router.get(
    "/settings/runtime-status",
    response_model=SettingsRuntimeStatusResponse,
)
async def get_runtime_status(request: Request):
    """只读查看模型、Torch 和索引状态，不触发模型冷加载。"""
    return build_runtime_status(_manager(request).snapshot())
