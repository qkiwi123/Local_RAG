"""OCR 原图只读接口测试（不加载本地模型）。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import settings
from backend.models.document_structure import SourceLocator
from backend.routers import upload
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts


@pytest.fixture
def ocr_preview_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OCR_IMAGE_PREVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "OCR_IMAGE_DIR", str(tmp_path / "ocr_images"))

    unit = assemble_unit_from_drafts(
        document_id="doc-1",
        document_name="扫描件.pdf",
        parser_version="structured-v2",
        locator=SourceLocator(
            kind="page",
            unit_index=0,
            location_label="第 1 页",
            page_number=1,
        ),
        drafts=[
            BlockDraft(
                block_type="paragraph",
                text="OCR 文本",
                attributes={
                    "ocr": True,
                    "ocr_image_ref": "0" * 16,
                },
            )
        ],
    )

    monkeypatch.setattr(
        upload,
        "get_document",
        lambda document_id: (
            {"status": "ready", "original_name": "扫描件.pdf"}
            if document_id == "doc-1"
            else None
        ),
    )
    monkeypatch.setattr(
        upload,
        "list_document_units",
        lambda document_id, parser_version, db_path=None: (
            [unit] if document_id == "doc-1" else []
        ),
    )

    image_path = tmp_path / "ocr_images" / f"{'0' * 16}.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-jpeg")

    app = FastAPI()
    app.include_router(upload.router, prefix="/api")
    return TestClient(app)


def test_ocr_image_endpoint_returns_image(ocr_preview_client):
    response = ocr_preview_client.get(
        f"/api/documents/id/doc-1/ocr-images/{'0' * 16}"
    )

    assert response.status_code == 200
    assert response.content == b"fake-jpeg"
    assert response.headers["content-type"] == "image/jpeg"


def test_ocr_image_endpoint_requires_document_reference(ocr_preview_client):
    response = ocr_preview_client.get(
        f"/api/documents/id/doc-1/ocr-images/{'1' * 16}"
    )

    assert response.status_code == 404


def test_ocr_image_endpoint_rejects_missing_document(ocr_preview_client):
    response = ocr_preview_client.get(
        f"/api/documents/id/missing-doc/ocr-images/{'0' * 16}"
    )

    assert response.status_code == 404


def test_ocr_image_endpoint_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OCR_IMAGE_PREVIEW_ENABLED", False)
    monkeypatch.setattr(settings, "OCR_IMAGE_DIR", str(tmp_path / "ocr_images"))

    app = FastAPI()
    app.include_router(upload.router, prefix="/api")
    client = TestClient(app)
    response = client.get(
        f"/api/documents/id/doc-1/ocr-images/{'0' * 16}"
    )

    assert response.status_code == 404
