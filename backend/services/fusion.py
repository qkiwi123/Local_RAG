"""Dense 与 BM25 的 Reciprocal Rank Fusion（只融合排名，不混合原始分数）。"""

from __future__ import annotations


def reciprocal_rank_fusion(
    dense_results: list[dict],
    sparse_results: list[dict],
    rrf_k: int,
    top_k: int,
) -> list[dict]:
    """按稳定 chunk_id 去重并累加双路倒数排名分数。"""
    if top_k <= 0:
        return []

    fused: dict[str, dict] = {}
    sources = (
        (dense_results, "dense_rank"),
        (sparse_results, "bm25_rank"),
    )
    for source, rank_key in sources:
        for fallback_rank, row in enumerate(source, 1):
            rank = row.get(rank_key) or fallback_rank
            chunk_id = row["chunk_id"]
            current = fused.setdefault(
                chunk_id,
                {
                    **row,
                    "dense_rank": None,
                    "bm25_rank": None,
                    "rrf_score": 0.0,
                },
            )
            current.update(
                {key: value for key, value in row.items() if value is not None}
            )
            current[rank_key] = rank
            current["rrf_score"] += 1.0 / (rrf_k + rank)

    return sorted(
        fused.values(),
        key=lambda row: (
            -row["rrf_score"],
            min(
                row.get("dense_rank") or 10**9,
                row.get("bm25_rank") or 10**9,
            ),
            row["chunk_id"],
        ),
    )[:top_k]
