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
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="rag-pytest-"))
os.environ["DEEPSEEK_API_KEY"] = "test-key-not-used"
os.environ["CHROMA_PERSIST_DIR"] = str(_TEST_ROOT / "chroma")
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "uploads")
os.environ["METADATA_DB_PATH"] = str(_TEST_ROOT / "metadata.db")


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
