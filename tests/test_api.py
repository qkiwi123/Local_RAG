"""
端到端 API 集成测试。

使用 FastAPI TestClient 模拟完整的 HTTP 请求-响应流程，
覆盖上传和问答两个核心接口。
"""

import pytest
from fastapi.testclient import TestClient


# 延迟导入 app，避免在 conftest 加载时就初始化 embedding 模型等
@pytest.fixture
def client():
    """返回 FastAPI TestClient 实例。"""
    # 在 fixture 内部导入，确保测试隔离
    from backend.main import app
    return TestClient(app)


class TestUploadAPI:
    """POST /api/upload 接口测试。"""

    def test_upload_pdf_returns_200(self, client):
        """上传一个测试 PDF，验证返回 200 和正确的 chunk 数量。"""
        with open("tests/fixtures/sample.pdf", "rb") as f:
            response = client.post(
                "/api/upload",
                files={"file": ("sample.pdf", f, "application/pdf")},
            )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "success"
        assert data["document_name"] == "sample.pdf"
        assert data["chunk_count"] > 0
        assert "上传成功" in data["message"]

    def test_upload_txt_returns_200(self, client):
        """上传一个 TXT 文件应成功。"""
        with open("tests/fixtures/sample.txt", "rb") as f:
            response = client.post(
                "/api/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["document_name"] == "sample.txt"
        assert data["chunk_count"] > 0

    def test_upload_unsupported_format_returns_400(self, client):
        """上传不支持的文件类型应返回 400。"""
        # 创建一个假的 .exe 文件内容
        fake_content = b"this is not a valid document"
        response = client.post(
            "/api/upload",
            files={"file": ("malware.exe", fake_content, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "不支持的文件格式" in response.json()["detail"]

    def test_upload_no_filename(self, client):
        """上传空文件名的请求应返回 400 或 422（FastAPI 自动校验）。"""
        response = client.post(
            "/api/upload",
            files={"file": ("", b"content", "application/pdf")},
        )
        # FastAPI/Starlette 可能返回 400 或 422
        assert response.status_code in [400, 422]

    def test_list_documents(self, client):
        """GET /api/documents 应返回文档列表（包含之前上传的）。"""
        response = client.get("/api/documents")
        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert isinstance(data["documents"], list)
        print(f"\n  Documents in store: {data['documents']}")


class TestChatAPI:
    """POST /api/chat 接口测试。"""

    def test_chat_returns_answer(self, client):
        """先确保有文档入库，再提问验证返回答案。"""
        # 先上传一个文档（确保库中有数据）
        with open("tests/fixtures/sample.pdf", "rb") as f:
            upload_resp = client.post(
                "/api/upload",
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
        assert upload_resp.status_code == 200

        # 提问
        response = client.post(
            "/api/chat",
            json={"question": "What is the annual leave policy?"},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "answer" in data
        assert len(data["answer"]) > 0
        assert "citations" in data
        print(f"\n  Answer: {data['answer'][:300]}")
        if data["citations"]:
            print(f"  Citations: {len(data['citations'])} found")

    def test_chat_empty_question_returns_422(self, client):
        """空问题应返回 422（FastAPI 自动校验 Pydantic）。"""
        response = client.post(
            "/api/chat",
            json={"question": ""},
        )
        assert response.status_code == 422
