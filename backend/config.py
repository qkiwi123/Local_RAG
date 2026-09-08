"""
集中管理所有配置项。

所有可配置参数从环境变量读取，在此处做校验和默认值设置，
其他模块统一从此导入，避免配置散落在各文件中。
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
# __file__ 是 backend/config.py，向上两级到项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a strict boolean environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false、1/0、yes/no 或 on/off")


def _is_supported_torch_device(device: str) -> bool:
    """设备配置只接受当前项目已验证的 CPU/CUDA 形式。"""
    return re.fullmatch(r"(?:cpu|cuda(?::\d+)?)", device) is not None


class Settings:
    """应用全局配置类，所有配置项通过实例属性访问。"""

    # ─── 必填项（缺一不可） ──────────────────────────────
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    # S1 先解析运行时设置可覆盖的部署默认；实际业务链路在 S3/S4 接入。
    LLM_ENABLED: bool = _env_bool("LLM_ENABLED", True)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

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
    _DENSE_CANDIDATE_K_RAW = (os.getenv("DENSE_CANDIDATE_K") or "").strip()
    DENSE_CANDIDATE_K: int | None = (
        int(_DENSE_CANDIDATE_K_RAW)
        if _DENSE_CANDIDATE_K_RAW
        else None
    )
    MAX_CHUNKS_PER_DOCUMENT: int = int(
        os.getenv(
            "MAX_CHUNKS_PER_DOCUMENT",
            str(min(3, RETRIEVAL_TOP_K)),
        )
    )
    # Chroma cosine distance 越小越相似；P1-4 在真实评测集上校准为 0.67。
    RETRIEVAL_DISTANCE_THRESHOLD: float = float(
        os.getenv("RETRIEVAL_DISTANCE_THRESHOLD", "0.67")
    )
    # P2-1 安全默认仍为 Dense；混合检索只有通过消融门槛后才切生产配置。
    RETRIEVAL_MODE: str = os.getenv("RETRIEVAL_MODE", "dense").strip().lower()
    BM25_TOP_K: int = int(os.getenv("BM25_TOP_K", "15"))
    BM25_K1: float = float(os.getenv("BM25_K1", "1.5"))
    BM25_B: float = float(os.getenv("BM25_B", "0.75"))
    # P6-C生产默认使用已验收的持久化Sparse索引；仍可显式切回旧实现。
    SPARSE_BACKEND: str = os.getenv(
        "SPARSE_BACKEND", "bm25s_mmap"
    ).strip().lower()
    SPARSE_INDEX_ROOT: str = os.getenv(
        "SPARSE_INDEX_ROOT", str(BASE_DIR / "data" / "sparse_index")
    ).strip()
    SPARSE_INDEX_CORPUS_ID: str = os.getenv(
        "SPARSE_INDEX_CORPUS_ID", "production-rag-20260908"
    ).strip()
    # P6-A后台重建协调：文档增删后自动重建持久化Sparse索引；仅在
    # SPARSE_BACKEND=bm25s_mmap时生效。评测/排查可显式关闭。
    SPARSE_AUTO_REBUILD: bool = _env_bool("SPARSE_AUTO_REBUILD", True)
    RRF_K: int = int(os.getenv("RRF_K", "60"))
    # P2-3 Trace 会返回未入选候选的完整原文，仅在可信调试环境显式开启。
    RETRIEVAL_TRACE_ENABLED: bool = _env_bool(
        "RETRIEVAL_TRACE_ENABLED", False
    )
    # P2-4 引用原文预览；开启后 API 会返回完整解析页文本，仅在可信环境使用。
    CITATION_PREVIEW_ENABLED: bool = _env_bool(
        "CITATION_PREVIEW_ENABLED", False
    )
    CITATION_PREVIEW_MAX_PAGE_CHARS: int = int(
        os.getenv("CITATION_PREVIEW_MAX_PAGE_CHARS", "200000")
    )
    CITATION_PREVIEW_MAX_DOCUMENT_CHARS: int = int(
        os.getenv("CITATION_PREVIEW_MAX_DOCUMENT_CHARS", "500000")
    )

    # ─── 持久化对话 ──────────────────────────────────
    CONVERSATION_HISTORY_MESSAGES: int = int(
        os.getenv("CONVERSATION_HISTORY_MESSAGES", "12")
    )
    CONVERSATION_HISTORY_MAX_TOKENS: int = int(
        os.getenv("CONVERSATION_HISTORY_MAX_TOKENS", "1500")
    )
    CONVERSATION_MESSAGE_MAX_CHARS: int = int(
        os.getenv("CONVERSATION_MESSAGE_MAX_CHARS", "8000")
    )
    CONVERSATION_MAX_MESSAGES: int = int(
        os.getenv("CONVERSATION_MAX_MESSAGES", "200")
    )
    CONVERSATION_PENDING_TIMEOUT_SECONDS: int = int(
        os.getenv("CONVERSATION_PENDING_TIMEOUT_SECONDS", "300")
    )

    # ─── Reranker ────────────────────────────────────
    # 安全默认关闭，生产环境通过 .env 显式启用高质量模式。
    RERANKER_ENABLED: bool = _env_bool("RERANKER_ENABLED", False)
    RERANKER_MODEL: str = os.getenv(
        "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
    )
    # P1-5 的 k=15/20/30 扫描质量持平，选择延迟最低的 k=15。
    RERANKER_CANDIDATE_K: int = int(os.getenv("RERANKER_CANDIDATE_K", "15"))
    RERANKER_DEVICE: str = os.getenv("RERANKER_DEVICE", "cpu").strip().lower()
    RERANKER_USE_FP16: bool = _env_bool("RERANKER_USE_FP16", False)
    RERANKER_BATCH_SIZE: int = int(os.getenv("RERANKER_BATCH_SIZE", "8"))
    RERANKER_MAX_LENGTH: int = int(os.getenv("RERANKER_MAX_LENGTH", "512"))

    # ─── Embedding 模型 ───────────────────────────────
    # P1-1 实验选择 BAAI/bge-large-zh-v1.5（中文检索，1024 维）。
    # query instruction A/B 中 noinst 的 MRR 与检索延迟更优，因此默认留空。
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"
    )
    EMBEDDING_QUERY_INSTRUCTION: str = os.getenv(
        "EMBEDDING_QUERY_INSTRUCTION", ""
    )
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu").strip().lower()
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

    # ─── 视觉 / OCR（多模态模型 API，独立于问答 LLM） ───
    # 智谱 GLM-4.6V-Flash 等 OpenAI 兼容端点；密钥一律放 .env，
    # 关闭总开关时完全不加载、不调用。
    VISION_API_KEY: str = os.getenv("VISION_API_KEY", "")
    VISION_BASE_URL: str = os.getenv("VISION_BASE_URL", "")
    VISION_MODEL: str = os.getenv("VISION_MODEL", "glm-4.6v-flash")
    OCR_ENABLED: bool = _env_bool("OCR_ENABLED", False)
    # A 层：无文本 PDF 页整页 OCR。
    OCR_PDF_SCANNED: bool = _env_bool("OCR_PDF_SCANNED", False)
    # B 层：有文本但含图片/图表的 PDF 页整页描述。
    OCR_PDF_PAGE_IMAGES: bool = _env_bool("OCR_PDF_PAGE_IMAGES", False)
    # C 层：DOCX/PPTX 内嵌图片识别。
    OCR_EMBEDDED_IMAGES: bool = _env_bool("OCR_EMBEDDED_IMAGES", False)
    OCR_MAX_IMAGE_EDGE: int = int(os.getenv("OCR_MAX_IMAGE_EDGE", "1600"))
    OCR_MAX_PER_DOCUMENT: int = int(os.getenv("OCR_MAX_PER_DOCUMENT", "20"))
    OCR_TIMEOUT_SECONDS: float = float(os.getenv("OCR_TIMEOUT_SECONDS", "60"))
    VISION_CACHE_DIR: str = os.getenv(
        "VISION_CACHE_DIR", str(BASE_DIR / "data" / "vision_cache")
    )
    # OCR 原图对照预览：入库时保存 OCR 使用的图片，供文档/引用预览对照展示。
    OCR_IMAGE_PREVIEW_ENABLED: bool = _env_bool("OCR_IMAGE_PREVIEW_ENABLED", True)
    OCR_IMAGE_DIR: str = os.getenv(
        "OCR_IMAGE_DIR", str(BASE_DIR / "data" / "ocr_images")
    )

    # ─── Trace 持久化与模型预热 ────────────────────────
    # Trace 仅在 RETRIEVAL_TRACE_ENABLED=true 时计算并持久化；总量超过
    # 上限时按创建时间淘汰最旧记录（LRU）。会话删除时级联清理。
    TRACE_STORAGE_MAX_BYTES: int = int(
        os.getenv("TRACE_STORAGE_MAX_BYTES", str(50 * 1024 * 1024))
    )
    # 服务启动后后台预热 Embedding/Reranker，把冷启动成本挪到启动期。
    PREWARM_MODELS: bool = _env_bool("PREWARM_MODELS", True)

    def __init__(self):
        """初始化时校验必填项。"""
        self._validate()

    def _validate(self):
        """校验必须配置的项是否已设置，缺失时抛出明确错误。"""
        # S3 起 API Key 可以来自用户级 DPAPI 文件；最终的“LLM 启用时必须
        # 有 Key”约束由 RuntimeSettingsManager 在合并环境与密钥覆盖后校验。
        parsed_base_url = urlsplit(self.DEEPSEEK_BASE_URL)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("DEEPSEEK_BASE_URL 必须是有效的 HTTP(S) URL")
        if not self.DEEPSEEK_MODEL.strip() or len(self.DEEPSEEK_MODEL) > 200:
            raise ValueError("DEEPSEEK_MODEL 长度必须为 1 到 200 个字符")
        if self.LLM_PROVIDER not in {"deepseek", "openai_compatible"}:
            raise ValueError("LLM_PROVIDER 仅支持 deepseek 或 openai_compatible")
        if not 0 <= self.LLM_TEMPERATURE <= 2:
            raise ValueError("LLM_TEMPERATURE 必须在 0 到 2 之间")
        if not 1 <= self.LLM_MAX_TOKENS <= 32768:
            raise ValueError("LLM_MAX_TOKENS 必须在 1 到 32768 之间")
        if not 1 <= self.LLM_TIMEOUT_SECONDS <= 600:
            raise ValueError("LLM_TIMEOUT_SECONDS 必须在 1 到 600 之间")
        if self.CHUNK_STRATEGY not in {"fixed", "recursive"}:
            raise ValueError("CHUNK_STRATEGY 仅支持 fixed 或 recursive")
        if self.CHUNK_SIZE <= 0:
            raise ValueError("CHUNK_SIZE 必须大于 0")
        if self.CHUNK_OVERLAP < 0 or self.CHUNK_OVERLAP >= self.CHUNK_SIZE:
            raise ValueError("CHUNK_OVERLAP 必须满足 0 <= overlap < CHUNK_SIZE")
        if self.CHUNK_MAX_TOKENS <= 0:
            raise ValueError("CHUNK_MAX_TOKENS 必须大于 0")
        if not 0 <= self.RETRIEVAL_DISTANCE_THRESHOLD <= 2:
            raise ValueError("RETRIEVAL_DISTANCE_THRESHOLD 必须在 0 到 2 之间")
        if self.RETRIEVAL_MODE not in {"dense", "bm25", "hybrid"}:
            raise ValueError("RETRIEVAL_MODE 必须是 dense、bm25 或 hybrid")
        if not 1 <= self.RETRIEVAL_TOP_K <= 20:
            raise ValueError("RETRIEVAL_TOP_K 必须在 1 到 20 之间")
        if self.DENSE_CANDIDATE_K is not None and not (
            self.RETRIEVAL_TOP_K <= self.DENSE_CANDIDATE_K <= 100
        ):
            raise ValueError(
                "DENSE_CANDIDATE_K 必须在 RETRIEVAL_TOP_K 到 100 之间"
            )
        if not 1 <= self.MAX_CHUNKS_PER_DOCUMENT <= self.RETRIEVAL_TOP_K:
            raise ValueError(
                "MAX_CHUNKS_PER_DOCUMENT 必须在 1 到 RETRIEVAL_TOP_K 之间"
            )
        if self.BM25_TOP_K <= 0:
            raise ValueError("BM25_TOP_K 必须大于 0")
        if self.BM25_K1 <= 0:
            raise ValueError("BM25_K1 必须大于 0")
        if not 0 <= self.BM25_B <= 1:
            raise ValueError("BM25_B 必须在 0 到 1 之间")
        if self.SPARSE_BACKEND not in {"rank_bm25_memory", "bm25s_mmap"}:
            raise ValueError(
                "SPARSE_BACKEND 必须是 rank_bm25_memory 或 bm25s_mmap"
            )
        if self.SPARSE_BACKEND == "bm25s_mmap" and not self.SPARSE_INDEX_CORPUS_ID:
            raise ValueError(
                "SPARSE_BACKEND=bm25s_mmap 时必须配置 SPARSE_INDEX_CORPUS_ID"
            )
        if self.SPARSE_BACKEND == "bm25s_mmap" and not self.SPARSE_INDEX_ROOT:
            raise ValueError(
                "SPARSE_BACKEND=bm25s_mmap 时必须配置 SPARSE_INDEX_ROOT"
            )
        if self.RRF_K <= 0:
            raise ValueError("RRF_K 必须大于 0")
        if self.RERANKER_CANDIDATE_K < self.RETRIEVAL_TOP_K:
            raise ValueError("RERANKER_CANDIDATE_K 不能小于 RETRIEVAL_TOP_K")
        if self.RERANKER_BATCH_SIZE <= 0:
            raise ValueError("RERANKER_BATCH_SIZE 必须大于 0")
        if self.RERANKER_MAX_LENGTH <= 0:
            raise ValueError("RERANKER_MAX_LENGTH 必须大于 0")
        if not _is_supported_torch_device(self.RERANKER_DEVICE):
            raise ValueError("RERANKER_DEVICE 仅支持 cpu、cuda 或 cuda:非负序号")
        if self.RERANKER_USE_FP16 and not self.RERANKER_DEVICE.startswith("cuda"):
            raise ValueError("RERANKER_USE_FP16=true 时 RERANKER_DEVICE 必须是 cuda")
        if not _is_supported_torch_device(self.EMBEDDING_DEVICE):
            raise ValueError("EMBEDDING_DEVICE 仅支持 cpu、cuda 或 cuda:非负序号")
        if self.EMBEDDING_BATCH_SIZE <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE 必须大于 0")
        if self.CITATION_PREVIEW_MAX_PAGE_CHARS <= 0:
            raise ValueError("CITATION_PREVIEW_MAX_PAGE_CHARS 必须大于 0")
        if self.OCR_MAX_IMAGE_EDGE <= 0:
            raise ValueError("OCR_MAX_IMAGE_EDGE 必须大于 0")
        if self.OCR_MAX_PER_DOCUMENT <= 0:
            raise ValueError("OCR_MAX_PER_DOCUMENT 必须大于 0")
        if self.OCR_TIMEOUT_SECONDS <= 0:
            raise ValueError("OCR_TIMEOUT_SECONDS 必须大于 0")
        if self.OCR_ENABLED and (
            not self.VISION_API_KEY or not self.VISION_BASE_URL
        ):
            raise ValueError(
                "OCR_ENABLED=true 时必须配置 VISION_API_KEY 与 VISION_BASE_URL"
            )
        if self.TRACE_STORAGE_MAX_BYTES <= 0:
            raise ValueError("TRACE_STORAGE_MAX_BYTES 必须大于 0")
        # 确保持久化目录存在
        Path(self.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.METADATA_DB_PATH).parent.mkdir(parents=True, exist_ok=True)


# 模块级单例，其他模块直接 `from backend.config import settings` 即可
settings = Settings()
