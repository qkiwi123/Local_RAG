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
    METADATA_DB_PATH: str = os.getenv(
        "METADATA_DB_PATH", str(BASE_DIR / "data" / "metadata.db")
    )

    # ─── Chroma 向量数据库 ─────────────────────────────
    CHROMA_PERSIST_DIR: str = os.getenv(
        "CHROMA_PERSIST_DIR", str(BASE_DIR / "data" / "chroma_db")
    )

    # ─── 文本切分参数 ─────────────────────────────────
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    # 缺失显式配置时保守回退 fixed；已迁移环境通过 .env 启用 recursive。
    CHUNK_STRATEGY: str = os.getenv("CHUNK_STRATEGY", "fixed").strip().lower()
    # BGE 最大 512 token；预留 32 token 给特殊标记及后续扩展。
    CHUNK_MAX_TOKENS: int = int(os.getenv("CHUNK_MAX_TOKENS", "480"))

    # ─── 检索参数 ─────────────────────────────────────
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))

    # ─── Embedding 模型 ───────────────────────────────
    # P1-1 实验选择 BAAI/bge-large-zh-v1.5（中文检索，1024 维）。
    # query instruction A/B 中 noinst 的 MRR 与检索延迟更优，因此默认留空。
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"
    )
    EMBEDDING_QUERY_INSTRUCTION: str = os.getenv(
        "EMBEDDING_QUERY_INSTRUCTION", ""
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
        if self.CHUNK_STRATEGY not in {"fixed", "recursive"}:
            raise ValueError("CHUNK_STRATEGY 仅支持 fixed 或 recursive")
        if self.CHUNK_SIZE <= 0:
            raise ValueError("CHUNK_SIZE 必须大于 0")
        if self.CHUNK_OVERLAP < 0 or self.CHUNK_OVERLAP >= self.CHUNK_SIZE:
            raise ValueError("CHUNK_OVERLAP 必须满足 0 <= overlap < CHUNK_SIZE")
        if self.CHUNK_MAX_TOKENS <= 0:
            raise ValueError("CHUNK_MAX_TOKENS 必须大于 0")
        # 确保持久化目录存在
        Path(self.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.METADATA_DB_PATH).parent.mkdir(parents=True, exist_ok=True)


# 模块级单例，其他模块直接 `from backend.config import settings` 即可
settings = Settings()
