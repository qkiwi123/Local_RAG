"""显式联网验收：当前问答连接 → 图片测试 → 扫描 PDF 上传 → 预览。

运行：python -m scripts.validate_vision_settings --run-external
真实请求可能产生少量费用。只发送程序生成的测试图；入库使用新建隔离目录，
不改动生产设置、已上传文档或索引。默认只显示用法，不调用接口。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-external", action="store_true")
    args = parser.parse_args()
    if not args.run_external:
        parser.print_help()
        return 0

    from backend.config import settings
    from backend.services.runtime_settings import RuntimeSettingsManager
    from backend.models.settings_schemas import VisionSettings

    source = RuntimeSettingsManager().snapshot()
    log_root = Path("logs").resolve()
    log_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="vision-acceptance-", dir=log_root))
    # 独立进程运行，设置仅在本次验收进程内覆盖。
    for attribute, name in {
        "METADATA_DB_PATH": "metadata.db", "UPLOAD_DIR": "uploads",
        "CHROMA_PERSIST_DIR": "chroma", "VISION_CACHE_DIR": "vision_cache",
        "OCR_IMAGE_DIR": "ocr_images", "SPARSE_INDEX_ROOT": "sparse_index",
    }.items():
        setattr(settings, attribute, str(root / name))
    settings.PREWARM_MODELS = False
    settings.SPARSE_BACKEND = "rank_bm25_memory"
    settings.SPARSE_AUTO_REBUILD = False
    settings.OCR_IMAGE_PREVIEW_ENABLED = True
    settings.DEEPSEEK_API_KEY = source.api_key.get_secret_value() if source.api_key else ""
    settings.LLM_ENABLED = source.llm.enabled
    import torch
    settings.EMBEDDING_DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"

    from fastapi.testclient import TestClient
    from backend.main import app
    from PIL import Image, ImageDraw, ImageFont

    expected = ["RAG PAGE ALPHA 2718", "RAG PAGE BETA 3141"]
    pages = []
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 40) if font_path.exists() else ImageFont.load_default(size=40)
    for index, marker in enumerate(expected, 1):
        picture = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(picture)
        draw.text((70, 100), marker, fill="black", font=font)
        draw.text((70, 220), f"Document page {index}. Temperature: 25 C.", fill="black", font=font)
        if font_path.exists():
            draw.text((70, 340), f"视觉识别验收，第 {index} 页。实验温度为25度。", fill="black", font=font)
        pages.append(picture)
    buffer = BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "isolation_directory": str(root), "model": source.llm.model, "checks": {}}
    with TestClient(app) as client:
        manager = app.state.runtime_settings_manager
        vision = VisionSettings(enabled=True, source="follow_llm", pdf_scanned_enabled=True).model_dump()
        manager.update_settings({"llm": source.llm.model_dump(), "vision": vision}, expected_revision=manager.snapshot().revision)
        for task in ("recognize", "describe"):
            response = client.post("/api/settings/test-vision", json={"task": task, "vision": {}})
            result = response.json()
            report["checks"][f"probe_{task}"] = result
            print(json.dumps({"task": task, "status": response.status_code, "success": result.get("success"), "message": result.get("message")}, ensure_ascii=False), flush=True)
            if response.status_code != 200 or not result.get("success"):
                (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"REPORT={root / 'report.json'}", flush=True)
                return 1
        upload = client.post("/api/upload", files={"file": ("vision-acceptance.pdf", buffer.getvalue(), "application/pdf")})
        payload = upload.json()
        report["checks"]["upload"] = {"http_status": upload.status_code, **payload}
        if upload.status_code == 200:
            preview = client.get(f"/api/documents/id/{payload['document_id']}/preview")
            preview_json = preview.json()
            raw = json.dumps(preview_json, ensure_ascii=False)
            report["checks"]["preview"] = {"http_status": preview.status_code, "markers_found": [marker in raw for marker in expected]}
            (root / "preview.json").write_text(json.dumps(preview_json, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            report["checks"]["preview"] = {"markers_found": []}
        report["success"] = upload.status_code == 200 and not payload.get("warnings") and report["checks"]["preview"]["markers_found"] == [True, True]
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT={root / 'report.json'}", flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
