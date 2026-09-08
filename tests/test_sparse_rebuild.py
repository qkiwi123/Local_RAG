"""P6-A后台重建协调器测试：单实例、合并、绑定复核、自愈与并发安全。"""

import threading
from pathlib import Path

import pytest

from backend.services import (
    bm25s_store,
    sparse_lifecycle,
    sparse_rebuild,
    sparse_retriever,
    vector_store,
)
from backend.services.sparse_index import SparseIndexDirtyError
from backend.services.sparse_tokenizer import tokenize as pure_tokenize


def _chunk(chunk_id: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "document_id": f"doc-{chunk_id}",
            "document_name": f"{chunk_id}.txt",
            "page_number": 1,
        },
    }


CHUNKS_V1 = [
    _chunk("c1", "二叉树前序遍历"),
    _chunk("c2", "进程调度算法"),
    _chunk("c3", "数据库索引结构"),
]
CHUNKS_V2 = [
    _chunk("c1", "二叉树前序遍历"),
    _chunk("c2", "进程调度算法"),
    _chunk("c3", "数据库索引结构"),
    _chunk("c4", "向量数据库与检索优化"),
]


@pytest.fixture(autouse=True)
def _worker_isolation(monkeypatch):
    monkeypatch.setattr(sparse_retriever, "_bound_revision", 0)
    monkeypatch.setattr(vector_store, "_index_revision", 0)
    sparse_rebuild.reset_for_tests()
    sparse_retriever.clear_cache()
    yield
    sparse_rebuild.stop_worker(timeout=5.0)
    sparse_rebuild.reset_for_tests()
    sparse_retriever.clear_cache()


def _enable_mmap(monkeypatch, tmp_path: Path, *, corpus_id: str = "rebuild-corpus"):
    from backend.config import settings

    root = tmp_path / "sparse_index"
    monkeypatch.setattr(settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(settings, "SPARSE_INDEX_ROOT", str(root))
    monkeypatch.setattr(settings, "SPARSE_INDEX_CORPUS_ID", corpus_id)
    monkeypatch.setattr(settings, "SPARSE_AUTO_REBUILD", True)
    monkeypatch.setattr(settings, "CHUNK_STRATEGY", "fixed")
    monkeypatch.setattr(settings, "CHUNK_SIZE", 500)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 50)
    monkeypatch.setattr(settings, "CHUNK_MAX_TOKENS", 480)
    monkeypatch.setattr(settings, "BM25_K1", 1.5)
    monkeypatch.setattr(settings, "BM25_B", 0.75)
    return root


def _install_fake_corpus(monkeypatch, state: dict):
    """provider与Chroma观感（revision/count）都指向可变state。"""
    sparse_rebuild.configure(
        provider=lambda: list(state["chunks"]), backoff_s=0.01
    )
    monkeypatch.setattr(
        vector_store, "get_index_revision", lambda: state["revision"]
    )
    monkeypatch.setattr(
        vector_store, "get_chunk_count", lambda: len(state["chunks"])
    )


def _install_hit_fetch(monkeypatch, state: dict):
    """命中后按chunk_id回取正文的替身。"""
    monkeypatch.setattr(
        vector_store,
        "get_chunks_by_ids",
        lambda ids: [row for row in state["chunks"] if row["chunk_id"] in ids],
    )


def _seed_generation(root: Path, chunks, generation_id: str) -> None:
    """用与查询端一致的合同构建种子generation，模拟受控入口的产物。"""
    from backend.config import settings

    contract = sparse_retriever.expected_contract(
        k1=float(settings.BM25_K1), b=float(settings.BM25_B)
    )
    bm25s_store.build_generation(
        chunks=chunks,
        target_root=root,
        corpus_id=contract["corpus_id"],
        tokenize=pure_tokenize,
        tokenizer=contract["tokenizer"],
        chunking=contract["chunking"],
        source={"chroma_collection": "knowledge_base"},
        generation_id=generation_id,
        publish=True,
    )


def test_request_and_worker_gated_off_for_memory_backend(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "SPARSE_BACKEND", "rank_bm25_memory")
    assert sparse_rebuild.request_rebuild("upload:x")["triggered"] is False
    assert sparse_rebuild.start_worker(recovery=False)["started"] is False
    assert sparse_rebuild.get_rebuild_status()["enabled"] is False


def test_worker_publishes_generation_and_binds_revision(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V2, "controlled-seed")
    state = {"chunks": list(CHUNKS_V1), "revision": 5}
    _install_fake_corpus(monkeypatch, state)

    assert sparse_rebuild.start_worker(recovery=False)["started"] is True
    sparse_rebuild.request_rebuild("upload:doc-9")
    assert sparse_rebuild.wait_for_idle(timeout=30)

    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "published"
    pointer = bm25s_store.read_active_payload(tmp_path / "sparse_index")
    assert pointer["status"] == "ready"
    assert sparse_retriever._bound_revision == 5
    # 发布后同revision查询恢复。
    _install_hit_fetch(monkeypatch, state)
    rows = sparse_retriever.search("二叉树", 2)
    assert [row["chunk_id"] for row in rows] == ["c1"]


def test_repeated_requests_merge_into_single_build(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V2, "controlled-seed")
    state = {"chunks": list(CHUNKS_V1), "revision": 1}
    _install_fake_corpus(monkeypatch, state)
    build_calls = []
    real_build = bm25s_store.build_generation

    def counting_build(**kwargs):
        build_calls.append(kwargs.get("generation_id"))
        return real_build(**kwargs)

    monkeypatch.setattr(bm25s_store, "build_generation", counting_build)
    # 先排队三次再启动worker：pending合并为一次构建。
    sparse_rebuild.request_rebuild("upload:a")
    sparse_rebuild.request_rebuild("upload:b")
    sparse_rebuild.request_rebuild("upload:c")
    sparse_rebuild.start_worker(recovery=False)
    assert sparse_rebuild.wait_for_idle(timeout=30)

    assert len(build_calls) == 1
    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["reason"].count("upload:") == 3


def test_source_change_mid_build_discards_candidate_and_requeues(
    monkeypatch, tmp_path
):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, [*CHUNKS_V2, _chunk("c5", "额外哨兵内容")], "controlled-seed")
    state = {"chunks": list(CHUNKS_V1), "revision": 1}
    calls = {"n": 0}

    def changing_provider():
        calls["n"] += 1
        if calls["n"] == 2:
            # 第一次复核取证时源已变化：丢弃候选代并重新排队。
            state.update(
                chunks=list(CHUNKS_V2),
                revision=state["revision"] + 1,
            )
        return list(state["chunks"])

    sparse_rebuild.configure(provider=changing_provider, backoff_s=0.01)
    monkeypatch.setattr(
        vector_store, "get_index_revision", lambda: state["revision"]
    )
    monkeypatch.setattr(
        vector_store, "get_chunk_count", lambda: len(state["chunks"])
    )
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:doc-1")
    assert sparse_rebuild.wait_for_idle(timeout=30)

    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "published"
    root = tmp_path / "sparse_index"
    pointer = bm25s_store.read_active_payload(root)
    assert pointer["status"] == "ready"
    # 候选代（V1语料）已丢弃，最终发布的是V2语料；目录里只有最终一代。
    manifest = bm25s_store._read_manifest(
        root / "generations" / pointer["generation_id"]
    )
    assert manifest["corpus"]["chunk_count"] == 4
    generations = [
        item for item in (root / "generations").iterdir() if item.is_dir()
    ]
    assert set(generations) == {
        root / "generations" / pointer["generation_id"],
        root / "generations" / "controlled-seed",
    }


def test_build_failure_retries_until_success(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V2, "controlled-seed")
    state = {"chunks": list(CHUNKS_V1), "revision": 2}
    calls = {"n": 0}

    def flaky_provider():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("chroma read hiccup")
        return list(state["chunks"])

    sparse_rebuild.configure(provider=flaky_provider, backoff_s=0.01)
    monkeypatch.setattr(
        vector_store, "get_index_revision", lambda: state["revision"]
    )
    monkeypatch.setattr(
        vector_store, "get_chunk_count", lambda: len(state["chunks"])
    )
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:doc-1")
    assert sparse_rebuild.wait_for_idle(timeout=30)

    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "published"
    assert calls["n"] >= 2
    # 成功后退避与尝试计数复位。
    assert status["attempt"] == 0


def test_persistent_failure_keeps_backing_off_and_stays_dirty(
    monkeypatch, tmp_path
):
    _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V1), "revision": 1}

    def always_failing_provider():
        raise RuntimeError("chroma unavailable")

    sparse_rebuild.configure(provider=always_failing_provider, backoff_s=0.01)
    monkeypatch.setattr(
        vector_store, "get_index_revision", lambda: state["revision"]
    )
    monkeypatch.setattr(
        vector_store, "get_chunk_count", lambda: len(state["chunks"])
    )
    _seed_generation(tmp_path / "sparse_index", CHUNKS_V1, "seed-gen")
    sparse_lifecycle.mark_corpus_changing("upload:doc-9", root=tmp_path / "sparse_index")

    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:doc-9")
    # 持续失败：等待两个退避周期后仍为failed，指针保持dirty。
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if sparse_rebuild.get_rebuild_status()["attempt"] >= 2:
            break
        time.sleep(0.05)
    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "failed"
    assert status["attempt"] >= 2
    assert sparse_lifecycle.read_active_status(tmp_path / "sparse_index") == "dirty"
    sparse_rebuild.stop_worker(timeout=5.0)


def test_startup_recovery_rebuilds_dirty_pointer(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V1), "revision": 0}
    _install_fake_corpus(monkeypatch, state)
    _seed_generation(root, CHUNKS_V1, "seed-gen")
    sparse_lifecycle.mark_corpus_changing("chroma_rebuild", root=root)

    sparse_rebuild.start_worker(recovery=True)
    assert sparse_rebuild.wait_for_idle(timeout=30)
    pointer = bm25s_store.read_active_payload(root)
    assert pointer["status"] == "ready"
    assert pointer["generation_id"] == "seed-gen"  # 语料未变：复用现役代
    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "already_fresh"


def test_startup_recovery_rebuilds_chunk_count_mismatch(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V2), "revision": 4}
    _install_fake_corpus(monkeypatch, state)
    _seed_generation(root, CHUNKS_V1, "stale-gen")

    sparse_rebuild.start_worker(recovery=True)
    assert sparse_rebuild.wait_for_idle(timeout=30)
    pointer = bm25s_store.read_active_payload(root)
    assert pointer["generation_id"] != "stale-gen"
    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "published"


def test_startup_recovery_does_not_bootstrap_missing_pointer(
    monkeypatch, tmp_path
):
    _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V1), "revision": 0}
    _install_fake_corpus(monkeypatch, state)

    sparse_rebuild.start_worker(recovery=True)
    assert sparse_rebuild.wait_for_idle(timeout=5)
    status = sparse_rebuild.get_rebuild_status()
    assert status["last_result"]["outcome"] == "idle"
    assert not (tmp_path / "sparse_index" / "active.json").exists()


def test_concurrent_queries_survive_generation_switch(monkeypatch, tmp_path):
    """发布切换期间的并发只读查询：无异常、无关闭中的mmap竞态。"""
    _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V1), "revision": 0}
    _install_fake_corpus(monkeypatch, state)
    _install_hit_fetch(monkeypatch, state)
    _seed_generation(tmp_path / "sparse_index", CHUNKS_V1, "seed-gen")

    errors = []
    dirty_windows = {"count": 0}
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                rows = sparse_retriever.search("二叉树", 2)
                assert [row["chunk_id"] for row in rows] == ["c1"]
            except SparseIndexDirtyError:
                # 重建窗口内的fail-closed拒绝是设计行为；只统计不断言。
                dirty_windows["count"] += 1
            except Exception as error:  # noqa: BLE001
                errors.append(f"{type(error).__name__}: {error}")

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for thread in readers:
        thread.start()

    # 三轮真实语料交替变化：每轮构建并发布新generation，读者全程只读。
    sparse_rebuild.start_worker(recovery=False)
    for round_index in range(3):
        with sparse_lifecycle.document_transaction(f"cycle:{round_index}"):
            sparse_lifecycle.mark_corpus_changing("test_write")
            if round_index % 2 == 0:
                state.update(chunks=list(CHUNKS_V2))
            else:
                state.update(chunks=list(CHUNKS_V1))
            state["revision"] += 1
        sparse_rebuild.request_rebuild(f"cycle:{round_index}")
        assert sparse_rebuild.wait_for_idle(timeout=30)
    stop.set()
    for thread in readers:
        thread.join(timeout=10)

    assert errors == []
    assert dirty_windows["count"] > 0  # 重建窗口确实经历了fail-closed拒绝
    pointer = bm25s_store.read_active_payload(tmp_path / "sparse_index")
    assert pointer["status"] == "ready"


def test_old_generation_handles_released_after_switch(monkeypatch, tmp_path):
    """Windows句柄释放：切换后旧generation文件不再被mmap占用。"""
    root = _enable_mmap(monkeypatch, tmp_path)
    state = {"chunks": list(CHUNKS_V1), "revision": 0}
    _install_fake_corpus(monkeypatch, state)
    _install_hit_fetch(monkeypatch, state)
    _seed_generation(root, CHUNKS_V1, "old-gen")
    sparse_retriever.search("二叉树", 2)

    # 语料真实变化：重建发布新generation；旧代在缓存换载后释放句柄。
    state.update(chunks=list(CHUNKS_V2), revision=2)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:doc-3")
    assert sparse_rebuild.wait_for_idle(timeout=30)
    sparse_retriever.search("二叉树", 2)  # 触发缓存换载

    old_dir = root / "generations" / "old-gen"
    renamed = root / "generations" / "old-gen-renamed"
    try:
        old_dir.rename(renamed)  # Windows上mmap未释放会抛PermissionError
        renamed.rename(old_dir)
    except PermissionError as error:
        raise AssertionError("旧generation的mmap句柄未释放") from error


def test_stop_worker_stops_cleanly(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V2, "controlled-seed")
    state = {"chunks": list(CHUNKS_V1), "revision": 1}
    _install_fake_corpus(monkeypatch, state)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:x")
    assert sparse_rebuild.wait_for_idle(timeout=30)

    result = sparse_rebuild.stop_worker(timeout=10.0)
    assert result["stopped"] is True
    assert sparse_rebuild.get_rebuild_status()["thread_alive"] is False


def test_upload_event_cannot_bootstrap_missing_pointer(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _install_fake_corpus(monkeypatch, {"chunks": CHUNKS_V1, "revision": 1})
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:first")
    assert sparse_rebuild.wait_for_idle(10)
    assert sparse_rebuild.get_rebuild_status()["last_result"]["outcome"] == "needs_initial_generation"
    assert not root.exists()


def test_stop_during_build_never_publishes(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V1, "old")
    _install_fake_corpus(monkeypatch, {"chunks": CHUNKS_V2, "revision": 1})
    built, release = threading.Event(), threading.Event()
    real_build = bm25s_store.build_generation

    def blocked_build(**kwargs):
        result = real_build(**kwargs)
        built.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(bm25s_store, "build_generation", blocked_build)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:x")
    try:
        assert built.wait(10)
        assert sparse_rebuild.stop_worker(timeout=0)["stopped"] is False
    finally:
        release.set()
    assert sparse_rebuild.stop_worker(timeout=10)["stopped"]
    pointer = bm25s_store.read_active_payload(root)
    assert pointer["generation_id"] == "old"
    assert pointer["status"] == "dirty"


def test_restart_checks_same_count_metadata_changes(monkeypatch, tmp_path):
    from copy import deepcopy

    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V1, "old")
    changed = deepcopy(CHUNKS_V1)
    changed[0]["metadata"]["document_name"] = "renamed.txt"
    _install_fake_corpus(monkeypatch, {"chunks": changed, "revision": 0})
    sparse_rebuild.start_worker(recovery=True)
    assert sparse_rebuild.wait_for_idle(15)
    pointer = bm25s_store.read_active_payload(root)
    assert pointer["generation_id"] != "old"
    assert pointer["status"] == "ready"


def test_empty_corpus_stays_dirty_without_infinite_retry(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V1, "old")
    state = {"chunks": [], "revision": 1}
    _install_fake_corpus(monkeypatch, state)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("delete:last")
    assert sparse_rebuild.wait_for_idle(10)
    assert sparse_rebuild.get_rebuild_status()["last_result"]["outcome"] == "empty"
    assert sparse_lifecycle.read_active_status(root) == "dirty"
    state.update(chunks=CHUNKS_V2, revision=2)
    sparse_rebuild.request_rebuild("upload:next")
    assert sparse_rebuild.wait_for_idle(15)
    assert sparse_lifecycle.read_active_status(root) == "ready"


def test_runtime_parameters_changed_during_build_discard_candidate(monkeypatch, tmp_path):
    from types import SimpleNamespace

    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V1, "old")
    _install_fake_corpus(monkeypatch, {"chunks": CHUNKS_V2, "revision": 1})
    runtime = SimpleNamespace(retrieval=SimpleNamespace(bm25_k1=1.2, bm25_b=0.6))
    sparse_rebuild.configure(runtime_provider=lambda: runtime)
    real_build = bm25s_store.build_generation
    builds = []

    def change_after_build(**kwargs):
        result = real_build(**kwargs)
        builds.append(kwargs["k1"])
        if len(builds) == 1:
            with sparse_lifecycle.corpus_lock:
                runtime.retrieval.bm25_k1 = 1.9
        return result

    monkeypatch.setattr(bm25s_store, "build_generation", change_after_build)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("settings")
    assert sparse_rebuild.wait_for_idle(20)
    assert builds == [1.2, 1.9]
    loaded = bm25s_store.load_active(root, expected_contract=sparse_retriever.expected_contract(k1=1.9, b=0.6))
    assert loaded.manifest["compatibility"]["oracle"]["question_count"] == 120
    loaded.close()
    sparse_retriever.clear_cache()
    _install_hit_fetch(monkeypatch, {"chunks": CHUNKS_V2})
    assert sparse_retriever.search("二叉树", 2, k1=1.9, b=0.6)


def test_final_source_check_and_publication_exclude_writers(monkeypatch, tmp_path):
    root = _enable_mmap(monkeypatch, tmp_path)
    _seed_generation(root, CHUNKS_V1, "old")
    state = {"chunks": CHUNKS_V2, "revision": 1}
    _install_fake_corpus(monkeypatch, state)
    entered, release, wrote = threading.Event(), threading.Event(), threading.Event()
    real_activate = bm25s_store.activate_generation

    def paused_activate(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_activate(*args, **kwargs)

    monkeypatch.setattr(bm25s_store, "activate_generation", paused_activate)
    sparse_rebuild.start_worker(recovery=False)
    sparse_rebuild.request_rebuild("upload:x")
    assert entered.wait(10)

    def writer():
        with sparse_lifecycle.corpus_lock:
            sparse_lifecycle.mark_corpus_changing("new_write")
            state["revision"] += 1
            wrote.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert not wrote.wait(0.1)
    finally:
        release.set()
        thread.join(10)
    assert wrote.is_set()
    assert sparse_rebuild.wait_for_idle(10)
    # 写入只能在发布之后标脏，不能被旧候选的ready覆盖。
    assert sparse_lifecycle.read_active_status(root) == "dirty"
