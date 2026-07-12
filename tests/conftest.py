"""
pytest 共享 fixtures。

在此定义测试所需的公共资源，如测试客户端、临时目录、
测试文件路径等，供所有测试模块复用。
"""

import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture(scope="session")
def fixtures_dir():
    """测试 fixtures（测试用文件）所在目录。"""
    return Path(__file__).parent / "fixtures"


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
