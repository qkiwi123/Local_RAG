"""React SPA 静态托管回归测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import _SpaStaticFiles


def test_javascript_asset_uses_executable_mime_type(tmp_path):
    """Windows MIME 注册表不能把 Vite bundle 降级成 text/plain。"""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text(
        "document.body.dataset.loaded = 'true';",
        encoding="utf-8",
    )

    test_app = FastAPI()
    test_app.mount("/", _SpaStaticFiles(directory=str(tmp_path), html=True))

    with TestClient(test_app) as client:
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
