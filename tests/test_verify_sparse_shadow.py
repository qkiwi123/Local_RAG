"""P6-B备份工具的不可覆盖、摘要与源变化保护。"""
import json
from pathlib import Path

import pytest
from scripts import verify_sparse_shadow as shadow


def test_fingerprint_detects_content_change_with_same_size(tmp_path):
    source = tmp_path / "file"
    source.write_bytes(b"abc")
    before = shadow.fingerprint(source)
    source.write_bytes(b"xyz")
    assert shadow.fingerprint(source) != before


def test_copy_preserves_full_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "metadata.db").write_bytes(b"database")
    (source / "metadata.db-wal").write_bytes(b"wal")
    target = tmp_path / "backup"
    shadow.copy(source,target)
    assert shadow.fingerprint(source) == shadow.fingerprint(target)
    with pytest.raises(FileExistsError):
        shadow.copy(source,target)


def test_reports_are_exclusive(tmp_path):
    path = tmp_path / "report.json"
    shadow.write(path,{"status":"passed"})
    with pytest.raises(FileExistsError):
        shadow.write(path,{"status":"changed"})
    assert json.loads(path.read_text())["status"] == "passed"


def test_source_check_detects_modified_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(shadow,"stopped",lambda: {})
    env = tmp_path / "environment"
    env.write_bytes(b"SPARSE_BACKEND=rank_bm25_memory")
    plan = {"source":{"environment":str(env)},"fingerprints":{"environment":shadow.fingerprint(env)}}
    assert shadow.source_unchanged(plan)
    env.write_bytes(b"SPARSE_BACKEND=bm25s_mmap")
    with pytest.raises(RuntimeError,match="生产源资产变化"):
        shadow.source_unchanged(plan)


def test_source_check_allows_shadow_but_rejects_active_publish(tmp_path,monkeypatch):
    monkeypatch.setattr(shadow,"stopped",lambda: {})
    sparse = tmp_path / "sparse"
    plan = {"source":{"sparse":str(sparse)},"backup":{"sparse":str(tmp_path / "backup")},
            "fingerprints":{"sparse":{"exists":False}}}
    (sparse / "generations" / "shadow").mkdir(parents=True)
    assert shadow.source_unchanged(plan)
    (sparse / "active.json").write_text("{}")
    with pytest.raises(RuntimeError,match="active"):
        shadow.source_unchanged(plan)


def test_service_refuses_paths_outside_restore(tmp_path):
    shadow.write(tmp_path / "restore-plan.json",{"paths":{"chroma":str(tmp_path / "production")}})
    with pytest.raises(ValueError,match="restore"):
        shadow.service_probe(tmp_path,"boot1","bm25s_mmap")
