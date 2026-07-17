"""
测试 backend/services/embedding.py — 嵌入模型。

覆盖：
- 向量维度正确（BAAI/bge-large-zh-v1.5 → 1024 维）
- 向量值在合理范围内（归一化后应在 [-1, 1]）
- 语义相近的句子余弦相似度更高
- 批处理与单条处理结果一致
- 空列表输入处理
"""

import numpy as np
import pytest
from backend.services.embedding import embed_texts, embed_query

pytestmark = pytest.mark.integration


def _cosine_similarity(a, b):
    """计算两个向量的余弦相似度（向量已归一化时等价于点积）。"""
    return np.dot(a, b)


class TestEmbedTexts:
    """批量向量化测试。"""

    def test_output_dimension(self):
        """bge-large-zh-v1.5 输出应为 1024 维。"""
        texts = ["你好，世界", "这是一个测试"]
        embeddings = embed_texts(texts)
        assert len(embeddings) == 2
        for vec in embeddings:
            assert len(vec) == 1024, f"Expected 1024, got {len(vec)}"

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

        默认模型面向中文检索，因此使用中文语义样例。
        """
        similar_1 = "员工每年享有五天带薪年假。"
        similar_2 = "公司员工每年可以获得五天带薪休假。"
        unrelated = "今天的天气晴朗，适合户外运动。"

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

    def test_returns_1024_dim_vector(self):
        """embed_query 返回 1024 维向量。"""
        vec = embed_query("公司的年假政策是什么？")
        assert len(vec) == 1024

    def test_consistent_with_embed_texts(self):
        """embed_query 的结果应与 embed_texts([text])[0] 一致。"""
        text = "Testing consistency between single and batch embedding."
        vec_single = embed_query(text)
        vec_batch = embed_texts([text])[0]
        # 浮点数精度，应完全一致
        assert vec_single == vec_batch
