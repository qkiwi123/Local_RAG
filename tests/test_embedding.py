"""
测试 backend/services/embedding.py — 嵌入模型。

覆盖：
- 向量维度正确（all-MiniLM-L6-v2 → 384 维）
- 向量值在合理范围内（归一化后应在 [-1, 1]）
- 语义相近的句子余弦相似度更高
- 批处理与单条处理结果一致
- 空列表输入处理
"""

import numpy as np
from backend.services.embedding import embed_texts, embed_query


def _cosine_similarity(a, b):
    """计算两个向量的余弦相似度（向量已归一化时等价于点积）。"""
    return np.dot(a, b)


class TestEmbedTexts:
    """批量向量化测试。"""

    def test_output_dimension(self):
        """all-MiniLM-L6-v2 输出应为 384 维。"""
        texts = ["Hello world", "This is a test"]
        embeddings = embed_texts(texts)
        assert len(embeddings) == 2
        for vec in embeddings:
            assert len(vec) == 384, f"Expected 384, got {len(vec)}"

    def test_values_in_range(self):
        """归一化后的向量值应在 [-1, 1] 范围内。"""
        embeddings = embed_texts(["Test sentence for value range checking."])
        vec = embeddings[0]
        assert all(-1.0 <= v <= 1.0 for v in vec), "Values should be in [-1, 1]"

    def test_empty_input(self):
        """空列表应返回空列表。"""
        assert embed_texts([]) == []

    def test_semantic_similarity(self):
        """
        语义相近的句子余弦相似度应明显高于无关句子。

        注意：all-MiniLM-L6-v2 是英文模型，对中文支持有限，
        因此本测试使用英文句子。阶段二切换 BAAI/bge-large-zh-v1.5
        后应补充中文语义相似度测试。
        """
        similar_1 = "Employees are entitled to five days of paid annual leave per year."
        similar_2 = "Workers receive 5 days of paid vacation annually."
        unrelated = "The weather today is very sunny and warm."

        embeddings = embed_texts([similar_1, similar_2, unrelated])

        sim_relevant = _cosine_similarity(embeddings[0], embeddings[1])
        sim_irrelevant_1 = _cosine_similarity(embeddings[0], embeddings[2])
        sim_irrelevant_2 = _cosine_similarity(embeddings[1], embeddings[2])

        print(f"\n  Similarity (related):    {sim_relevant:.4f}")
        print(f"  Similarity (unrelated 1): {sim_irrelevant_1:.4f}")
        print(f"  Similarity (unrelated 2): {sim_irrelevant_2:.4f}")

        # 相关句之间的相似度应明显高于无关句
        assert sim_relevant > sim_irrelevant_1, (
            f"Related sentences should be more similar ({sim_relevant:.4f}) "
            f"than unrelated ({sim_irrelevant_1:.4f})"
        )
        assert sim_relevant > sim_irrelevant_2, (
            f"Related sentences should be more similar ({sim_relevant:.4f}) "
            f"than unrelated ({sim_irrelevant_2:.4f})"
        )


class TestEmbedQuery:
    """单条查询向量化测试。"""

    def test_returns_384_dim_vector(self):
        """embed_query 返回 384 维向量。"""
        vec = embed_query("What is the annual leave policy?")
        assert len(vec) == 384

    def test_consistent_with_embed_texts(self):
        """embed_query 的结果应与 embed_texts([text])[0] 一致。"""
        text = "Testing consistency between single and batch embedding."
        vec_single = embed_query(text)
        vec_batch = embed_texts([text])[0]
        # 浮点数精度，应完全一致
        assert vec_single == vec_batch
