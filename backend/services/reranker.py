"""Cross-encoder Reranker：对 Dense 候选按 query/chunk 相关性重新排序。"""

from functools import lru_cache
from numbers import Real
from typing import List

from backend.config import settings


@lru_cache(maxsize=1)
def _get_model():
    """从本地 Hugging Face 缓存加载一次模型，服务运行时不隐式联网。"""
    from FlagEmbedding import FlagReranker
    from backend.services.model_cache import resolve_cached_snapshot
    from backend.services.torch_device import require_torch_device

    require_torch_device(settings.RERANKER_DEVICE, "Reranker")
    return FlagReranker(
        resolve_cached_snapshot(settings.RERANKER_MODEL),
        use_fp16=settings.RERANKER_USE_FP16,
        devices=settings.RERANKER_DEVICE,
        batch_size=settings.RERANKER_BATCH_SIZE,
        max_length=settings.RERANKER_MAX_LENGTH,
    )


def rerank(question: str, candidates: List[dict]) -> List[dict]:
    """给全部候选写入 reranker_score，并按分数从高到低返回新列表。"""
    if not candidates:
        return []

    pairs = [(question, row["content"]) for row in candidates]
    model = _get_model()
    scores = model.compute_score(pairs)
    if settings.RERANKER_DEVICE.startswith("cuda"):
        from backend.services.torch_device import assert_model_uses_device

        assert_model_uses_device(model.model, settings.RERANKER_DEVICE, "Reranker")
    if isinstance(scores, Real):
        scores = [scores]
    if len(scores) != len(candidates):
        raise RuntimeError(
            f"Reranker 返回 {len(scores)} 个分数，但候选有 {len(candidates)} 条"
        )

    ranked = [
        {**row, "reranker_score": float(score)}
        for row, score in zip(candidates, scores)
    ]
    return sorted(ranked, key=lambda row: row["reranker_score"], reverse=True)
