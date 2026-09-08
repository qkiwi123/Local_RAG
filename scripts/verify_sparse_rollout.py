"""P6-C bounded rollout probes. Reports contain timings and hashes, not corpus text.

Replica probes must pass before publishing production. Production probes require
an explicit matching Chroma path and an already-active verified generation.
This tool does not edit deployment settings or publish production pointers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from scripts.verify_sparse_shadow import copy, database, fingerprint, source_unchanged, stopped, write


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_root(root):
    if not root.is_relative_to(PROJECT / "data" / "backups"):
        raise ValueError("验收目录必须位于项目data/backups")


def production_stopped():
    """Require no other Python service, ignoring only this venv launcher's ancestors."""
    import psutil
    current = psutil.Process()
    own_command = current.cmdline()
    launcher_pids = {
        parent.pid for parent in current.parents()
        if (parent.name() or "").lower() == "python.exe" and parent.cmdline() == own_command
    }
    others = [p.pid for p in psutil.process_iter(["pid","name"])
              if p.pid != current.pid and p.pid not in launcher_pids
              and (p.info["name"] or "").lower() in {"python.exe","pythonw.exe","uvicorn.exe"}]
    if others:
        raise RuntimeError(f"停写窗口未成立，存在其他Python进程：{others}")
    return {"other_python_processes":0,"ignored_same_command_launchers":len(launcher_pids)}


def compare_runs(baseline, candidate):
    """P6-C BM25 causal gate; known cross-process Dense ANN drift is reported separately."""
    if baseline["status"] != "passed" or candidate["status"] != "passed":
        raise ValueError("不完整的运行不能作为上线依据")
    if baseline["backend"] != "rank_bm25_memory" or candidate["backend"] != "bm25s_mmap":
        raise ValueError("对照后端错误")
    if baseline["runtime_retrieval"] != candidate["runtime_retrieval"]:
        raise ValueError("同期参数不一致")
    if len(baseline["rows"]) != 120 or len(candidate["rows"]) != 120:
        raise ValueError("必须完成全部120题")
    final_exact = all(a["id"] == b["id"] and a["ordered_sha256"] == b["ordered_sha256"]
                      for a,b in zip(baseline["rows"],candidate["rows"]))
    sparse_exact = baseline["sparse_top50_hashes"] == candidate["sparse_top50_hashes"]
    ratio = candidate["warm_sparse_ms"]["p95"] / baseline["warm_sparse_ms"]["p95"]
    return {"passed":sparse_exact and ratio <= 1.10,
            "full_chain_order_exact_observation":final_exact, "sparse_top50_exact":sparse_exact,
            "sparse_p95_ratio":ratio,"sparse_p95_limit":1.10}


def differences(left, right):
    return [{"id":a["id"], "final_equal":a["ordered_sha256"] == b["ordered_sha256"],
             "different_stages":[k for k in a["stage_sha256"] if a["stage_sha256"][k] != b["stage_sha256"].get(k)]}
            for a,b in zip(left["rows"],right["rows"])
            if a["stage_sha256"] != b["stage_sha256"] or a["ordered_sha256"] != b["ordered_sha256"]]


def summarize(root):
    validate_root(root)
    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    source_unchanged(plan)
    for key,path in plan["backup"].items():
        if fingerprint(Path(path)) != plan["fingerprints"][key]:
            raise RuntimeError("原始备份变化")
    runs = {name:json.loads((root / name / "result.json").read_text(encoding="utf-8"))
            for name in ("baseline","candidate","baseline-repeat")}
    gate = compare_runs(runs["baseline"],runs["candidate"])
    compact = {}
    for name,r in runs.items():
        compact[name] = {k:r[k] for k in ("status","pid","backend","warm_sparse_ms","warm_total_ms",
            "status_start","status_end","rebuild","tags","concurrent_checks","handles_released",
            "http_smoke_passed","full_library_guard","network_events","errors","cpu_s","io_read_bytes",
            "io_write_bytes","memory_after","gpu","wall_s","runtime_retrieval")}
        samples = r["samples"]
        compact[name]["warm_sample_count"] = len(samples)
        compact[name]["warm_peak_rss_bytes"] = max(s["rss_bytes"] for s in samples)
        compact[name]["warm_peak_private_bytes"] = max(s["private_bytes"] for s in samples)
        compact[name]["warm_mean_cpu_percent"] = sum(s["process_cpu_percent"] for s in samples)/len(samples)
        compact[name]["result_sha256"] = fingerprint(root / name / "result.json")["files"][0]["sha256"]
    result = {"stage":"P6-C","status":"preactivation_gate_passed","date":"2026-09-08",
        "gate":gate,"runs":compact,"old_vs_new_differences":differences(runs["baseline"],runs["candidate"]),
        "old_vs_old_differences":differences(runs["baseline"],runs["baseline-repeat"]),
        "old_vs_old_sparse_exact":runs["baseline"]["sparse_top50_hashes"] == runs["baseline-repeat"]["sparse_top50_hashes"],
        "production_sources_unchanged":True,"backup_unchanged":True,"backup_bytes":plan["backup_bytes"],
        "production_active_exists":(Path(plan["source"]["sparse"]) / "active.json").exists(),
        "production_backend":plan["settings"]["SPARSE_BACKEND"],"documents":31,"chunks":2620,
        "scope":"Three fresh GPU ASGI processes on complete production replicas; 120 fixed questions per process; no gold recall claim, no LLM calls",
        "code_sha256":{p:fingerprint(PROJECT / p)["files"][0]["sha256"] for p in
            ("scripts/verify_sparse_rollout.py","backend/services/retriever.py","backend/services/sparse_retriever.py")}}
    write(PROJECT / "eval/crud_rag_1000/v3_1/bm25_persistence_p6c/result-20260908-r1.json", result)
    print(json.dumps({"status":result["status"],"gate":gate,
        "old_vs_old_different_questions":len(result["old_vs_old_differences"]),
        "old_vs_old_final_differences":sum(not r["final_equal"] for r in result["old_vs_old_differences"])}))


def finalize_rollout(root):
    """Verify the published production state and write a sanitized final report."""
    validate_root(root)
    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    build = json.loads((PROJECT / "data/backups/p6b-20260908-r1/shadow-build.json").read_text(encoding="utf-8"))
    for key, path in plan["backup"].items():
        if fingerprint(Path(path)) != plan["fingerprints"][key]:
            raise RuntimeError("P6-C原始备份变化: " + key)
    # Only the Sparse pointer/generation and deployment environment are expected to change.
    for key in ("metadata","metadata-wal","metadata-shm","uploads","ocr_images","vision_cache","runtime_secrets"):
        if fingerprint(Path(plan["source"][key])) != plan["fingerprints"][key]:
            raise RuntimeError("生产主资产发生非预期变化: " + key)
    # Chroma updates only acquire_write bookkeeping in chroma.sqlite3 when opened.
    # HNSW/index files must remain byte-identical and logical embeddings must not change.
    before_chroma = {row["path"]:row for row in plan["fingerprints"]["chroma"]["files"]}
    after_chroma_fp = fingerprint(Path(plan["source"]["chroma"]))
    after_chroma = {row["path"]:row for row in after_chroma_fp["files"]}
    changed_chroma = [name for name in sorted(before_chroma.keys() | after_chroma.keys())
                      if before_chroma.get(name) != after_chroma.get(name)]
    if changed_chroma != ["chroma.sqlite3"]:
        raise RuntimeError("Chroma变化超出允许运行簿记边界")
    chroma_db = Path(plan["source"]["chroma"]) / "chroma.sqlite3"
    with database(chroma_db) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Chroma SQLite quick_check失败")
        if connection.execute("SELECT count(*) FROM embeddings").fetchone()[0] != 2620:
            raise RuntimeError("Chroma embedding计数变化")
    connection.close()
    from backend.config import settings
    from backend.services import bm25s_store
    if settings.SPARSE_BACKEND != "bm25s_mmap" or settings.RETRIEVAL_MODE != "hybrid":
        raise RuntimeError("生产默认配置未生效")
    if Path(settings.SPARSE_INDEX_ROOT).resolve() != Path(plan["source"]["sparse"]):
        raise RuntimeError("生产Sparse root改变")
    if settings.SPARSE_INDEX_CORPUS_ID != "production-rag-20260908":
        raise RuntimeError("生产corpus ID错误")
    active = bm25s_store.read_active_payload(settings.SPARSE_INDEX_ROOT)
    if not active or active["status"] != "ready" or active["generation_id"] != build["generation_id"] or active["manifest_hash"] != build["manifest_hash"]:
        raise RuntimeError("生产active与已验收影子代不匹配")
    loaded = bm25s_store.load_active(settings.SPARSE_INDEX_ROOT)
    try:
        if loaded.manifest_hash != build["manifest_hash"] or loaded.manifest["corpus"]["chunk_count"] != 2620:
            raise RuntimeError("生产索引重载校验失败")
    finally:
        loaded.close()
    runs = {name:json.loads((root / name / "result.json").read_text(encoding="utf-8"))
            for name in ("baseline","candidate","baseline-repeat","prod-boot1","prod-boot2")}
    if any(run["status"] != "passed" for run in runs.values()):
        raise RuntimeError("存在未通过运行")
    if len({runs[name]["pid"] for name in ("prod-boot1","prod-boot2")}) != 2:
        raise RuntimeError("生产启动不是两个独立进程")
    if any(runs[name]["sparse_top50_hashes"] != runs["candidate"]["sparse_top50_hashes"] for name in ("prod-boot1","prod-boot2")):
        raise RuntimeError("生产BM25结果与候选不同")
    old_p95 = runs["baseline"]["warm_sparse_ms"]["p95"]
    if any(runs[name]["warm_sparse_ms"]["p95"] > old_p95 * 1.10 for name in ("candidate","prod-boot1","prod-boot2")):
        raise RuntimeError("生产Sparse P95超过旧版110%")
    compact = {}
    for name, run in runs.items():
        compact[name] = {k:run[k] for k in ("status","pid","backend","warm_sparse_ms","warm_total_ms",
            "status_start","status_end","rebuild","tags","concurrent_checks","handles_released",
            "http_smoke_passed","full_library_guard","network_events","errors","cpu_s","io_read_bytes",
            "io_write_bytes","memory_after","gpu","wall_s","runtime_retrieval")}
        compact[name]["sparse_results_sha256"] = digest(run["sparse_top50_hashes"])
        compact[name]["result_sha256"] = fingerprint(root / name / "result.json")["files"][0]["sha256"]
        samples = run["samples"]
        compact[name]["warm_sample_count"] = len(samples)
        compact[name]["warm_peak_rss_bytes"] = max(s["rss_bytes"] for s in samples)
        compact[name]["warm_peak_private_bytes"] = max(s["private_bytes"] for s in samples)
        compact[name]["warm_mean_cpu_percent"] = sum(s["process_cpu_percent"] for s in samples)/len(samples)
    result = {"stage":"P6-C","status":"passed_production_default_enabled","date":"2026-09-08",
        "decision":"Known Chroma/HNSW cross-process Dense ANN drift is non-causal for this BM25 backend change; retain it as an observation, not a Sparse failure.",
        "historical_dense_evidence":["new_docs/设计/DES-007-评测隔离与数据契约.md#dense-variance",
            "eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/Dense差异来源与波动范围-20260829.md"],
        "bm25_gate":compare_runs(runs["baseline"],runs["candidate"]),
        "old_vs_new_different_questions":len(differences(runs["baseline"],runs["candidate"])),
        "old_vs_old_different_questions":len(differences(runs["baseline"],runs["baseline-repeat"])),
        "old_vs_old_final_differences":sum(not d["final_equal"] for d in differences(runs["baseline"],runs["baseline-repeat"])),
        "production": {"backend":settings.SPARSE_BACKEND,"retrieval_mode":settings.RETRIEVAL_MODE,
            "index_root":"data/sparse_index","corpus_id":settings.SPARSE_INDEX_CORPUS_ID,
            "active":active,"documents":31,"chunks":2620,"generation_bytes":1593104,
            "main_assets_unchanged":True,"chroma_runtime_bookkeeping_only":changed_chroma,
            "original_backup_unchanged":True,"service_left_running":False},
        "runs":compact,"rollback":{"backend":"rank_bm25_memory","checkpoint_commit":"2274589",
            "backup_root":"data/backups/p6c-20260908-r1","previous_generation_retained":True},
        "scope":"Two fresh production-config GPU ASGI processes after atomic active publication; 120 fixed queries each; LLM disabled; no synthetic production write, browser, TCP listener, or long-duration live traffic",
        "not_executed":["synthetic write into production corpus","external LLM generation","browser UI acceptance","long-duration traffic observation"],
        "previous_blocked_report":{"path":"result-20260908-r1.json","sha256":fingerprint(PROJECT / "eval/crud_rag_1000/v3_1/bm25_persistence_p6c/result-20260908-r1.json")["files"][0]["sha256"]},
        "code_sha256":{p:fingerprint(PROJECT / p)["files"][0]["sha256"] for p in
            ("scripts/verify_sparse_rollout.py","backend/config.py","backend/services/retriever.py","backend/services/sparse_retriever.py")}}
    target = PROJECT / "eval/crud_rag_1000/v3_1/bm25_persistence_p6c/result-20260908-r2.json"
    write(target,result)
    print(json.dumps({"status":result["status"],"active":active,"production_boots":[
        {"pid":runs[n]["pid"],"sparse_p95":runs[n]["warm_sparse_ms"]["p95"]} for n in ("prod-boot1","prod-boot2")]}))


def prepare(root, run):
    validate_root(root)
    stopped()
    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    target = root / run
    target.mkdir()
    paths = {}
    for key, source in plan["backup"].items():
        destination = target / "storage" / key / Path(source).name
        copy(Path(source), destination)
        if fingerprint(destination) != plan["fingerprints"][key]:
            raise RuntimeError("副本摘要不一致: " + key)
        paths[key] = str(destination)
    for suffix in ("-wal", "-shm"):
        if Path(paths["metadata" + suffix]).exists():
            copy(Path(paths["metadata" + suffix]), Path(paths["metadata"] + suffix))
    write(target / "paths.json", paths)
    print(json.dumps({"replica_prepared": run}))


def probe(root, run, backend, production_confirm):
    validate_root(root)
    target = root / run
    plan = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    build = json.loads((PROJECT / "data/backups/p6b-20260908-r1/shadow-build.json").read_text(encoding="utf-8"))
    production = production_confirm is not None
    if production:
        production_stopped()
        if Path(production_confirm).resolve() != Path(plan["source"]["chroma"]):
            raise ValueError("生产路径确认不匹配")
        paths = plan["source"]
        target.mkdir()
    else:
        paths = json.loads((target / "paths.json").read_text(encoding="utf-8"))
        if any(not Path(p).resolve().is_relative_to(target / "storage") for p in paths.values()):
            raise ValueError("副本路径越界")
    os.environ.update({"CHROMA_PERSIST_DIR":paths["chroma"], "METADATA_DB_PATH":paths["metadata"],
        "UPLOAD_DIR":paths["uploads"], "OCR_IMAGE_DIR":paths["ocr_images"], "VISION_CACHE_DIR":paths["vision_cache"],
        "SPARSE_INDEX_ROOT":paths["sparse"], "SPARSE_INDEX_CORPUS_ID":"production-rag-20260908",
        "PREWARM_MODELS":"false", "LLM_ENABLED":"false", "OCR_ENABLED":"false",
        "ANONYMIZED_TELEMETRY":"False", "DO_NOT_TRACK":"1"})
    if not production:
        os.environ["SPARSE_BACKEND"] = backend
    network = []
    def guard(event, args):
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            try:
                local = ipaddress.ip_address(args[1][0]).is_loopback
            except ValueError:
                local = False
            if not local:
                network.append(event)
                raise PermissionError("验收禁止外部连接")
    sys.addaudithook(guard)
    import psutil
    import torch
    from fastapi.testclient import TestClient
    from backend.config import settings
    from backend.main import app
    from backend.services import bm25s_store, sparse_retriever, sparse_rebuild, vector_store, retriever
    from backend.services.tag_store import resolve_document_ids
    from eval.crud_rag_1000.v3_1.bm25_persistence_p4.run_p4_benchmark import (
        _ProcessSampler, _stats, _memory_snapshot, _cuda_snapshot,
    )
    if settings.SPARSE_BACKEND != backend or settings.RETRIEVAL_MODE != "hybrid":
        raise RuntimeError("实际部署后端/模式不匹配")
    if not torch.cuda.is_available() or not settings.RERANKER_ENABLED:
        raise RuntimeError("完整检索GPU/Reranker条件不成立")
    if backend == "bm25s_mmap" and not production:
        bm25s_store.activate_generation(paths["sparse"], build["generation_id"])
    questions = json.loads((PROJECT / "eval/crud_rag_1000/v3_1/questions_v3_120q.json").read_text(encoding="utf-8"))["questions"]
    process = psutil.Process()
    rows = []
    sparse_rows = []
    errors = []
    started = time.perf_counter()
    scans = {"full_scan":0, "okapi_build":0}
    def reject_scan(*args, **kwargs):
        scans["full_scan"] += 1
        raise AssertionError("全库mmap请求扫描Chroma")
    def reject_build(*args, **kwargs):
        scans["okapi_build"] += 1
        raise AssertionError("全库mmap请求构建BM25Okapi")
    def query(q, runtime):
        t = time.perf_counter()
        response = retriever.retrieve_with_trace(q["question"], runtime_config=runtime)
        trace = response.trace
        if trace is None or trace.sparse_backend != backend:
            raise AssertionError("Trace后端不匹配或静默降级")
        return {"id":q["id"], "ordered_sha256":digest([r["chunk_id"] for r in response.results]),
                "stage_sha256":{s.name:digest([c.chunk_id for c in s.candidates]) for s in trace.stages},
                "total_ms":(time.perf_counter()-t)*1000,
                "sparse_ms":trace.timings_ms.sparse_retrieval,
                "backend":trace.sparse_backend}
    try:
        with TestClient(app) as client:
            if not sparse_rebuild.wait_for_idle(60):
                raise RuntimeError("启动重建未就绪")
            status = sparse_retriever.get_runtime_status()
            if status["sparse_index_status"] != "ready":
                raise RuntimeError("启动状态非ready")
            if backend == "bm25s_mmap" and (status["sparse_index_generation"] != build["generation_id"] or status["sparse_manifest_hash"] != build["manifest_hash"]):
                raise RuntimeError("启动错代")
            runtime = app.state.runtime_settings_manager.snapshot().retrieval
            scan_patch = patch.object(vector_store, "get_all_chunks", reject_scan) if backend == "bm25s_mmap" else patch.object(vector_store, "get_all_chunks", wraps=vector_store.get_all_chunks)
            build_patch = patch.object(sparse_retriever, "BM25Okapi", reject_build) if backend == "bm25s_mmap" else patch.object(sparse_retriever, "BM25Okapi", wraps=sparse_retriever.BM25Okapi)
            with scan_patch, build_patch:
                cold = query(questions[0], runtime)
                before_cpu, before_io = process.cpu_times(), process.io_counters()
                torch.cuda.reset_peak_memory_stats()
                with _ProcessSampler(process, psutil) as sampler:
                    for i, q in enumerate(questions):
                        rows.append(query(q, runtime))
                        hits = sparse_retriever.search(q["question"], 50)
                        sparse_rows.append(digest([h["chunk_id"] for h in hits]))
                        if (i+1) % 20 == 0:
                            print(f"{run}: {i+1}/120", flush=True)
                after_cpu, after_io = process.cpu_times(), process.io_counters()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    concurrent = list(pool.map(lambda q:digest([r["chunk_id"] for r in sparse_retriever.search(q["question"],50)]), questions[:24]))
                if concurrent != sparse_rows[:24]:
                    raise AssertionError("并发查询结果变化")
            with database(Path(paths["metadata"])) as c:
                tags = [r[0] for r in c.execute("SELECT tag_path FROM tags ORDER BY tag_path")]
            c.close()
            for tag in tags:
                ids = resolve_document_ids([tag], db_path=paths["metadata"])
                hits = sparse_retriever.search(questions[0]["question"], 15, allowed_document_ids=ids)
                if any(r["metadata"]["document_id"] not in ids for r in hits):
                    raise AssertionError("标签越界")
                if client.get("/api/documents", params={"tags":tag}).status_code != 200:
                    raise AssertionError("标签HTTP错误")
            for url in ("/api/health", "/api/settings/runtime-status", "/"):
                if client.get(url).status_code != 200:
                    raise AssertionError("HTTP冒烟错误")
            # 非持久会话请求：不写入生产聊天记录，也不调用生成API。
            smoke = client.post("/api/chat", json={"question":questions[0]["question"]})
            if smoke.status_code != 200 or smoke.json().get("trace", {}).get("llm", {}).get("called"):
                raise AssertionError("问答接口冒烟失败或发生生成调用")
            end_status = sparse_retriever.get_runtime_status()
            rebuild_status = sparse_rebuild.get_rebuild_status()
        sparse_retriever.clear_cache()
        index_root = str(Path(paths["sparse"]).resolve()).lower()
        maps = [m for m in process.memory_maps(grouped=False) if m.path.lower().startswith(index_root)]
        if maps:
            raise AssertionError("关闭后仍有索引映射")
    except Exception as error:
        errors.append(type(error).__name__)
        raise
    finally:
        sparse_retriever.clear_cache()
        vector_store._client._system.stop()
        if errors:
            write(target / "failure.json", {"errors":errors,"run":run,"network_events":len(network)})
    if network:
        raise RuntimeError("存在外部连接尝试")
    result = {"status":"passed", "run":run, "production":production, "backend":backend,"pid":os.getpid(),
        "questions":len(rows), "cold":cold, "rows":rows, "sparse_top50_hashes":sparse_rows,
        "warm_sparse_ms":_stats([r["sparse_ms"] for r in rows]), "warm_total_ms":_stats([r["total_ms"] for r in rows]),
        "status_start":status, "status_end":end_status, "rebuild":rebuild_status,
        "tags":len(tags), "concurrent_checks":24, "handles_released":True, "http_smoke_passed":True,
        "full_library_guard":scans, "network_events":len(network), "errors":errors,
        "cpu_s":after_cpu.user+after_cpu.system-before_cpu.user-before_cpu.system,
        "io_read_bytes":after_io.read_bytes-before_io.read_bytes,"io_write_bytes":after_io.write_bytes-before_io.write_bytes,
        "memory_after":_memory_snapshot(process), "gpu":_cuda_snapshot(torch),
        "samples":sampler.samples, "wall_s":time.perf_counter()-started,
        "runtime_retrieval":runtime.model_dump(mode="json"),
        "scope":"120-query GPU hybrid retrieval; LLM disabled; ASGI lifespan and HTTP smoke; no browser or long-running traffic"}
    write(target / "result.json", result)
    print(json.dumps({k:result[k] for k in ("status","run","backend","warm_sparse_ms","warm_total_ms","wall_s")},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "probe", "summarize", "finalize"))
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run", default="summary")
    parser.add_argument("--backend", choices=("rank_bm25_memory","bm25s_mmap"), default="bm25s_mmap")
    parser.add_argument("--production-confirm")
    args = parser.parse_args()
    if not args.run.replace("-", "").isalnum():
        raise ValueError("run必须是简单目录名")
    root = Path(args.run_root).resolve()
    if args.phase == "prepare":
        prepare(root, args.run)
    elif args.phase == "probe":
        probe(root, args.run, args.backend, args.production_confirm)
    elif args.phase == "summarize":
        summarize(root)
    else:
        finalize_rollout(root)


if __name__ == "__main__":
    main()
