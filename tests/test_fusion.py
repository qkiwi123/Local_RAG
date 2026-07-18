"""P2-1 Reciprocal Rank Fusion 测试。"""

import pytest

from backend.services.fusion import reciprocal_rank_fusion


def _row(chunk_id: str, **extra) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"content-{chunk_id}",
        "metadata": {"document_name": "doc.txt", "page_number": 1},
        **extra,
    }


def test_rrf_fuses_by_chunk_id_and_preserves_branch_ranks():
    dense = [
        _row("a", distance=0.1, dense_rank=1),
        _row("b", distance=0.2, dense_rank=2),
    ]
    sparse = [
        _row("b", distance=None, bm25_score=2.0, bm25_rank=1),
        _row("c", distance=None, bm25_score=1.0, bm25_rank=2),
    ]

    rows = reciprocal_rank_fusion(dense, sparse, rrf_k=60, top_k=3)

    assert [row["chunk_id"] for row in rows] == ["b", "a", "c"]
    assert rows[0]["dense_rank"] == 2
    assert rows[0]["bm25_rank"] == 1
    assert rows[0]["distance"] == 0.2
    assert rows[0]["bm25_score"] == 2.0
    assert rows[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_accepts_single_branch_and_respects_top_k():
    rows = reciprocal_rank_fusion(
        [],
        [_row("z", bm25_rank=1), _row("a", bm25_rank=2)],
        rrf_k=60,
        top_k=1,
    )

    assert [row["chunk_id"] for row in rows] == ["z"]
    assert rows[0]["dense_rank"] is None


def test_rrf_empty_or_zero_top_k_returns_empty():
    assert reciprocal_rank_fusion([], [], rrf_k=60, top_k=5) == []
    assert reciprocal_rank_fusion([_row("a")], [], rrf_k=60, top_k=0) == []
