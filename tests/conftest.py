"""
pytest 共享 fixtures。

在此定义测试所需的公共资源，如测试客户端、临时目录、
测试文件路径等，供所有测试模块复用。
"""

import os
import tempfile
import shutil
from pathlib import Path

import pytest


# 必须在任何 backend import 之前设置。config.py/load_dotenv 默认不会覆盖已存在变量。
# 默认使用系统临时目录；受限环境可通过 PYTEST_ISOLATION_ROOT 指定工作区内隔离根。
_ISOLATION_ROOT = os.environ.get("PYTEST_ISOLATION_ROOT")
if _ISOLATION_ROOT:
    _TEST_ROOT = Path(_ISOLATION_ROOT).resolve()
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
else:
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="rag-pytest-"))
os.environ["DEEPSEEK_API_KEY"] = "test-key-not-used"
os.environ["LLM_ENABLED"] = "true"
os.environ["LLM_PROVIDER"] = "deepseek"
os.environ["LLM_TEMPERATURE"] = "0.3"
os.environ["LLM_MAX_TOKENS"] = "2000"
os.environ["LLM_TIMEOUT_SECONDS"] = "120"
os.environ["CHROMA_PERSIST_DIR"] = str(_TEST_ROOT / "chroma")
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "uploads")
os.environ["METADATA_DB_PATH"] = str(_TEST_ROOT / "metadata.db")
os.environ["EMBEDDING_MODEL"] = "BAAI/bge-large-zh-v1.5"
os.environ["EMBEDDING_QUERY_INSTRUCTION"] = ""
# 测试设备不得继承生产 .env；GPU 专项可通过 PYTEST_* 显式选择。
os.environ["EMBEDDING_DEVICE"] = os.environ.get(
    "PYTEST_EMBEDDING_DEVICE", "cpu"
)
os.environ["EMBEDDING_BATCH_SIZE"] = os.environ.get(
    "PYTEST_EMBEDDING_BATCH_SIZE", "32"
)
os.environ["RETRIEVAL_MODE"] = "dense"
os.environ["SPARSE_BACKEND"] = "rank_bm25_memory"
os.environ["SPARSE_INDEX_ROOT"] = str(_TEST_ROOT / "sparse_index")
os.environ["SPARSE_INDEX_CORPUS_ID"] = ""
os.environ["DENSE_CANDIDATE_K"] = ""
os.environ["MAX_CHUNKS_PER_DOCUMENT"] = "3"
os.environ["RETRIEVAL_TRACE_ENABLED"] = "false"
# 单元测试默认不加载约 2.2 GB 的 Reranker；专项测试会显式开启。
os.environ["RERANKER_ENABLED"] = "false"
os.environ["RERANKER_DEVICE"] = os.environ.get(
    "PYTEST_RERANKER_DEVICE", "cpu"
)
os.environ["RERANKER_USE_FP16"] = os.environ.get(
    "PYTEST_RERANKER_USE_FP16", "false"
)
os.environ["CITATION_PREVIEW_ENABLED"] = "false"
# OCR 默认全部关闭，测试不受本地 .env 的真实开关影响；专项测试显式开启。
os.environ["OCR_ENABLED"] = "false"
os.environ["OCR_PDF_SCANNED"] = "false"
os.environ["OCR_PDF_PAGE_IMAGES"] = "false"
os.environ["OCR_EMBEDDED_IMAGES"] = "false"
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_BASE_URL"] = ""
os.environ["VISION_CACHE_DIR"] = str(_TEST_ROOT / "vision_cache")
# OCR 原图预览默认关闭，测试按需显式开启；图片写入隔离临时目录。
os.environ["OCR_IMAGE_PREVIEW_ENABLED"] = "false"
os.environ["OCR_IMAGE_DIR"] = str(_TEST_ROOT / "ocr_images")
# 测试进程内不做模型预热，避免与集成测试争抢资源；预热逻辑单独测试。
os.environ["PREWARM_MODELS"] = "false"


def pytest_addoption(parser):
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="运行会调用真实外部 API 的测试",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-external"):
        return
    skip_external = pytest.mark.skip(reason="需要 --run-external 才会调用真实 API")
    for item in items:
        if "external" in item.keywords:
            item.add_marker(skip_external)


def pytest_sessionfinish(session, exitstatus):
    """测试结束清理隔离目录；Windows 文件锁失败时留给系统临时目录回收。"""
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def fixtures_dir():
    """测试 fixtures（测试用文件）所在目录。"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def isolated_data_dir():
    return _TEST_ROOT


@pytest.fixture
def offline_token_counter(monkeypatch):
    """API/存储合同测试用确定性计数，不加载BGE；真实模型测试不引用此fixture。"""
    from backend.routers import upload
    from backend.services import conversation_context, retrieval_trace

    def count(text):
        return len(text) + 2

    for module in (upload, conversation_context, retrieval_trace):
        monkeypatch.setattr(module, "count_tokens", count)
    return count


@pytest.fixture(scope="session")
def sample_pdf_path(fixtures_dir):
    """示例 PDF 文件路径。"""
    return str(fixtures_dir / "sample.pdf")


@pytest.fixture(scope="session")
def sample_txt_path(fixtures_dir):
    """示例 TXT 文件路径。"""
    return str(fixtures_dir / "sample.txt")


@pytest.fixture(scope="session")
def sample_docx_path(fixtures_dir):
    """示例 DOCX 文件路径。"""
    return str(fixtures_dir / "sample.docx")


@pytest.fixture
def temp_dir():
    """临时目录，测试结束后自动清理。"""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)
