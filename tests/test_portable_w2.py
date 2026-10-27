"""Portable W2 guards and real BM25 lifecycle; model assets here are tiny fakes.

Full embedded-runtime/model/API acceptance is a separate offline probe.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from backend import portable


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    # Pure unit tests must not mutate the process environment used by other tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    root = tmp_path / "中文 空格 bundle"
    root.mkdir()
    (root / "locks").mkdir()
    models = {}
    for key, (model_id, revision) in portable.MODELS.items():
        directory = root / "models" / key
        directory.mkdir(parents=True)
        (directory / "config.json").write_bytes(b"{}")
        models[key] = {"id": model_id, "revision": revision, "files": [
            {"path": "config.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}]}
    (root / "locks/w1-lock.json").write_text(json.dumps({"models": models}), encoding="utf-8")
    return root


def test_new_portable_workspace_defaults_and_identity_survive_copy(bundle, monkeypatch):
    monkeypatch.setitem(os.environ, "DEEPSEEK_API_KEY", "do-not-inherit")
    monkeypatch.setitem(os.environ, "HF_HOME", "C:/do-not-use")
    values = portable.prepare_environment(bundle, "cpu")
    first = portable.read_workspace(bundle)
    assert values["LLM_ENABLED"] == values["OCR_ENABLED"] == "false"
    assert values["RERANKER_ENABLED"] == "true"
    assert values["RERANKER_USE_FP16"] == "false"
    assert values["RETRIEVAL_MODE"] == "hybrid"
    assert values["SPARSE_BACKEND"] == "bm25s_mmap"
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert Path(os.environ["HF_HOME"]).is_relative_to(bundle)
    assert Path(tempfile.gettempdir()) == bundle / "temp"
    portable.prepare_environment(bundle, "cuda:0")
    assert portable.read_workspace(bundle)["workspace_id"] == first["workspace_id"]
    copied = bundle.with_name("另一份 目录")
    shutil.copytree(bundle, copied)
    relocated = portable.prepare_environment(copied, "cpu")
    assert portable.read_workspace(copied)["workspace_id"] == first["workspace_id"]
    assert Path(relocated["METADATA_DB_PATH"]).is_relative_to(copied)


def test_reduced_batch_profile_keeps_models_and_candidate_contract(bundle):
    values = portable.prepare_environment(bundle, "cuda:0", "reduced")
    assert values["EMBEDDING_BATCH_SIZE"] == "16"
    assert values["RERANKER_BATCH_SIZE"] == "4"
    assert values["DENSE_CANDIDATE_K"] == values["RERANKER_CANDIDATE_K"] == "15"
    assert values["RETRIEVAL_TOP_K"] == "5" and values["RERANKER_ENABLED"] == "true"
    assert values["EMBEDDING_MODEL"] == portable.MODELS["embedding"][0]


def test_v5_conversation_upgrade_backs_up_and_preserves_workspace(bundle):
    from backend.services import metadata_schema as schema
    portable.prepare_environment(bundle, "cpu")
    marker = bundle / "data/workspace.json"
    old = json.loads(marker.read_text("utf-8"))
    old["contract"]["storage_schema"] = 5
    marker.write_text(json.dumps(old), encoding="utf-8")
    db = bundle / "data/metadata.db"
    with sqlite3.connect(db) as connection:
        for ddl in schema._SCHEMAS:
            if ddl != schema.CHAT_REPLACEMENTS_SCHEMA:
                connection.execute(ddl.replace("    retrieval_tags_json TEXT,\n", ""))
        connection.execute("INSERT INTO schema_meta VALUES (5, 'old')")
        connection.execute("INSERT INTO conversations VALUES ('old','旧会话','old','old')")
    portable.prepare_environment(bundle, "cpu")
    portable.prepare_environment(bundle, "cpu")
    current = portable.read_workspace(bundle)
    assert current["workspace_id"] == old["workspace_id"]
    assert current["contract"]["storage_schema"] == 6
    backups = list((bundle / "data/migration-backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0] == 5
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT title FROM conversations").fetchone()[0] == "旧会话"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_config_import_ignores_dotenv_and_inherited_deployment_values(bundle):
    backend = bundle / "backend"
    backend.mkdir()
    (backend / "__init__.py").write_text("", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "backend"
    for name in ("config.py", "portable.py"):
        shutil.copyfile(source / name, backend / name)
    (bundle / ".env").write_text("LLM_ENABLED=true\nDEEPSEEK_API_KEY=bad-dotenv\n", encoding="utf-8")
    environment = dict(os.environ, RAG_DISTRIBUTION_MODE="portable", RAG_PORTABLE_DEVICE="cpu",
                       PYTHONPATH=str(bundle), DEEPSEEK_API_KEY="bad-inherited", LLM_ENABLED="true",
                       RETRIEVAL_MODE="dense", RERANKER_ENABLED="false", CHUNK_SIZE="not-an-integer",
                       UPLOAD_DIR="C:/do-not-create", PYTHONUTF8="1")
    code = """
from backend.config import settings as s
assert s.DISTRIBUTION_MODE == 'portable'
assert not s.LLM_ENABLED and not s.OCR_ENABLED and s.DEEPSEEK_API_KEY == ''
assert s.RERANKER_ENABLED and s.RERANKER_DEVICE == 'cpu' and not s.RERANKER_USE_FP16
assert s.CHUNK_SIZE == 400 and s.CHUNK_OVERLAP == 80 and s.CHUNK_STRATEGY == 'recursive'
assert s.RETRIEVAL_MODE == 'hybrid' and s.SPARSE_BACKEND == 'bm25s_mmap'
assert s.DENSE_CANDIDATE_K == s.RERANKER_CANDIDATE_K == 15 and s.RETRIEVAL_TOP_K == 5
assert s.METADATA_DB_PATH == str(s.BASE_DIR / 'data/metadata.db')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=bundle, env=environment,
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("device", ["auto", "cuda:1", "mps", "garbage"])
def test_device_choice_is_explicit_and_rejects_unsupported(bundle, device):
    with pytest.raises(ValueError, match="RAG_PORTABLE_DEVICE"):
        portable.prepare_environment(bundle, device)
    assert not (bundle / "data").exists()


def test_does_not_adopt_unmarked_existing_data(bundle):
    (bundle / "data").mkdir()
    database = bundle / "data/metadata.db"
    database.write_bytes(b"existing personal data sentinel")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="拒绝接管"):
        portable.prepare_environment(bundle, "cpu")
    assert database.read_bytes() == before
    assert not (bundle / "data/workspace.json").exists()


@pytest.mark.parametrize("version", [4, 7])
def test_incompatible_database_refused_before_migration_or_schema_write(bundle, version):
    portable.prepare_environment(bundle, "cpu")
    database = bundle / "data/metadata.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE schema_meta (schema_version INTEGER)")
        connection.execute("INSERT INTO schema_meta VALUES (?)", (version,))
    before = database.read_bytes()
    with pytest.raises(ValueError, match="schema不兼容"):
        portable.prepare_environment(bundle, "cpu")
    assert database.read_bytes() == before


def test_bundle_contract_drift_refused(bundle):
    portable.prepare_environment(bundle, "cpu")
    marker = bundle / "data/workspace.json"
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["contract"]["models"]["embedding"]["revision"] = "0" * 40
    marker.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="不兼容"):
        portable.prepare_environment(bundle, "cpu")


def test_runtime_lock_change_refused_before_existing_data_opens(bundle):
    portable.prepare_environment(bundle, "cpu")
    path = bundle / "locks/w1-lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["changed_runtime"] = "unreviewed version"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="不兼容"):
        portable.prepare_environment(bundle, "cpu")


def test_missing_model_fails_without_falling_back_to_global_cache(bundle, monkeypatch):
    from backend.config import settings
    from backend.services.model_cache import resolve_cached_snapshot

    monkeypatch.setattr(settings, "DISTRIBUTION_MODE", "portable")
    monkeypatch.setattr(settings, "BASE_DIR", bundle)
    assert resolve_cached_snapshot(portable.MODELS["embedding"][0]) == str((bundle / "models/embedding").resolve())
    (bundle / "models/embedding/config.json").unlink()
    with pytest.raises(ValueError, match="缺失"):
        resolve_cached_snapshot(portable.MODELS["embedding"][0])
    with pytest.raises(ValueError, match="固定"):
        resolve_cached_snapshot("BAAI/unapproved")


def test_escaping_model_manifest_rejected(bundle):
    path = bundle / "locks/w1-lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["models"]["embedding"]["files"][0]["path"] = "../../outside"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="清单路径"):
        portable.prepare_environment(bundle, "cpu")
    assert not (bundle / "data").exists()


def test_cache_link_cannot_escape_bundle(bundle, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (bundle / "cache").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Current Windows account cannot create symlinks")
    with pytest.raises(ValueError, match="越界"):
        portable.prepare_environment(bundle, "cpu")
    assert not (bundle / "data").exists()
    assert list(outside.iterdir()) == []


def test_storage_schema_contract_matches_application():
    from backend.services.metadata_schema import SCHEMA_VERSION

    assert portable.STORAGE_SCHEMA == SCHEMA_VERSION


def test_w2_builder_refuses_non_build_output(tmp_path, monkeypatch):
    from deploy.windows import prepare_w1, prepare_w2

    monkeypatch.setattr(prepare_w1, "REPO", tmp_path / "project")
    monkeypatch.setattr(prepare_w2, "REPO", tmp_path / "project")
    with pytest.raises(ValueError, match="NEW child"):
        prepare_w2.assemble(tmp_path / "unused", tmp_path / "outside")
    assert not (tmp_path / "outside").exists()


def test_w2_refresh_cannot_touch_production_directory():
    from deploy.windows import prepare_w2

    with pytest.raises(ValueError, match="W2 child"):
        prepare_w2.refresh_application(prepare_w2.REPO / "data")


def test_w2_refresh_rejects_unrecognized_stage(tmp_path, monkeypatch):
    from deploy.windows import prepare_w2

    monkeypatch.setattr(prepare_w2, "REPO", tmp_path)
    target = tmp_path / "build" / "bundle"
    target.mkdir(parents=True)
    (target / "w1-manifest.json").write_text(json.dumps({"stage": "not W2"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Not a W2"):
        prepare_w2.refresh_application(target)


def test_w2_refresh_checks_tampering_before_any_source_write(tmp_path, monkeypatch):
    from deploy.windows import prepare_w2

    monkeypatch.setattr(prepare_w2, "REPO", tmp_path)
    target = tmp_path / "build" / "bundle"
    target.mkdir(parents=True)
    (target / "resource").write_bytes(b"modified")
    manifest = {"stage": "W2 application validation, NOT release", "bytes": 8, "files": [
        {"path": "resource", "size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}]}
    (target / "w1-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare_w2.refresh_application(target)
    assert not (target / "backend").exists()


@pytest.fixture
def corpus(bundle, monkeypatch):
    from backend.config import settings
    from backend.services import sparse_rebuild, sparse_retriever, vector_store

    portable.prepare_environment(bundle, "cpu")
    workspace = portable.read_workspace(bundle)
    for key, value in {"BASE_DIR": bundle, "DISTRIBUTION_MODE": "portable",
                       "SPARSE_BACKEND": "bm25s_mmap", "SPARSE_AUTO_REBUILD": True,
                       "SPARSE_INDEX_ROOT": str(bundle / "data/sparse_index"),
                       "SPARSE_INDEX_CORPUS_ID": workspace["corpus_id"]}.items():
        monkeypatch.setattr(settings, key, value)
    sparse_rebuild.reset_for_tests()
    sparse_retriever.clear_cache()
    state = {"chunks": [
        {"chunk_id": f"c{i}", "content": text, "metadata": {"document_id": f"d{i}", "document_name": f"{i}.txt"}}
        for i, text in enumerate(("知识库备份复制完整目录", "苹果树浇水修剪", "配置模型密钥与上传文档"))
    ], "revision": 1}
    sparse_rebuild.configure(provider=lambda: list(state["chunks"]), backoff_s=0.01)
    monkeypatch.setattr(vector_store, "get_index_revision", lambda: state["revision"])
    monkeypatch.setattr(vector_store, "get_chunk_count", lambda: len(state["chunks"]))
    monkeypatch.setattr(sparse_rebuild, "_oracle_questions", lambda: pytest.fail("Portable rebuild read private eval"))
    yield SimpleNamespace(root=bundle, state=state, settings=settings, rebuild=sparse_rebuild)
    sparse_rebuild.stop_worker(timeout=5)
    sparse_retriever.clear_cache()
    sparse_rebuild.reset_for_tests()


def test_snapshot_oracle_is_bounded_deterministic_and_has_a_nonmatch(corpus):
    from backend.services.sparse_tokenizer import tokenize

    rows = corpus.state["chunks"]
    questions, info = portable.snapshot_oracle(rows)
    assert (questions, info) == portable.snapshot_oracle(list(reversed(rows)))
    assert 1 < info["count"] <= 65
    vocabulary = {word for row in rows for word in tokenize(row["content"])}
    assert not vocabulary.intersection(tokenize(questions[-1]["question"]))


def test_empty_portable_startup_waits_for_upload_without_fake_generation(corpus):
    corpus.state["chunks"] = []
    corpus.rebuild.start_worker(recovery=True)
    assert corpus.rebuild.wait_for_idle(20)
    assert corpus.rebuild.get_rebuild_status()["last_result"]["outcome"] == "awaiting_upload"
    assert not (corpus.root / "data/sparse_index/active.json").exists()
    assert not portable.read_workspace(corpus.root)["sparse_initialized"]


def test_first_publication_has_real_oracle_and_mmap_then_lost_pointer_cannot_bootstrap(corpus):
    from backend.services import bm25s_store, sparse_retriever

    result = corpus.rebuild._run_cycle()
    assert result["outcome"] == "published"
    marker = portable.read_workspace(corpus.root)
    assert marker["sparse_initialized"] and not marker["sparse_publication_pending"]
    loaded = bm25s_store.load_active(corpus.settings.SPARSE_INDEX_ROOT, expected_contract=corpus.rebuild._contract())
    assert loaded.manifest["compatibility"]["oracle"]["question_count"] > 1
    assert loaded.manifest["contract"]["source"]["oracle"]["policy"] == "portable-snapshot-v1"
    loaded.close()
    sparse_retriever.clear_cache()
    (corpus.root / "data/sparse_index/active.json").unlink()
    assert corpus.rebuild._run_cycle()["outcome"] == "needs_initial_generation"


def test_initial_source_change_discards_candidate_without_marking_published(corpus, monkeypatch):
    from backend.services import bm25s_store

    real_build = bm25s_store.build_generation
    def change_after_build(**kwargs):
        result = real_build(**kwargs)
        corpus.state["revision"] += 1
        return result
    monkeypatch.setattr(bm25s_store, "build_generation", change_after_build)
    assert corpus.rebuild._run_cycle()["outcome"] == "superseded"
    assert not portable.read_workspace(corpus.root)["sparse_initialized"]
    assert not (corpus.root / "data/sparse_index/active.json").exists()


def test_interrupted_initial_commit_replays_exact_candidate_then_closes_gate(corpus, monkeypatch):
    from backend.services import bm25s_store

    real_activate = bm25s_store.activate_generation
    def fail_activate(*args, **kwargs):
        raise OSError("simulated publication interruption")
    monkeypatch.setattr(bm25s_store, "activate_generation", fail_activate)
    with pytest.raises(OSError, match="interruption"):
        corpus.rebuild._run_cycle()
    pending = portable.pending_publication(corpus.settings)
    assert pending and not portable.may_bootstrap(corpus.settings)
    monkeypatch.setattr(bm25s_store, "activate_generation", real_activate)
    result = corpus.rebuild._run_cycle()
    assert result["reason"] == "resumed_initial_publication"
    assert result["generation_id"] == pending
    assert portable.pending_publication(corpus.settings) is None


def test_interrupted_commit_does_not_restore_stale_source(corpus, monkeypatch):
    from backend.services import bm25s_store

    def fail_activate(*args, **kwargs):
        raise OSError("interrupted")
    monkeypatch.setattr(bm25s_store, "activate_generation", fail_activate)
    with pytest.raises(OSError):
        corpus.rebuild._run_cycle()
    corpus.state["chunks"][0]["content"] = "已变化的正文"
    with pytest.raises(RuntimeError, match="语料已变化"):
        corpus.rebuild._run_cycle()
    assert not (corpus.root / "data/sparse_index/active.json").exists()


def test_initialized_portable_library_rebuilds_after_new_content(corpus):
    from backend.services import bm25s_store

    assert corpus.rebuild._run_cycle()["outcome"] == "published"
    corpus.state["chunks"].append({"chunk_id": "new", "content": "操作系统进程调度",
                                   "metadata": {"document_id": "new", "document_name": "new.txt"}})
    corpus.state["revision"] += 1
    assert corpus.rebuild._run_cycle()["outcome"] == "published"
    loaded = bm25s_store.load_active(corpus.settings.SPARSE_INDEX_ROOT, expected_contract=corpus.rebuild._contract())
    assert loaded.manifest["corpus"]["chunk_count"] == 4
    loaded.close()
