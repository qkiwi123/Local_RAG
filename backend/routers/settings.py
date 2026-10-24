"""S2-S4 设置读取、动态保存与草稿连接测试 API。"""

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from backend.models.settings_schemas import (
    APIKeyReplace,
    LLMConnectionTestRequest,
    LLMConnectionTestResponse,
    SettingsReadResponse,
    SettingsPatchRequest,
    SettingsPatchResponse,
    SettingsResetRequest,
    SettingsRuntimeStatusResponse,
    VisionConnectionTestRequest,
    VisionConnectionTestResponse,
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
    """原子保存动态设置；密钥只接受三态语义。"""
    manager = _manager(request)
    key_update = payload.llm.api_key_update if payload.llm else None
    vision_key_update = payload.vision.api_key_update if payload.vision else None
    action = key_update.action if key_update is not None else "keep"
    key_value = (
        key_update.value.get_secret_value()
        if isinstance(key_update, APIKeyReplace)
        else None
    )
    try:
        patch = {}
        if payload.llm is not None:
            patch["llm"] = payload.llm.runtime_payload()
        if payload.retrieval is not None:
            patch["retrieval"] = payload.retrieval.runtime_payload()
        if payload.vision is not None:
            patch["vision"] = payload.vision.runtime_payload()
        if payload.chunking is not None:
            patch["chunking"] = payload.chunking.runtime_payload()
        snapshot, changed_fields = manager.update_settings(
            patch,
            expected_revision=payload.expected_revision,
            api_key_action=action,
            api_key_value=key_value,
            vision_api_key_action=vision_key_update.action if vision_key_update else "keep",
            vision_api_key_value=(vision_key_update.value.get_secret_value() if isinstance(vision_key_update, APIKeyReplace) else None),
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
    _vision_effects(snapshot, changed_fields, effects, warnings)
    _chunking_effects(changed_fields, effects, warnings)
    llm_changed = any(path.startswith("llm.") for path in changed_fields)
    retrieval_changed = any(
        path.startswith("retrieval.") for path in changed_fields
    )
    if llm_changed:
        clear_llm_client_cache()
        effects.append("llm_client_cache_cleared")
    if {"retrieval.bm25_k1", "retrieval.bm25_b"} & set(changed_fields):
        from backend.services.sparse_retriever import clear_cache

        clear_cache()
        # P6-A：BM25参数属于持久化索引合同，变更后必须重建generation。
        try:
            from backend.services import sparse_rebuild

            sparse_rebuild.request_rebuild(
                "settings_changed:" + ",".join(sorted(changed_fields))
            )
        except Exception:  # noqa: BLE001
            pass
        effects.append("bm25_cache_cleared")
    if llm_changed or retrieval_changed:
        effects.append("next_request")
    if action == "clear" and snapshot.api_key_source == "environment":
        warnings.append("已清除运行时密钥，当前继续使用环境变量 API Key")
    if llm_changed and not snapshot.llm.enabled:
        warnings.append("生成模型已关闭，问答将使用 retrieval-only 模式")
    if llm_changed and (
        snapshot.llm.answer_thinking_mode == "enabled"
        or snapshot.llm.rewrite_thinking_mode == "enabled"
    ):
        warnings.append(
            "思考模式下仍会提交 Temperature，但部分模型可能忽略该参数"
        )
    if llm_changed and (
        (
            snapshot.llm.answer_thinking_mode == "disabled"
            and snapshot.llm.answer_reasoning_effort != "provider_default"
        )
        or (
            snapshot.llm.rewrite_thinking_mode == "disabled"
            and snapshot.llm.rewrite_reasoning_effort != "provider_default"
        )
    ):
        warnings.append(
            "思考模式与思考强度存在冲突组合，上游可能忽略参数或拒绝请求"
        )
    if llm_changed and snapshot.llm.provider == "openai_compatible" and (
        snapshot.llm.answer_thinking_mode != "provider_default"
        or snapshot.llm.answer_reasoning_effort != "provider_default"
        or snapshot.llm.rewrite_thinking_mode != "provider_default"
        or snapshot.llm.rewrite_reasoning_effort != "provider_default"
    ):
        warnings.append(
            "OpenAI-compatible 端点的思考参数支持不统一，请以该服务商文档和 Trace 为准"
        )
    return SettingsPatchResponse(
        revision=snapshot.revision,
        settings=build_settings_response(snapshot),
        changed_fields=changed_fields,
        effects=effects,
        warnings=warnings,
    )


@router.post("/settings/reset", response_model=SettingsPatchResponse)
async def reset_settings(payload: SettingsResetRequest, request: Request):
    """删除运行时覆盖，恢复环境变量和代码部署默认值。"""
    manager = _manager(request)
    try:
        snapshot, changed_fields = manager.reset_settings(
            payload.section,
            expected_revision=payload.expected_revision,
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
            detail="设置恢复失败且无法安全回滚",
        ) from error

    changed = set(changed_fields)
    llm_changed = any(path.startswith("llm.") for path in changed)
    retrieval_changed = any(path.startswith("retrieval.") for path in changed)
    effects: list[str] = []
    warnings: list[str] = []
    _vision_effects(snapshot, changed_fields, effects, warnings)
    _chunking_effects(changed_fields, effects, warnings)
    if llm_changed:
        clear_llm_client_cache()
        effects.append("llm_client_cache_cleared")
    if {"retrieval.bm25_k1", "retrieval.bm25_b"} & changed:
        from backend.services.sparse_retriever import clear_cache

        clear_cache()
        # P6-A：BM25参数属于持久化索引合同，变更后必须重建generation。
        try:
            from backend.services import sparse_rebuild

            sparse_rebuild.request_rebuild(
                "settings_reset:" + ",".join(sorted(changed))
            )
        except Exception:  # noqa: BLE001
            pass
        effects.append("bm25_cache_cleared")
    if llm_changed or retrieval_changed:
        effects.append("next_request")
    if llm_changed and snapshot.api_key_source == "environment":
        warnings.append("已恢复使用环境变量 API Key")
    if llm_changed and not snapshot.llm.enabled:
        warnings.append("生成模型已关闭，问答将使用 retrieval-only 模式")
    return SettingsPatchResponse(
        revision=snapshot.revision,
        settings=build_settings_response(snapshot),
        changed_fields=changed_fields,
        effects=effects,
        warnings=warnings,
    )


def _chunking_effects(changed_fields, effects, warnings):
    if any(path.startswith("chunking.") for path in changed_fields):
        if "next_document_upload" not in effects:
            effects.append("next_document_upload")
        warnings.append("分块设置从下一次上传开始生效；已有文档保留原分块，需重新上传才能应用新参数")


def _vision_effects(snapshot, changed_fields, effects, warnings):
    changed = any(path.startswith("vision.") for path in changed_fields)
    followed_change = snapshot.vision.source == "follow_llm" and bool(
        {"llm.provider", "llm.base_url", "llm.model", "llm.api_key"} & set(changed_fields)
    )
    if not (changed or followed_change):
        return
    effects.append("next_document_upload")
    warnings.append("视觉设置在下次上传或重新解析时生效，已入库文档不会自动更新")
    if snapshot.vision.enabled and not any((snapshot.vision.pdf_scanned_enabled, snapshot.vision.pdf_page_images_enabled, snapshot.vision.embedded_images_enabled)):
        warnings.append("视觉模型已启用，但尚未开启任何识别场景")
    if snapshot.vision_api_key_source == "environment" and "vision.api_key" in changed_fields:
        warnings.append("视觉密钥已恢复使用环境变量配置")
    provider = snapshot.llm.provider if snapshot.vision.source == "follow_llm" else snapshot.vision.provider
    if provider == "openai_compatible" and any(
        getattr(snapshot.vision, f"{task}_thinking_mode") != "provider_default"
        or getattr(snapshot.vision, f"{task}_reasoning_effort") != "provider_default"
        for task in ("recognize", "describe")
    ):
        warnings.append("自定义兼容接口的思考参数支持不统一，请用图片测试验证；不支持时可选择服务商默认")


@router.post("/settings/test-vision", response_model=VisionConnectionTestResponse)
async def test_vision(payload: VisionConnectionTestRequest, request: Request):
    from backend.services.vision import vision_config_from_snapshot, test_vision_connection

    try:
        draft_snapshot = _manager(request).vision_draft_snapshot(payload.vision)
    except RuntimeSettingsValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail="运行时密钥存储当前不可用") from error
    config = vision_config_from_snapshot(draft_snapshot)
    return await run_in_threadpool(test_vision_connection, config, task=payload.task)


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
