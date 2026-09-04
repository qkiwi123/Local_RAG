"""P2 Sparse构建CLI的路径与显式参数安全测试。"""

from pathlib import Path
import subprocess
import sys

import pytest

from scripts import rebuild_sparse_index


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    chroma = tmp_path / "source" / "chroma"
    chroma.mkdir(parents=True)
    metadata = tmp_path / "source" / "metadata.db"
    metadata.touch()
    return chroma, metadata


def test_safe_paths_accept_separate_explicit_test_paths(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    target = tmp_path / "output" / "sparse"
    monkeypatch.setattr(rebuild_sparse_index, "_configured_production_paths", lambda: set())

    assert rebuild_sparse_index.ensure_safe_paths(
        source_chroma=chroma,
        source_metadata=metadata,
        target_root=target,
    ) == (chroma.resolve(), metadata.resolve(), target.resolve())


def test_safe_paths_reject_production_sources(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: {chroma.resolve(), metadata.resolve()},
    )
    with pytest.raises(ValueError, match="生产数据"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=tmp_path / "target",
        )


def test_safe_paths_reject_source_target_overlap(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    monkeypatch.setattr(rebuild_sparse_index, "_configured_production_paths", lambda: set())
    with pytest.raises(ValueError, match="互相包含"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=chroma / "sparse",
        )


def test_cli_requires_all_source_and_contract_arguments():
    parser = rebuild_sparse_index._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_cli_file_entrypoint_can_import_project_modules():
    result = subprocess.run(
        [sys.executable, str(Path(rebuild_sparse_index.__file__).resolve()), "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--source-chroma" in result.stdout
