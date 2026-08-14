"""
FastAPI 应用入口 — 创建应用实例、注册路由、配置中间件。

启动方式：
    uvicorn backend.main:app --reload --reload-dir backend
    然后访问 http://localhost:8000/docs 查看 Swagger 文档
"""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="RAG Knowledge Base API",
    version="0.1.0",
    description="企业知识库 RAG 问答系统 — 上传文档、自然语言提问、获取带引用的 AI 答案",
)

# CORS 中间件：允许 Streamlit 前端跨域访问
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


@app.get("/")
async def root():
    """根路径健康检查。"""
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup_event():
    """启动前校验或创建 structured-v1 metadata schema。"""
    from backend.config import settings
    from backend.services.metadata_schema import bootstrap_structured_database
    from backend.services.conversation_store import recover_interrupted_requests

    bootstrap_structured_database(
        settings.METADATA_DB_PATH,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    recover_interrupted_requests()
    print(f"[启动] RAG Knowledge Base API v0.1.0")
    print(f"[启动] Embedding 模型: {settings.EMBEDDING_MODEL}")
    print(f"[启动] LLM 模型: {settings.DEEPSEEK_MODEL}")
    print(f"[启动] Chroma 数据目录: {settings.CHROMA_PERSIST_DIR}")
    print(f"[启动] 文档元数据: {settings.METADATA_DB_PATH}")
