"""
FastAPI 应用入口 — 创建应用实例、注册路由、配置中间件。

启动方式：
    uvicorn backend.main:app --reload
    然后访问 http://localhost:8000/docs 查看 Swagger 文档
"""

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
from backend.routers import upload, chat

app.include_router(upload.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/")
async def root():
    """根路径健康检查。"""
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化日志。"""
    from backend.config import settings
    print(f"[启动] RAG Knowledge Base API v0.1.0")
    print(f"[启动] Embedding 模型: {settings.EMBEDDING_MODEL}")
    print(f"[启动] LLM 模型: {settings.DEEPSEEK_MODEL}")
    print(f"[启动] Chroma 数据目录: {settings.CHROMA_PERSIST_DIR}")
