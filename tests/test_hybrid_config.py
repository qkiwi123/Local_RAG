"""P2-1 混合检索配置测试。"""

import pytest

from backend.config import settings


def test_hybrid_retrieval_defaults():
    assert settings.RETRIEVAL_MODE == "dense"
    assert settings.BM25_TOP_K == 15
    assert settings.BM25_K1 == 1.5
    assert settings.BM25_B == 0.75
    assert settings.SPARSE_BACKEND == "rank_bm25_memory"
    assert settings.RRF_K == 60


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("RETRIEVAL_MODE", "invalid", "RETRIEVAL_MODE"),
        ("BM25_TOP_K", 0, "BM25_TOP_K"),
        ("BM25_K1", 0, "BM25_K1"),
        ("BM25_B", 1.1, "BM25_B"),
        ("RRF_K", 0, "RRF_K"),
        ("SPARSE_BACKEND", "invalid", "SPARSE_BACKEND"),
    ],
)
def test_hybrid_retrieval_config_validation(
    monkeypatch, attribute, value, message
):
    monkeypatch.setattr(settings, attribute, value, raising=False)

    with pytest.raises(ValueError, match=message):
        settings._validate()


def test_mmap_backend_requires_corpus_id(monkeypatch):
    monkeypatch.setattr(settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(settings, "SPARSE_INDEX_CORPUS_ID", "")

    with pytest.raises(ValueError, match="SPARSE_INDEX_CORPUS_ID"):
        settings._validate()


def test_mmap_backend_requires_index_root(monkeypatch):
    monkeypatch.setattr(settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(settings, "SPARSE_INDEX_CORPUS_ID", "test-corpus")
    monkeypatch.setattr(settings, "SPARSE_INDEX_ROOT", "")

    with pytest.raises(ValueError, match="SPARSE_INDEX_ROOT"):
        settings._validate()
