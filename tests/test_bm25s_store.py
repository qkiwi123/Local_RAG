"""P2持久化BM25 generation、mmap、合同和原子指针测试。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from rank_bm25 import BM25Okapi

from backend.services import bm25s_store, sparse_retriever
from backend.services.sparse_index import (
    SparseIndexBuildError,
    SparseIndexCorruptError,
    SparseIndexDirtyError,
    SparseIndexMismatchError,
    SparseIndexMissingError,
)


def _chunk(chunk_id: str, content: str, document_id: str | None = None) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {"document_id": document_id or f"doc-{chunk_id}", "page_number": 1},
    }


def _corpus() -> list[dict]:
    return [
        _chunk("c3", "数据库 索引 事务 隔离", "doc-3"),
        _chunk("c1", "二叉树 前序 遍历 算法", "doc-1"),
        _chunk("c5", "向量 检索 稠密 模型", "doc-5"),
        _chunk("c2", "进程 调度 操作系统 算法", "doc-2"),
        _chunk("c4", "中文 检索 BM25 BM25", "doc-4"),
    ]


def _tokenizer_contract() -> dict:
    return {
        "contract": bm25s_store.TOKENIZER_CONTRACT,
        "jieba_version": "0.42.1",
        "user_dictionary_sha256": None,
        "stopwords_sha256": None,
    }


def _chunking_contract() -> dict:
    return {
        "parser_version": "structured-v2",
        "strategy": "fixed",
        "size": 500,
        "overlap": 50,
        "max_tokens": 480,
    }


def _source_contract() -> dict:
    return {
        "chroma_collection": "knowledge_base",
        "chroma_chunk_count": 5,
        "metadata_ready_documents_sha256": "a" * 64,
    }


def _build(root: Path, generation_id: str = "test-generation", **kwargs):
    return bm25s_store.build_generation(
        chunks=_corpus(),
        target_root=root,
        corpus_id="unit-corpus",
        tokenize=sparse_retriever.tokenize,
        tokenizer=_tokenizer_contract(),
        chunking=_chunking_contract(),
        source=_source_contract(),
        generation_id=generation_id,
        oracle_questions=[
            {"id": "q1", "question": "二叉树算法"},
            {"id": "q2", "question": "BM25 BM25 中文"},
        ],
        **kwargs,
    )


def _rank_bm25_results(chunks: list[dict], query: str, top_k: int) -> list[tuple[str, float]]:
    rows = sorted(chunks, key=lambda row: row["chunk_id"])
    tokens = [sparse_retriever.tokenize(row["content"]) for row in rows]
    model = BM25Okapi(tokens, k1=1.5, b=0.75, epsilon=0.25)
    scores = model.get_scores(sparse_retriever.tokenize(query))
    return sorted(
        ((row["chunk_id"], float(score)) for row, score in zip(rows, scores) if score > 0),
        key=lambda item: (-item[1], item[0]),
    )[:top_k]


def test_build_load_mmap_and_rank_bm25_equivalence(tmp_path):
    result = _build(tmp_path)
    generation = Path(result["generation_dir"])

    assert result["chunk_count"] == 5
    assert result["document_count"] == 5
    assert result["published"] is False
    assert not (generation / "corpus.jsonl").exists()
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["body_storage"].startswith("chunk_id_only")
    assert manifest["contract"]["bm25"]["idf_method"] == bm25s_store.IDF_METHOD
    assert manifest["corpus"]["ordered_chunk_ids_sha256"]
    assert manifest["compatibility"]["oracle"]["passed"] is True
    assert manifest["compatibility"]["oracle"]["exact_order_questions"] == {
        "1": 2, "5": 2, "15": 2, "50": 2
    }
    assert manifest["compatibility"]["oracle"]["max_absolute_score_delta"] == 0

    with bm25s_store.load_generation(generation) as loaded:
        assert loaded.is_mmap
        assert all(
            isinstance(value, np.memmap)
            for key, value in loaded.model.scores.items()
            if key != "num_docs"
        )
        for query in ("二叉树算法", "BM25 BM25 中文", "不存在的词"):
            expected = _rank_bm25_results(_corpus(), query, 5)
            actual = [(hit.chunk_id, hit.score) for hit in loaded.search_tokens(
                sparse_retriever.tokenize(query), 5
            )]
            assert [row[0] for row in actual] == [row[0] for row in expected]
            assert [row[1] for row in actual] == pytest.approx([row[1] for row in expected], abs=0)


def test_publish_load_active_dirty_and_explicit_switch(tmp_path):
    first = _build(tmp_path, "generation-one", publish=True)
    assert first["published"] is True
    with bm25s_store.load_active(tmp_path) as loaded:
        assert loaded.generation_id == "generation-one"

    dirty = bm25s_store.mark_active_dirty(tmp_path, "corpus changed")
    assert dirty["status"] == "dirty"
    with pytest.raises(SparseIndexDirtyError, match="corpus changed"):
        bm25s_store.load_active(tmp_path)

    _build(tmp_path, "generation-two")
    with pytest.raises(FileExistsError, match="active.json"):
        bm25s_store.activate_generation(tmp_path, "generation-two")
    bm25s_store.activate_generation(tmp_path, "generation-two", allow_replace=True)
    with bm25s_store.load_active(tmp_path) as loaded:
        assert loaded.generation_id == "generation-two"


def test_existing_generation_and_active_are_not_overwritten_by_default(tmp_path):
    _build(tmp_path, "same", publish=True)
    manifest_before = (tmp_path / "generations" / "same" / "manifest.json").read_bytes()
    active_before = (tmp_path / "active.json").read_bytes()

    with pytest.raises(FileExistsError, match="generation已存在"):
        _build(tmp_path, "same")
    with pytest.raises(FileExistsError, match="已有active.json"):
        _build(tmp_path, "other", publish=True)

    assert (tmp_path / "generations" / "same" / "manifest.json").read_bytes() == manifest_before
    assert (tmp_path / "active.json").read_bytes() == active_before


@pytest.mark.parametrize("filename", ["data.csc.index.npy", "vocab.index.json", "chunk_ids.jsonl"])
def test_missing_required_file_is_rejected(tmp_path, filename):
    result = _build(tmp_path)
    generation = Path(result["generation_dir"])
    (generation / filename).unlink()

    with pytest.raises(SparseIndexMissingError, match="缺少索引文件"):
        bm25s_store.load_generation(generation)


def test_corrupt_file_and_manifest_contract_hash_are_rejected(tmp_path):
    result = _build(tmp_path)
    generation = Path(result["generation_dir"])
    with (generation / "data.csc.index.npy").open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(SparseIndexCorruptError, match="hash或大小"):
        bm25s_store.load_generation(generation)

    second = _build(tmp_path, "second")
    manifest_path = Path(second["generation_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract"]["bm25"]["epsilon"] = 0.5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SparseIndexCorruptError, match="contract hash"):
        bm25s_store.load_generation(Path(second["generation_dir"]))


def test_expected_contract_mismatch_is_rejected(tmp_path):
    result = _build(tmp_path)
    generation = Path(result["generation_dir"])

    with pytest.raises(SparseIndexMismatchError, match="epsilon"):
        bm25s_store.load_generation(
            generation,
            expected_contract={"bm25": {"epsilon": 0.5}},
        )
    with pytest.raises(SparseIndexMismatchError, match="parser_version"):
        bm25s_store.load_generation(
            generation,
            expected_contract={"chunking": {"parser_version": "other"}},
        )


def test_active_manifest_hash_mismatch_is_rejected(tmp_path):
    _build(tmp_path, publish=True)
    active_path = tmp_path / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["manifest_hash"] = "0" * 64
    active_path.write_text(json.dumps(active), encoding="utf-8")

    with pytest.raises(SparseIndexCorruptError, match="manifest hash"):
        bm25s_store.load_active(tmp_path)


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([], "空语料"),
        ([_chunk("c1", "一"), _chunk("c1", "二")], "chunk_id重复"),
        ([{"chunk_id": "c1", "content": "一", "metadata": {}}], "document_id"),
        ([_chunk("c1", "!!!")], "分词结果为空"),
    ],
)
def test_invalid_corpus_is_rejected_without_leaving_generation(tmp_path, chunks, message):
    with pytest.raises(SparseIndexBuildError, match=message):
        bm25s_store.build_generation(
            chunks=chunks,
            target_root=tmp_path,
            corpus_id="invalid",
            tokenize=sparse_retriever.tokenize,
            tokenizer=_tokenizer_contract(),
            chunking=_chunking_contract(),
            source=_source_contract(),
            generation_id="invalid",
        )
    assert not (tmp_path / "generations" / "invalid").exists()


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"k1": 0}, "k1"),
        ({"b": 1.1}, "b"),
        ({"epsilon": -0.1}, "epsilon"),
    ],
)
def test_invalid_parameters_are_rejected(tmp_path, parameters, message):
    with pytest.raises(SparseIndexBuildError, match=message):
        _build(tmp_path, **parameters)


def test_invalid_generation_id_does_not_escape_target(tmp_path):
    with pytest.raises(SparseIndexBuildError, match="generation_id"):
        _build(tmp_path, "../escape")
    assert not (tmp_path.parent / "escape").exists()


def test_missing_active_and_missing_generation_are_explicit(tmp_path):
    with pytest.raises(SparseIndexMissingError, match="active.json"):
        bm25s_store.load_active(tmp_path)
    with pytest.raises(SparseIndexMissingError, match="generation目录"):
        bm25s_store.load_generation(tmp_path / "missing")


def test_failed_build_removes_temporary_generation(tmp_path, monkeypatch):
    def fail_save(*args, **kwargs):
        raise OSError("simulated save failure")

    monkeypatch.setattr(bm25s_store.bm25s.BM25, "save", fail_save)
    with pytest.raises(OSError, match="simulated"):
        _build(tmp_path)
    generations = tmp_path / "generations"
    assert not generations.exists() or list(generations.iterdir()) == []


def test_copied_generation_can_be_loaded_independently(tmp_path):
    result = _build(tmp_path)
    copied = tmp_path / "backup" / "generations" / "test-generation"
    copied.parent.mkdir(parents=True)
    shutil.copytree(result["generation_dir"], copied)
    with bm25s_store.load_generation(copied) as loaded:
        assert loaded.search_tokens(sparse_retriever.tokenize("数据库"), 1)


def test_generation_directory_name_must_match_manifest(tmp_path):
    result = _build(tmp_path)
    copied = tmp_path / "wrong-name"
    shutil.copytree(result["generation_dir"], copied)
    with pytest.raises(SparseIndexMismatchError, match="目录名"):
        bm25s_store.load_generation(copied)


def test_concurrent_read_is_deterministic_and_close_releases_files(tmp_path):
    result = _build(tmp_path)
    generation = Path(result["generation_dir"])
    loaded = bm25s_store.load_generation(generation)
    tokens = sparse_retriever.tokenize("BM25 中文")
    expected = loaded.search_tokens(tokens, 5)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: loaded.search_tokens(tokens, 5), range(64)))
    assert all(result == expected for result in results)
    loaded.close()

    moved = generation.with_name("temporarily-moved")
    generation.rename(moved)
    moved.rename(generation)


def test_manifest_schema_version_mismatch_is_rejected(tmp_path):
    result = _build(tmp_path)
    manifest_path = Path(result["generation_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SparseIndexMismatchError, match="schema"):
        bm25s_store.load_generation(Path(result["generation_dir"]))


def test_same_input_produces_identical_index_files(tmp_path):
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")
    first_manifest = json.loads(
        (Path(first["generation_dir"]) / "manifest.json").read_text(encoding="utf-8")
    )
    second_manifest = json.loads(
        (Path(second["generation_dir"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert first_manifest["files"] == second_manifest["files"]
    assert first_manifest["corpus"] == second_manifest["corpus"]
