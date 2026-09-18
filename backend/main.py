"""
FastAPI 应用入口 — 创建应用实例、注册路由、配置中间件。

启动方式：
    uvicorn backend.main:app --reload --reload-dir backend
    然后访问 http://localhost:8000/docs 查看 Swagger 文档

生产模式：web/dist 构建产物存在时由本服务静态托管 SPA（非 /api 路径
fallback 到 index.html）；开发模式用 Vite dev server（代理 /api）。
"""

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# Windows 会从注册表初始化 mimetypes；部分机器把 .js 映射为 text/plain。
# 浏览器会拒绝以 text/plain 返回的 ES module，导致 SPA 的 #root 保持空白。
mimetypes.add_type("text/javascript", ".js")

app = FastAPI(
    title="RAG Knowledge Base API",
    version="0.1.0",
    description="企业知识库 RAG 问答系统 — 上传文档、自然语言提问、获取带引用的 AI 答案",
)

# CORS 中间件：允许前端跨域访问
# 开发阶段 allow_origins=["*"]，正式部署再收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_sensitive_validation_location(location) -> bool:
    exact_names = {
        "api_key",
        "api_key_update",
        "password",
        "secret",
        "authorization",
        "access_token",
        "refresh_token",
    }
    return any(
        str(part).strip().lower().replace("-", "_") in exact_names
        for part in location
    )


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(
    request: Request,
    error: RequestValidationError,
):
    """敏感输入校验失败时不让 FastAPI 的默认 422 回显原值。"""
    errors = error.errors()
    def contains_secret(value):
        if isinstance(value, dict):
            return any(_is_sensitive_validation_location((key,)) or contains_secret(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(contains_secret(item) for item in value)
        return False

    if not any(
        _is_sensitive_validation_location(item.get("loc", ())) or contains_secret(item.get("input"))
        for item in errors
    ):
        return await request_validation_exception_handler(request, error)
    sanitized = []
    for item in errors:
        clean = {
            key: value
            for key, value in item.items()
            if key not in {"input", "ctx"}
        }
        if _is_sensitive_validation_location(item.get("loc", ())):
            clean["input"] = "**********"
        sanitized.append(clean)
    return JSONResponse(status_code=422, content={"detail": sanitized})

# 注册路由
from backend.routers import (
    chat,
    citations,
    conversations,
    settings as settings_router,
    tags,
    upload,
)

app.include_router(upload.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(citations.router)
app.include_router(conversations.router, prefix="/api")
app.include_router(tags.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")


@app.get("/api/health")
async def health():
    """存活与模型预热状态（不依赖 SPA 构建产物）。"""
    from backend.services.warmup import get_warmup_status

    return {
        "status": "ok",
        "version": "0.1.0",
        "model_warmup": get_warmup_status(),
    }


class _SpaStaticFiles(StaticFiles):
    """SPA fallback：非文件路径返回 index.html，供前端路由使用。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

if (_WEB_DIST / "index.html").is_file():
    # 显式路由优先于挂载；此处仅接管非 /api 的其余路径
    app.mount("/", _SpaStaticFiles(directory=str(_WEB_DIST), html=True), name="spa")
else:

    @app.get("/")
    async def root():
        """根路径健康检查（SPA 未构建时的回退）。"""
        return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup_event():
    """启动前校验或创建 metadata schema，并后台预热本地模型。"""
    from backend.config import settings
    from backend.services.metadata_schema import bootstrap_structured_database
    from backend.services.conversation_store import recover_interrupted_requests
    from backend.services.runtime_settings import RuntimeSettingsManager
    from backend.services.warmup import start_background_warmup

    bootstrap_structured_database(
        settings.METADATA_DB_PATH,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    app.state.runtime_settings_manager = RuntimeSettingsManager()
    recover_interrupted_requests()
    start_background_warmup()
    # P6-A：Sparse后台重建协调。仅bm25s_mmap部署生效；启动时识别
    # dirty/错配指针并自愈重建，重启不会把持久化dirty悄悄恢复成ready。
    try:
        from backend.services import sparse_rebuild

        sparse_rebuild.configure(
            runtime_provider=lambda: app.state.runtime_settings_manager.snapshot()
        )
        _sparse_worker = sparse_rebuild.start_worker(recovery=True)
        if _sparse_worker.get("started"):
            print("[启动] Sparse后台重建协调器已启动（bm25s_mmap）")
    except Exception as error:  # noqa: BLE001 协调器故障不阻断服务启动
        print(f"[启动] Sparse后台重建协调器启动失败: {error}")
    print(f"[启动] RAG Knowledge Base API v0.1.0")
    print(
        f"[启动] Embedding 模型: {settings.EMBEDDING_MODEL}; "
        f"device={settings.EMBEDDING_DEVICE}; batch={settings.EMBEDDING_BATCH_SIZE}"
    )
    print(
        f"[启动] Reranker: enabled={settings.RERANKER_ENABLED}; "
        f"device={settings.RERANKER_DEVICE}; fp16={settings.RERANKER_USE_FP16}; "
        f"batch={settings.RERANKER_BATCH_SIZE}"
    )
    print(f"[启动] LLM 模型: {settings.DEEPSEEK_MODEL}")
    print(f"[启动] Chroma 数据目录: {settings.CHROMA_PERSIST_DIR}")
    print(f"[启动] 文档元数据: {settings.METADATA_DB_PATH}")


@app.on_event("shutdown")
async def shutdown_event():
    """停止后台任务；Sparse重建worker退出前不再发布新generation。"""
    try:
        from backend.services import sparse_rebuild

        sparse_rebuild.stop_worker(timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
