"""Offline W2 application acceptance via real ASGI routes, not a browser/launcher."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import traceback


DOCUMENTS = {
    "备份说明.txt": "知识库怎样完整备份：先停止服务，再复制整个项目目录。备份必须包含原文、metadata数据库、Chroma向量库、Sparse索引、OCR图片和配置。",
    "恢复说明.txt": "知识库恢复时保留完整备份，使用兼容版本，在新目录恢复整套data。不要覆盖正在运行的知识库，不要只复制一个数据库文件。",
    "移动说明.txt": "移动知识库之前停止服务，移动完整目录后从新目录启动。原文与索引必须随知识库保留，不能依赖旧目录或全局模型缓存。",
    "模型说明.txt": "知识库GPU模式启用Embedding和Reranker，CPU备用模式仍保留混合检索与重排。两份模型来自包内，切换设备要停止并重新启动。",
    "密钥说明.txt": "知识库问答和识图需要用户自行配置API密钥。没有密钥仍可上传文档和本地检索。新工作空间不会继承开发者的密钥。",
    "园艺对照.txt": "苹果树修剪主要在合适的季节进行，浇水频率需要根据土壤含水量与天气调整。这是与知识库备份无关的合成对照资料。",
}


async def exercise(root: Path, phase: str) -> dict:
    import httpx
    from backend.main import app, startup_event, shutdown_event
    from backend.config import settings
    from backend import portable
    from backend.services import embedding, reranker, sparse_rebuild, sparse_retriever, vector_store
    from backend.services.warmup import get_warmup_status

    fixture_path = root / "reports/w2-fixture.json"
    before = json.loads(fixture_path.read_text(encoding="utf-8")) if phase == "restart" else None
    await startup_event()
    try:
        deadline = time.monotonic() + 180
        while get_warmup_status()["status"] != "ready":
            if get_warmup_status()["status"] == "failed" or time.monotonic() > deadline:
                raise RuntimeError(f"Model warmup failed/timed out: {get_warmup_status()}")
            await asyncio.sleep(0.1)
        assert await asyncio.to_thread(sparse_rebuild.wait_for_idle, 90)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            current = await client.get("/api/settings")
            assert current.status_code == 200, current.text
            values = current.json()
            assert not values["llm"]["enabled"] and not values["vision"]["enabled"]
            assert not values["llm"]["api_key"]["configured"]
            assert values["retrieval"]["mode"] == "hybrid" and values["retrieval"]["reranker_enabled"]
            if phase == "first":
                assert vector_store.get_chunk_count() == 0
                assert sparse_rebuild.get_rebuild_status()["last_result"]["outcome"] == "awaiting_upload"
                assert not (root / "data/sparse_index/active.json").exists()
                uploaded = []
                for name, text in DOCUMENTS.items():
                    response = await client.post("/api/upload", files={"file": (name, text.encode("utf-8"), "text/plain")})
                    assert response.status_code == 200, response.text
                    uploaded.append(response.json())
                assert await asyncio.to_thread(sparse_rebuild.wait_for_idle, 90)
                assert sparse_retriever.get_runtime_status()["sparse_index_status"] == "ready"
                first_generation = json.loads((root / "data/sparse_index/active.json").read_text())["generation_id"]
                patch = await client.patch("/api/settings", json={"expected_revision": values["revision"],
                    "retrieval": {"bm25_k1": 1.6}})
                assert patch.status_code == 200, patch.text
                assert await asyncio.to_thread(sparse_rebuild.wait_for_idle, 90)
                deleted = uploaded[-1]["document_id"]
                response = await client.delete("/api/documents/id/" + deleted)
                assert response.status_code == 200, response.text
                assert await asyncio.to_thread(sparse_rebuild.wait_for_idle, 90)
                fixture = {"workspace_id": portable.read_workspace(root)["workspace_id"],
                           "documents": [item["document_id"] for item in uploaded[:-1]],
                           "deleted_document": deleted, "bm25_k1": 1.6,
                           "first_generation": first_generation, "chunks": vector_store.get_chunk_count()}
                fixture_path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
            else:
                assert portable.read_workspace(root)["workspace_id"] == before["workspace_id"]
                assert values["retrieval"]["bm25_k1"] == before["bm25_k1"]
                assert vector_store.get_chunk_count() == before["chunks"]
                listing = await client.get("/api/documents")
                assert listing.status_code == 200, listing.text
                assert before["deleted_document"] not in listing.text
                for document_id in before["documents"]:
                    assert document_id in listing.text
            query = await client.post("/api/chat", json={"question": "知识库怎样完整备份？"})
            assert query.status_code == 200, query.text
            answer = query.json()
            assert answer["citations"] and len(answer["citations"]) <= 5
            assert any(item["document_name"] == "备份说明.txt" for item in answer["citations"])
            snapshot = app.state.runtime_settings_manager.snapshot()
            index_status = sparse_retriever.get_runtime_status(k1=snapshot.retrieval.bm25_k1,
                                                               b=snapshot.retrieval.bm25_b)
            assert index_status["sparse_index_status"] == "ready", index_status
            emodel, rmodel = embedding._get_model(), reranker._get_model().model
            edevice = sorted({str(parameter.device) for parameter in emodel.parameters()})
            rdevice = sorted({str(parameter.device) for parameter in rmodel.parameters()})
            assert edevice == rdevice == [settings.EMBEDDING_DEVICE]
            expected_dtype = "torch.float16" if settings.RERANKER_USE_FP16 else "torch.float32"
            assert {str(parameter.dtype) for parameter in rmodel.parameters()} == {expected_dtype}
            generation = root / "data/sparse_index/generations" / index_status["sparse_index_generation"]
            manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["compatibility"]["oracle"]["passed"]
            assert manifest["contract"]["source"]["oracle"]["policy"] == "portable-snapshot-v1"
            return {"phase": phase, "device": settings.EMBEDDING_DEVICE, "reranker_dtype": expected_dtype,
                    "settings_without_key": "passed", "hybrid_chat_with_sources": "passed",
                    "citation_count": len(answer["citations"]), "chunk_count": vector_store.get_chunk_count(),
                    "index": index_status, "oracle": manifest["compatibility"]["oracle"],
                    "oracle_source": manifest["contract"]["source"]["oracle"],
                    "workspace": portable.read_workspace(root), "warmup": get_warmup_status(),
                    "restart_retains_settings_and_documents": phase == "restart"}
    finally:
        await shutdown_event()
        sparse_retriever.clear_cache()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cpu", "cuda:0"], required=True)
    parser.add_argument("--phase", choices=["first", "restart"], required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    if Path(args.report).name != args.report:
        parser.error("report must be a filename")
    root = Path(__file__).resolve().parents[1]
    if sys.flags.isolated != 1 or not Path(sys.executable).resolve().is_relative_to(root / "runtime"):
        raise ValueError("Use this W2 tree's embedded interpreter")
    if args.phase == "first" and ((root / "data/metadata.db").exists() or (root / "reports/w2-fixture.json").exists()):
        raise ValueError("First-run probe refuses an existing database or completed fixture")
    sys.dont_write_bytecode = True
    os.environ["RAG_DISTRIBUTION_MODE"] = "portable"
    os.environ["RAG_PORTABLE_DEVICE"] = args.device
    from validation.probe_w1 import verify_payload, audit_guard, assert_module_paths, native_runtime_paths

    report = {"status": "failed", "scope": "W2 real application routes, local synthetic data; not W3-W5"}
    events = []
    loop = None
    try:
        print("Checking W2 payload hashes...", flush=True)
        report["payload"] = verify_payload(root)
        from backend.config import settings  # Preflight BEFORE business imports.

        # Windows event-loop socket-pair setup is local plumbing, before the
        # no-network guard. Actual requests below are ASGI in-process, no TCP.
        loop = asyncio.new_event_loop()
        os.chdir(root / "temp")
        audit_guard(root, events)
        print(f"Real W2 {args.phase} application flow on {args.device}...", flush=True)
        report["application"] = loop.run_until_complete(exercise(root, args.phase))
        report["bundled_modules"] = assert_module_paths(root)
        report["native_runtime_paths"] = native_runtime_paths(root)
        assert not settings.LLM_ENABLED and not settings.OCR_ENABLED
        assert not events, events
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        if loop is not None:
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
    report["guard_events"] = events
    (root / "reports" / args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": args.report, "guard_events": events}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
