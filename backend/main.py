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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

# 注册路由
from backend.routers import upload, chat, citations, conversations, tags

app.include_router(upload.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(citations.router)
app.include_router(conversations.router, prefix="/api")
app.include_router(tags.router, prefix="/api")


@app.get("/api/health")
async def health():
    """健康检查（不依赖 SPA 构建产物）。"""
    return {"status": "ok", "version": "0.1.0"}


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
    from backend.services.warmup import start_background_warmup

    bootstrap_structured_database(
        settings.METADATA_DB_PATH,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    recover_interrupted_requests()
    start_background_warmup()
    print(f"[启动] RAG Knowledge Base API v0.1.0")
    print(f"[启动] Embedding 模型: {settings.EMBEDDING_MODEL}")
    print(f"[启动] LLM 模型: {settings.DEEPSEEK_MODEL}")
    print(f"[启动] Chroma 数据目录: {settings.CHROMA_PERSIST_DIR}")
    print(f"[启动] 文档元数据: {settings.METADATA_DB_PATH}")
