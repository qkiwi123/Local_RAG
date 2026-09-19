"""P1-5 Reranker 精排链路测试。"""

import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.config import _env_bool, settings
from backend.services import reranker, retriever
from eval import adapters
from eval import eval as eval_cli


def _result(chunk_id, distance, document_name="doc-a.txt", content=None):
    return {
        "chunk_id": chunk_id,
        "content": content or f"content-{chunk_id}",
        "metadata": {
            "document_name": document_name,
            "page_number": 1,
        },
        "distance": distance,
    }


class _FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def compute_score(self, pairs):
        self.calls.append(pairs)
        return self.scores


def test_rerank_candidates_sorts_by_score_and_preserves_dense_fields(monkeypatch):
    model = _FakeReranker([0.2, 0.9, -0.1])
    monkeypatch.setattr(reranker, "_get_model", lambda: model)
    monkeypatch.setattr(settings, "RERANKER_DEVICE", "cpu")
    candidates = [
        {**_result("a", 0.10), "dense_rank": 1},
        {**_result("b", 0.20), "dense_rank": 2},
        {**_result("c", 0.30), "dense_rank": 3},
    ]

    ranked = reranker.rerank("测试问题", candidates)

    assert model.calls == [[
        ("测试问题", "content-a"),
        ("测试问题", "content-b"),
        ("测试问题", "content-c"),
    ]]
    assert [row["chunk_id"] for row in ranked] == ["b", "a", "c"]
    assert [row["reranker_score"] for row in ranked] == [0.9, 0.2, -0.1]
    assert ranked[0]["distance"] == 0.20
    assert ranked[0]["dense_rank"] == 2


def test_rerank_empty_candidates_does_not_load_model(monkeypatch):
    monkeypatch.setattr(
        reranker,
        "_get_model",
        lambda: (_ for _ in ()).throw(AssertionError("不应加载模型")),
    )

    assert reranker.rerank("测试问题", []) == []


def test_retrieve_reranks_all_filtered_candidates_before_document_limit(monkeypatch):
    raw = [
        _result("a1", 0.10),
        _result("a2", 0.20),
        _result("a3", 0.30),
        _result("a4", 0.40),
        _result("b1", 0.50, document_name="doc-b.txt"),
        _result("filtered", 0.80, document_name="doc-c.txt"),
    ]
    calls = []

    def fake_search(question, top_k, allowed_document_ids=None):
        calls.append((question, top_k))
        return raw

    def fake_rerank(question, candidates):
        assert [row["chunk_id"] for row in candidates] == [
            "a1", "a2", "a3", "a4", "b1"
        ]
        scores = {"b1": 10.0, "a4": 9.0, "a3": 8.0, "a2": 7.0, "a1": 6.0}
        return sorted(
            ({**row, "reranker_score": scores[row["chunk_id"]]} for row in candidates),
            key=lambda row: row["reranker_score"],
            reverse=True,
        )

    monkeypatch.setattr(retriever, "vector_search", fake_search)
    monkeypatch.setattr(retriever, "rerank", fake_rerank)
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 6, raising=False)
    monkeypatch.setattr(settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67, raising=False)

    results = retriever.retrieve("测试问题", top_k=4)

    assert calls == [("测试问题", 6)]
    # doc-a 的第 4 条只在 rerank 后才因每文档最多 3 条被移除。
    assert [row["chunk_id"] for row in results] == ["b1", "a4", "a3", "a2"]
    assert [row["dense_rank"] for row in results] == [5, 4, 3, 2]


def test_retrieve_without_reranker_keeps_p1_4_candidate_behavior(monkeypatch):
    calls = []

    def fake_search(question, top_k, allowed_document_ids=None):
        calls.append((question, top_k))
        return [_result("a", 0.20)]

    monkeypatch.setattr(retriever, "vector_search", fake_search)
    monkeypatch.setattr(
        retriever,
        "rerank",
        lambda *_: (_ for _ in ()).throw(AssertionError("不应调用 reranker")),
        raising=False,
    )
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67, raising=False)

    results = retriever.retrieve("测试问题", top_k=3)

    assert calls == [("测试问题", 6)]
    assert [row["chunk_id"] for row in results] == ["a"]
    assert results[0]["dense_rank"] == 1
    assert "reranker_score" not in results[0]


def test_eval_run_sets_and_records_reranker_config(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_cli, "EVAL_DIR", tmp_path)
    args = SimpleNamespace(
        label="reranker-config-test",
        overwrite=False,
        embedding_model=None,
        query_instruction=None,
        top_k=None,
        distance_threshold=None,
        reranker_enabled=True,
        reranker_model="BAAI/bge-reranker-v2-m3",
        reranker_candidate_k=15,
        reranker_device="cuda:0",
        reranker_use_fp16=True,
        reranker_batch_size=8,
    )

    eval_cli._setup_env(args)

    assert os.environ["RERANKER_ENABLED"] == "true"
    assert os.environ["RERANKER_MODEL"] == "BAAI/bge-reranker-v2-m3"
    assert os.environ["RERANKER_CANDIDATE_K"] == "15"
    assert os.environ["RERANKER_DEVICE"] == "cuda:0"
    assert os.environ["RERANKER_USE_FP16"] == "true"
    assert os.environ["RERANKER_BATCH_SIZE"] == "8"

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3", raising=False
    )
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 15, raising=False)
    monkeypatch.setattr(settings, "RERANKER_DEVICE", "cuda:0", raising=False)
    monkeypatch.setattr(settings, "RERANKER_USE_FP16", True, raising=False)
    monkeypatch.setattr(settings, "RERANKER_BATCH_SIZE", 8, raising=False)
    config = adapters.get_run_config()
    assert config["reranker_enabled"] is True
    assert config["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert config["reranker_candidate_k"] == 15
    assert config["reranker_device"] == "cuda:0"
    assert config["reranker_use_fp16"] is True
    assert config["reranker_batch_size"] == 8


def test_selected_reranker_defaults_keep_safe_switch_and_use_k15(monkeypatch):
    """实验选择 k=15；未显式配置时仍保持安全关闭。"""
    monkeypatch.delenv("RERANKER_SAFE_DEFAULT_TEST", raising=False)
    assert _env_bool("RERANKER_SAFE_DEFAULT_TEST", False) is False
    assert settings.RERANKER_MODEL == "BAAI/bge-reranker-v2-m3"
    assert settings.RERANKER_CANDIDATE_K == 15


def test_reranker_model_receives_device_and_precision_config(monkeypatch):
    captured = {}

    class _ConfiguredReranker:
        def __init__(self, model_path, **kwargs):
            captured["model_path"] = model_path
            captured.update(kwargs)

    # 这里只验证配置传递，不导入完整模型库或读取真实权重。
    fake_flag_embedding = ModuleType("FlagEmbedding")
    fake_flag_embedding.FlagReranker = _ConfiguredReranker
    monkeypatch.setitem(sys.modules, "FlagEmbedding", fake_flag_embedding)
    monkeypatch.setattr(
        "backend.services.model_cache.resolve_cached_snapshot",
        lambda model_name: f"cached/{model_name}",
    )
    monkeypatch.setattr(settings, "RERANKER_DEVICE", "cpu")
    monkeypatch.setattr(settings, "RERANKER_USE_FP16", False)
    monkeypatch.setattr(settings, "RERANKER_BATCH_SIZE", 8)
    monkeypatch.setattr(settings, "RERANKER_MAX_LENGTH", 512)
    reranker._get_model.cache_clear()

    reranker._get_model()

    assert captured == {
        "model_path": "cached/BAAI/bge-reranker-v2-m3",
        "use_fp16": False,
        "devices": "cpu",
        "batch_size": 8,
        "max_length": 512,
    }
    reranker._get_model.cache_clear()


@pytest.mark.integration
def test_cached_bge_reranker_scores_relevant_passage_higher():
    """真实本地模型兼容性金丝雀；CI 子集不加载模型。"""
    reranker._get_model.cache_clear()
    ranked = reranker.rerank(
        "什么是检索增强生成？",
        [
            _result(
                "relevant",
                0.2,
                content="检索增强生成结合外部知识库与大语言模型。",
            ),
            _result(
                "irrelevant",
                0.3,
                content="员工每年享有五天带薪年假。",
            ),
        ],
    )

    assert [row["chunk_id"] for row in ranked] == ["relevant", "irrelevant"]
    assert ranked[0]["reranker_score"] > ranked[1]["reranker_score"]
