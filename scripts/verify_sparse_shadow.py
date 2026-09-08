"""P6-B停写备份、只读预检和隔离恢复验收；不会发布生产活动指针。"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def fingerprint(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("备份源不允许链接")
    if not path.exists():
        return {"exists": False}
    rows = []
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for item in files:
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("备份源不允许链接")
        if item.is_file():
            with item.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            rows.append({"path": item.relative_to(path).as_posix() if path.is_dir() else item.name,
                         "bytes": item.stat().st_size, "sha256": digest})
    return {"exists": True, "files": rows, "bytes": sum(r["bytes"] for r in rows)}


def copy(source, target):
    if source.is_dir():
        shutil.copytree(source, target)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def stopped():
    import psutil

    # 当前机器的本项目只用Python服务；未知Python进程存在时拒绝停写声明。
    others = []
    for process in psutil.process_iter(["pid", "name"]):
        if process.info["pid"] != os.getpid() and (process.info["name"] or "").lower() in {"python.exe", "pythonw.exe", "uvicorn.exe"}:
            others.append(process.info["pid"])
    if others:
        raise RuntimeError(f"停写窗口未成立，存在其他Python进程：{others}")
    return {"other_python_processes": 0, "at": time.time()}


def database(path):
    c = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def backup(root):
    from backend.config import settings as s

    quiet = stopped()
    if root.exists():
        raise FileExistsError("运行目录已存在，拒绝覆盖备份")
    assets = {"chroma": Path(s.CHROMA_PERSIST_DIR).resolve(),
              "metadata": Path(s.METADATA_DB_PATH).resolve(),
              "uploads": Path(s.UPLOAD_DIR).resolve(),
              "ocr_images": Path(s.OCR_IMAGE_DIR).resolve(),
              "vision_cache": Path(s.VISION_CACHE_DIR).resolve(),
              "sparse": Path(s.SPARSE_INDEX_ROOT).resolve(),
              "environment": PROJECT / ".env",
              "runtime_secrets": Path(s.METADATA_DB_PATH).resolve().with_name("runtime_secrets.bin")}
    for suffix in ("-wal", "-shm"):
        assets["metadata" + suffix] = Path(str(assets["metadata"]) + suffix)
    if any(root == p or root.is_relative_to(p) for p in assets.values()):
        raise ValueError("备份目录不能包含于源资产")
    before = {key: fingerprint(path) for key, path in assets.items()}
    with database(assets["metadata"]) as c:
        if [tuple(r) for r in c.execute("PRAGMA quick_check")] != [("ok",)]:
            raise RuntimeError("SQLite quick_check失败")
        counts = [dict(r) for r in c.execute("SELECT status, count(*) AS documents, sum(chunk_count) AS chunks FROM documents GROUP BY status")]
        runtime = c.execute("SELECT revision,payload_json FROM runtime_settings WHERE singleton_id=1").fetchone()
        retrieval = json.loads(runtime["payload_json"]).get("retrieval", {}) if runtime else {}
    c.close()
    required = sum(x.get("bytes", 0) for x in before.values()) * 5
    if shutil.disk_usage(PROJECT).free < required:
        raise RuntimeError("不足以保留备份和隔离恢复副本")
    root.mkdir(parents=True)
    destinations = {}
    for key, source in assets.items():
        destination = root / "backup" / key / source.name
        copy(source, destination)
        if fingerprint(destination) != before[key]:
            raise RuntimeError("备份摘要不匹配：" + key)
        destinations[key] = str(destination)
    after = {key: fingerprint(path) for key, path in assets.items()}
    if before != after:
        raise RuntimeError("备份期间源变化")
    keys = ("CHUNK_STRATEGY", "CHUNK_SIZE", "CHUNK_OVERLAP", "CHUNK_MAX_TOKENS",
            "SPARSE_BACKEND", "RETRIEVAL_MODE", "BM25_K1", "BM25_B")
    plan = {"source": {k: str(p) for k,p in assets.items()}, "backup": destinations,
            "fingerprints": before, "settings": {k: getattr(s,k) for k in keys},
            "runtime_retrieval": retrieval, "runtime_revision": runtime["revision"] if runtime else 0,
            "stopped": quiet, "source_unchanged": True, "document_counts": counts,
            "backup_bytes": sum(v.get("bytes",0) for v in before.values()), "created_at": time.time()}
    write(root / "backup-manifest.json", plan)
    print(json.dumps({k: plan[k] for k in ("document_counts", "backup_bytes", "settings", "runtime_retrieval", "runtime_revision", "source_unchanged")},ensure_ascii=False))


def source_unchanged(plan):
    stopped()
    # P6-B可添加影子代；原活动指针必须保持原样。
    for key, path in plan["source"].items():
        if key == "sparse":
            before = plan["fingerprints"][key]
            active = Path(path) / "active.json"
            old_active = Path(plan["backup"][key]) / "active.json"
            if fingerprint(active) != fingerprint(old_active):
                raise RuntimeError("生产active指针改变")
            if before.get("exists"):
                for row in before["files"]:
                    candidate = Path(path) / row["path"]
                    if fingerprint(candidate)["files"][0]["sha256"] != row["sha256"]:
                        raise RuntimeError("原Sparse文件改变")
        elif fingerprint(Path(path)) != plan["fingerprints"][key]:
            raise RuntimeError("生产源资产变化：" + key)
    return True


def inspect(root):
    from scripts.rebuild_sparse_index import _metadata_evidence, _verify_registered_chunks
    import chromadb

    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    source_unchanged(plan)
    working = root / "inspection" / "chroma"
    shutil.copytree(plan["backup"]["chroma"], working)
    client = chromadb.PersistentClient(path=str(working))
    try:
        collection = client.get_collection("knowledge_base")
        result = collection.get(include=["documents","metadatas"])
        chunks = [{"chunk_id": i,"content": t,"metadata": m or {}}
                  for i,t,m in zip(result["ids"],result["documents"],result["metadatas"])]
        _verify_registered_chunks(chunks, _metadata_evidence(Path(plan["backup"]["metadata"])))
        if collection.count() != len(chunks):
            raise RuntimeError("Chroma计数不一致")
        with database(Path(plan["backup"]["metadata"])) as c:
            rows = c.execute("SELECT document_id,stored_name FROM documents WHERE status='ready'").fetchall()
            missing = sum(not (Path(plan["backup"]["uploads"]) / r["stored_name"]).is_file() for r in rows)
            tags = c.execute("SELECT count(*) FROM tags").fetchone()[0]
            schema = [dict(r) for r in c.execute("SELECT * FROM schema_meta")]
        c.close()
        if missing:
            raise RuntimeError("备份缺失就绪文档原文")
        report = {"status":"passed","documents":len(rows),"chunks":len(chunks),"tags":tags,
                  "parser_versions":dict(Counter(r["metadata"].get("parser_version","missing") for r in chunks)),
                  "schema":schema,"uploads_missing":missing,"source_unchanged":source_unchanged(plan)}
        write(root / "preflight.json", report)
        print(json.dumps(report,ensure_ascii=False))
    finally:
        client._system.stop()


def prepare_restore(root):
    from backend.services import bm25s_store

    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    build = json.loads((root / "shadow-build.json").read_text(encoding="utf-8"))
    source_unchanged(plan)
    if build["status"] != "passed" or build["published"] or not build["source_unchanged"]:
        raise RuntimeError("影子构建未通过或已经发布")
    restored = root / "restore"
    restored.mkdir()
    paths = {}
    for key, value in plan["backup"].items():
        source = Path(value)
        target = restored / key / source.name
        copy(source, target)
        if fingerprint(target) != plan["fingerprints"][key]:
            raise RuntimeError("恢复副本不匹配：" + key)
        paths[key] = str(target)
    for suffix in ("-wal","-shm"):
        source = Path(paths["metadata" + suffix])
        if source.exists():
            shutil.copy2(source, paths["metadata"] + suffix)
    if Path(paths["runtime_secrets"]).exists():
        shutil.copy2(paths["runtime_secrets"], Path(paths["metadata"]).with_name("runtime_secrets.bin"))
    generation = Path(build["generation_dir"])
    sparse = Path(paths["sparse"])
    destination = sparse / "generations" / build["generation_id"]
    shutil.copytree(generation, destination)
    if fingerprint(generation) != fingerprint(destination):
        raise RuntimeError("影子generation恢复摘要不同")
    loaded = bm25s_store.load_generation(destination)
    try:
        if loaded.manifest_hash != build["manifest_hash"]:
            raise RuntimeError("恢复generation与构建报告不匹配")
        corpus_id = loaded.manifest["contract"]["corpus_id"]
    finally:
        loaded.close()
    # 仅在restore子目录发布用于演练；生产没有active.json。
    bm25s_store.activate_generation(sparse, build["generation_id"])
    write(root / "restore-plan.json", {"paths":paths,"generation_id":build["generation_id"],
          "corpus_id":corpus_id,"assets_hash_matched":True,
          "source_unchanged":source_unchanged(plan)})
    print(json.dumps({"restore_assets_hash_matched":True,"production_unchanged":True}))


def service_probe(root, pass_id, backend):
    restored = json.loads((root / "restore-plan.json").read_text(encoding="utf-8"))
    paths = restored["paths"]
    for path in paths.values():
        if not Path(path).resolve().is_relative_to(root / "restore"):
            raise ValueError("恢复服务路径必须全部位于本轮restore目录")
    # 必须在导入backend配置及vector_store之前隔离所有存储路径。
    os.environ.update({"CHROMA_PERSIST_DIR":paths["chroma"],"METADATA_DB_PATH":paths["metadata"],
        "UPLOAD_DIR":paths["uploads"],"OCR_IMAGE_DIR":paths["ocr_images"],"VISION_CACHE_DIR":paths["vision_cache"],
        "SPARSE_INDEX_ROOT":paths["sparse"],"SPARSE_INDEX_CORPUS_ID":restored["corpus_id"],
        "SPARSE_BACKEND":backend,"SPARSE_AUTO_REBUILD":"true","PREWARM_MODELS":"false",
        "LLM_ENABLED":"false","OCR_ENABLED":"false","ANONYMIZED_TELEMETRY":"False",
        "DO_NOT_TRACK":"1","EMBEDDING_DEVICE":"cpu","RERANKER_DEVICE":"cpu","RERANKER_USE_FP16":"false"})
    import ipaddress
    import socket
    network = []
    def deny_external(event, args):
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            # Windows事件循环用loopback建立内部socketpair，属于本机通信。
            try:
                local = ipaddress.ip_address(args[1][0]).is_loopback
            except ValueError:
                local = False
            if not local:
                network.append(event)
                raise PermissionError("恢复验证禁止外部网络连接")
    sys.addaudithook(deny_external)
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.config import settings
    from backend.services import sparse_retriever, sparse_rebuild, vector_store, bm25s_store
    from backend.services.tag_store import resolve_document_ids
    from backend.services.sparse_tokenizer import tokenize

    questions = json.loads((PROJECT / "eval/crud_rag_1000/v3_1/questions_v3_120q.json").read_text(encoding="utf-8"))["questions"]
    started = time.perf_counter()
    def run_queries():
        return [[row["chunk_id"] for row in sparse_retriever.search(q["question"], k)]
                for k in (1,5,15,50) for q in questions]

    with TestClient(app) as client:
        if not sparse_rebuild.wait_for_idle(30):
            raise RuntimeError("恢复服务索引启动未就绪")
        runtime = client.get("/api/settings/runtime-status")
        if runtime.status_code != 200:
            raise RuntimeError("恢复服务状态接口失败")
        status = sparse_retriever.get_runtime_status()
        if status["sparse_index_status"] != "ready":
            raise RuntimeError("恢复服务Sparse未ready")
        results = run_queries()
        expected_file = root / "restore-query-hashes.json"
        signature = hashlib.sha256(json.dumps(results).encode()).hexdigest()
        if expected_file.exists():
            if signature != json.loads(expected_file.read_text())["sha256"]:
                raise RuntimeError("跨新进程/回滚查询结果变化")
        else:
            write(expected_file, {"sha256":signature})

        # 旧后端是相同生产副本上的独立对照，不使用题库gold标签计算质量。
        settings.SPARSE_BACKEND = "rank_bm25_memory"
        sparse_retriever.clear_cache()
        reference = run_queries()
        if reference != results:
            raise RuntimeError("恢复后的新旧后端有序结果不同")
        settings.SPARSE_BACKEND = backend
        sparse_retriever.clear_cache()
        with database(Path(paths["metadata"])) as c:
            tags = [r[0] for r in c.execute("SELECT tag_path FROM tags ORDER BY tag_path")]
        c.close()
        chunks = vector_store.get_all_chunks()
        checked_tags = 0
        for tag in tags:
            ids = resolve_document_ids([tag], db_path=paths["metadata"])
            scoped = [r for r in chunks if r["metadata"]["document_id"] in ids]
            query = scoped[0]["content"][:100] if scoped else questions[0]["question"]
            diagnostics = {}
            actual = sparse_retriever.search(query,15,allowed_document_ids=ids,diagnostics=diagnostics)
            settings.SPARSE_BACKEND = "rank_bm25_memory"
            expected = sparse_retriever.search(query,15,allowed_document_ids=ids)
            settings.SPARSE_BACKEND = backend
            if actual != expected or any(r["metadata"]["document_id"] not in ids for r in actual):
                raise RuntimeError("标签子语料回归")
            response = client.get("/api/documents",params={"tags":tag})
            if response.status_code != 200:
                raise RuntimeError("标签文档接口失败")
            checked_tags += 1
        if sparse_retriever.search("索引",5,allowed_document_ids=[]) != []:
            raise RuntimeError("空标签范围不为空")
        with ThreadPoolExecutor(max_workers=4) as pool:
            actual = list(pool.map(lambda q:[r["chunk_id"] for r in sparse_retriever.search(q["question"],15)], questions[:24]))
        if actual != results[240:264]:
            raise RuntimeError("并发读取排序不一致")

        switched = False
        if backend == "bm25s_mmap" and pass_id == "boot1":
            contract = sparse_retriever.expected_contract(k1=settings.BM25_K1,b=settings.BM25_B)
            candidate = bm25s_store.build_generation(chunks=chunks,target_root=paths["sparse"],
                corpus_id=restored["corpus_id"],tokenize=tokenize,tokenizer=contract["tokenizer"],
                chunking=contract["chunking"],source={"origin":"p6b-isolated-switch"},
                generation_id="restore-switch-candidate",oracle_questions=questions,publish=False)
            bm25s_store.activate_generation(paths["sparse"],candidate["generation_id"],allow_replace=True)
            if run_queries() != results:
                raise RuntimeError("候选generation切换改变结果")
            bm25s_store.activate_generation(paths["sparse"],restored["generation_id"],allow_replace=True)
            if run_queries() != results:
                raise RuntimeError("恢复原影子代改变结果")
            switched = True
        sparse_retriever.clear_cache()
        generation = Path(paths["sparse"]) / "generations" / restored["generation_id"]
        # 验证Windows映射已释放，只操作隔离恢复目录中的单个索引文件。
        probe_file = generation / "data.csc.index.npy"
        renamed = generation / "data.csc.index.npy.released"
        probe_file.rename(renamed)
        renamed.rename(probe_file)
    sparse_retriever.clear_cache()
    vector_store._client._system.stop()
    if network:
        raise RuntimeError("恢复验证发生出站连接尝试")
    report = {"status":"passed","pass_id":pass_id,"pid":os.getpid(),"backend":backend,
              "query_count":120,"k_values":[1,5,15,50],"exact_order_checks":len(results),
              "query_signature":signature,"tag_scopes":checked_tags,"concurrent_checks":24,
              "candidate_switch_and_return":switched,"handles_released":True,"network_events":0,
              "wall_s":time.perf_counter()-started,"service_startup_shutdown":True}
    write(root / (pass_id + ".json"),report)
    print(json.dumps(report))


def finalize(root):
    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    build = json.loads((root / "shadow-build.json").read_text(encoding="utf-8"))
    preflight = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
    boots = [json.loads((root / (name + ".json")).read_text()) for name in ("boot1","boot2","rollback")]
    source_unchanged(plan)
    for key, path in plan["backup"].items():
        if fingerprint(Path(path)) != plan["fingerprints"][key]:
            raise RuntimeError("原始备份在演练中被改变：" + key)
    if any(r["status"] != "passed" for r in boots) or len({r["pid"] for r in boots}) != 3:
        raise RuntimeError("独立进程启动验收未通过")
    if len({r["query_signature"] for r in boots}) != 1:
        raise RuntimeError("跨进程结果不一致")
    from backend.services import bm25s_store
    loaded = bm25s_store.load_generation(build["generation_dir"])
    try:
        if loaded.manifest_hash != build["manifest_hash"]:
            raise RuntimeError("生产影子manifest变化")
        corpus = loaded.manifest["corpus"]
        contract = loaded.manifest["contract"]
    finally:
        loaded.close()
    summary = {"status":"passed","stage":"P6-B","date":"2026-09-08",
        "preflight":preflight,"backup_bytes":plan["backup_bytes"],
        "backup_unchanged":True,"production_sources_unchanged":True,
        "production_active_exists":(Path(plan["source"]["sparse"]) / "active.json").exists(),
        "production_backend":plan["settings"]["SPARSE_BACKEND"],
        "retrieval_mode":plan["settings"]["RETRIEVAL_MODE"],
        "generation_id":build["generation_id"],"manifest_hash":build["manifest_hash"],
        "corpus_id":contract["corpus_id"],"corpus":corpus,
        "chunking":contract["chunking"],"bm25":contract["bm25"],
        "index_bytes":build["index_bytes"],
        "generation_bytes":fingerprint(Path(build["generation_dir"]))["bytes"],
        "build_wall_s":build["wall_s"],"build_total_wall_s":build["total_wall_s"],
        "reload_oracle":build["reload_oracle"],"restore_processes":boots,
        "environment":"Windows, isolated ASGI lifespan in three fresh Python processes; no external network",
        "not_executed":["P6-C production activation","GPU/LLM end-to-end performance benchmark","10000-document expansion"],
        "initial_probe_issue":"Windows asyncio loopback socketpair was blocked by CLI network guard; service harness now permits loopback only",
        "code_sha256":{name:fingerprint(PROJECT / name)["files"][0]["sha256"] for name in (
            "scripts/verify_sparse_shadow.py","scripts/rebuild_sparse_index.py","backend/services/bm25s_store.py",
            "backend/services/sparse_rebuild.py","backend/services/sparse_lifecycle.py","backend/services/sparse_retriever.py")}}
    public = PROJECT / "eval/crud_rag_1000/v3_1/bm25_persistence_p6b/result-20260908-r1.json"
    write(public,summary)
    print(json.dumps({"status":"passed","report":str(public),"generation_bytes":summary["generation_bytes"],
        "backup_unchanged":True,"production_unchanged":True}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=("backup","inspect","verify-source","prepare-restore","service","finalize"))
    p.add_argument("--run-root", required=True)
    p.add_argument("--pass-id", choices=("boot1","boot2","rollback"))
    p.add_argument("--backend", choices=("bm25s_mmap","rank_bm25_memory"), default="bm25s_mmap")
    args = p.parse_args()
    root = Path(args.run_root).resolve()
    if not root.is_relative_to(PROJECT / "data" / "backups"):
        raise ValueError("运行目录必须在项目data/backups中")
    if args.phase == "backup":
        backup(root)
    elif args.phase == "inspect":
        inspect(root)
    elif args.phase == "prepare-restore":
        prepare_restore(root)
    elif args.phase == "service":
        if not args.pass_id:
            raise ValueError("service阶段需要pass-id")
        service_probe(root,args.pass_id,args.backend)
    elif args.phase == "finalize":
        finalize(root)
    else:
        plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
        print(json.dumps({"source_unchanged":source_unchanged(plan)}))


if __name__ == "__main__":
    main()
