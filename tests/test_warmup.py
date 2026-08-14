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

    warmup.warmup_models()

    assert calls[0][0] == "embed"
    assert calls[1][0] == "rerank"


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

    warmup.warmup_models()

    assert calls == ["embed"]


def test_warmup_failures_do_not_raise(monkeypatch):
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(
        "backend.services.embedding.embed_texts",
        lambda texts: (_ for _ in ()).throw(RuntimeError("加载失败")),
    )
    monkeypatch.setattr(
        "backend.services.reranker.rerank",
        lambda question, candidates: (_ for _ in ()).throw(
            RuntimeError("加载失败")
        ),
    )

    warmup.warmup_models()


def test_background_warmup_respects_switch(monkeypatch):
    started = []
    monkeypatch.setattr(settings, "PREWARM_MODELS", False)
    monkeypatch.setattr(warmup, "warmup_models", lambda: started.append(True))

    warmup.start_background_warmup()

    assert started == []
