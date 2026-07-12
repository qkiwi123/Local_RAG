"""
集中管理所有配置项。

所有可配置参数从环境变量读取，在此处做校验和默认值设置，
其他模块统一从此导入，避免配置散落在各文件中。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
# __file__ 是 backend/config.py，向上两级到项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """应用全局配置类，所有配置项通过实例属性访问。"""

    # ─── 必填项（缺一不可） ──────────────────────────────
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # ─── 文件存储 ──────────────────────────────────────
    # 相对路径统一基于项目根目录（即 backend/ 的上一级）
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))

    # ─── Chroma 向量数据库 ─────────────────────────────
    CHROMA_PERSIST_DIR: str = os.getenv(
        "CHROMA_PERSIST_DIR", str(BASE_DIR / "data" / "chroma_db")
    )

    # ─── 文本切分参数 ─────────────────────────────────
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))

    # ─── 检索参数 ─────────────────────────────────────
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))

    # ─── Embedding 模型 ───────────────────────────────
    # 阶段一使用英文轻量模型 all-MiniLM-L6-v2，384 维向量
    # 阶段二切换为 BAAI/bge-large-zh-v1.5（中文专优，1024 维）
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL", "all-MiniLM-L6-v2"
    )

    def __init__(self):
        """初始化时校验必填项。"""
        self._validate()

    def _validate(self):
        """校验必须配置的项是否已设置，缺失时抛出明确错误。"""
        if not self.DEEPSEEK_API_KEY:
            raise ValueError(
                "缺少必填配置: DEEPSEEK_API_KEY\n"
                "请在项目根目录的 .env 文件中设置 DEEPSEEK_API_KEY=你的API密钥\n"
                "（可参考 .env.example 模板）"
            )
        # 确保持久化目录存在
        Path(self.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)


# 模块级单例，其他模块直接 `from backend.config import settings` 即可
settings = Settings()
