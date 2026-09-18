"""模型预热测试：只验证调度逻辑，不实际加载模型。"""

from backend.config import settings
from backend.services import warmup


def test_warmup_loads_embedding_and_reranker_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(
        "backend.services.embedding.embed_texts",
        lambda texts: calls.append(("embed", texts)) or [[0.0]],
    )
    monkeypatch.setattr(
        "backend.services.reranker.rerank",
        lambda question, candidates: calls.append(("rerank", question))
        or candidates,
    )

    assert warmup.warmup_models() is True

    assert calls[0][0] == "embed"
    assert calls[1][0] == "rerank"
    assert warmup.get_warmup_status()["status"] == "ready"


def test_warmup_skips_reranker_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(
        "backend.services.embedding.embed_texts",
        lambda texts: calls.append("embed") or [[0.0]],
    )
    monkeypatch.setattr(
        "backend.services.reranker.rerank",
        lambda question, candidates: calls.append("rerank") or candidates,
    )

    assert warmup.warmup_models() is True

    assert calls == ["embed"]
    assert warmup.get_warmup_status()["status"] == "ready"


def test_embedding_failure_stops_warmup_and_records_status(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(
        "backend.services.embedding.embed_texts",
        lambda texts: (_ for _ in ()).throw(RuntimeError("加载失败")),
    )
    monkeypatch.setattr(
        "backend.services.reranker.rerank",
        lambda question, candidates: calls.append("reranker"),
    )

    assert warmup.warmup_models() is False
    assert calls == []
    assert warmup.get_warmup_status() == {
        "status": "failed",
        "component": "embedding",
        "error_type": "RuntimeError",
        "detail": "加载失败",
    }


def test_reranker_failure_records_status(monkeypatch):
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(
        "backend.services.embedding.embed_texts",
        lambda texts: [[0.0]],
    )
    monkeypatch.setattr(
        "backend.services.reranker.rerank",
        lambda question, candidates: (_ for _ in ()).throw(
            RuntimeError("重排加载失败")
        ),
    )

    assert warmup.warmup_models() is False
    assert warmup.get_warmup_status() == {
        "status": "failed",
        "component": "reranker",
        "error_type": "RuntimeError",
        "detail": "重排加载失败",
    }


def test_background_warmup_respects_switch(monkeypatch):
    started = []
    monkeypatch.setattr(settings, "PREWARM_MODELS", False)
    monkeypatch.setattr(warmup, "warmup_models", lambda: started.append(True))

    warmup.start_background_warmup()

    assert started == []
    assert warmup.get_warmup_status()["status"] == "disabled"
