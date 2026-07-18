# P2-1 Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add application-side Chinese BM25 and rank-based RRF fusion to the existing Chroma Dense + Reranker pipeline, then decide deployment from same-dataset ablation evidence.

**Architecture:** Chroma remains the source of truth. A lazy in-memory BM25 index reads chunk snapshots from Chroma and rebuilds after the vector-store revision changes. Dense and BM25 results are fused by stable chunk ID using reciprocal ranks, optionally reranked, and returned through the existing retrieval contract.

**Tech Stack:** Python 3.14, Chroma, jieba 0.42.1, rank-bm25 0.2.2, FlagEmbedding, pytest, existing eval CLI.

---

### Task 1: Dependencies and validated configuration

**Files:**
- Modify: `requirements.txt`
- Modify: `backend/config.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_hybrid_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add failing configuration tests**

```python
from backend.config import settings


def test_hybrid_defaults(monkeypatch):
    assert settings.RETRIEVAL_MODE == "dense"
    assert settings.BM25_TOP_K == 15
    assert settings.BM25_K1 == 1.5
    assert settings.BM25_B == 0.75
    assert settings.RRF_K == 60


def test_validate_rejects_unknown_retrieval_mode(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "invalid")
    try:
        settings.validate()
    except ValueError as exc:
        assert "RETRIEVAL_MODE" in str(exc)
    else:
        raise AssertionError("非法检索模式必须失败")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_config.py -q -p no:cacheprovider`

Expected: FAIL because the five settings do not exist.

- [ ] **Step 3: Add dependencies and minimal settings**

Add to `requirements.txt`:

```text
jieba==0.42.1
rank-bm25==0.2.2
```

Add to `Settings`:

```python
RETRIEVAL_MODE: str = os.getenv("RETRIEVAL_MODE", "dense").strip().lower()
BM25_TOP_K: int = int(os.getenv("BM25_TOP_K", "15"))
BM25_K1: float = float(os.getenv("BM25_K1", "1.5"))
BM25_B: float = float(os.getenv("BM25_B", "0.75"))
RRF_K: int = int(os.getenv("RRF_K", "60"))
```

Add validation:

```python
if self.RETRIEVAL_MODE not in {"dense", "bm25", "hybrid"}:
    raise ValueError("RETRIEVAL_MODE 必须是 dense、bm25 或 hybrid")
if self.BM25_TOP_K <= 0:
    raise ValueError("BM25_TOP_K 必须大于 0")
if self.BM25_K1 <= 0:
    raise ValueError("BM25_K1 必须大于 0")
if not 0 <= self.BM25_B <= 1:
    raise ValueError("BM25_B 必须在 0 到 1 之间")
if self.RRF_K <= 0:
    raise ValueError("RRF_K 必须大于 0")
```

Set `os.environ["RETRIEVAL_MODE"] = "dense"` in `tests/conftest.py`. Add the five variables to `.env.example`, keeping `dense` until the deployment gate passes.

- [ ] **Step 4: Install dependencies and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pip install jieba==0.42.1 rank-bm25==0.2.2`

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_config.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add requirements.txt backend/config.py tests/conftest.py tests/test_hybrid_config.py .env.example
git commit -m "feat: add hybrid retrieval configuration"
```

### Task 2: Chroma chunk snapshot and revision lifecycle

**Files:**
- Modify: `backend/services/vector_store.py`
- Modify: `tests/test_vector_store.py`

- [ ] **Step 1: Add failing snapshot and revision tests**

```python
def test_get_all_chunks_returns_sparse_index_contract(monkeypatch):
    from backend.services import vector_store

    class FakeCollection:
        def get(self, include):
            assert include == ["documents", "metadatas"]
            return {
                "ids": ["c1"],
                "documents": ["Qwen 2.5 模型"],
                "metadatas": [{"document_name": "doc.txt", "page_number": 1}],
            }

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    assert vector_store.get_all_chunks() == [{
        "chunk_id": "c1",
        "content": "Qwen 2.5 模型",
        "metadata": {"document_name": "doc.txt", "page_number": 1},
    }]


def test_revision_changes_after_successful_add_and_delete(monkeypatch):
    from backend.services import vector_store
    start = vector_store.get_index_revision()
    monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0]])

    class FakeCollection:
        def add(self, **kwargs):
            pass
        def delete(self, **kwargs):
            pass

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    chunk = _document_chunk("c1")
    vector_store.add_chunks([chunk])
    assert vector_store.get_index_revision() == start + 1
    assert vector_store.delete_document_by_id("doc-1") is True
    assert vector_store.get_index_revision() == start + 2
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_vector_store.py -q -p no:cacheprovider`

Expected: FAIL because `get_all_chunks` and `get_index_revision` do not exist.

- [ ] **Step 3: Implement revision and snapshot**

```python
_index_revision = 0


def _bump_index_revision() -> None:
    global _index_revision
    _index_revision += 1


def get_index_revision() -> int:
    return _index_revision


def get_all_chunks() -> List[dict]:
    results = _collection.get(include=["documents", "metadatas"])
    return [
        {"chunk_id": chunk_id, "content": content, "metadata": metadata or {}}
        for chunk_id, content, metadata in zip(
            results.get("ids", []),
            results.get("documents", []),
            results.get("metadatas", []),
        )
    ]
```

Call `_bump_index_revision()` only after successful `_collection.add()` and successful delete operations.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_vector_store.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/services/vector_store.py tests/test_vector_store.py
git commit -m "feat: expose Chroma chunks for sparse indexing"
```

### Task 3: Chinese BM25 sparse retrieval

**Files:**
- Create: `backend/services/sparse_retriever.py`
- Create: `tests/test_sparse_retriever.py`

- [ ] **Step 1: Add failing tokenizer, ranking, zero-score and rebuild tests**

```python
from backend.services import sparse_retriever


def test_tokenize_preserves_chinese_model_and_number_tokens():
    tokens = sparse_retriever.tokenize("Qwen 2.5 支持中文，压缩 30-50%。")
    assert "qwen" in tokens
    assert "2.5" in tokens
    assert "中文" in tokens
    assert any(token in {"30", "50", "30-50"} for token in tokens)


def test_bm25_ranks_exact_model_name_first(monkeypatch):
    chunks = [
        _chunk("c1", "Claude Code、AutoGPT、CrewAI 是 Agent 框架"),
        _chunk("c2", "智能体可以规划任务和调用工具"),
        _chunk("c3", "向量数据库保存文档嵌入"),
    ]
    monkeypatch.setattr(sparse_retriever.vector_store, "get_all_chunks", lambda: chunks)
    monkeypatch.setattr(sparse_retriever.vector_store, "get_index_revision", lambda: 1)
    sparse_retriever.clear_cache()
    results = sparse_retriever.search("CrewAI 框架", top_k=2)
    assert [row["chunk_id"] for row in results] == ["c1"]
    assert results[0]["bm25_score"] > 0
    assert results[0]["bm25_rank"] == 1
    assert results[0]["distance"] is None


def test_bm25_excludes_zero_score_results(monkeypatch):
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: [_chunk("c1", "RAG 检索增强生成")],
    )
    monkeypatch.setattr(sparse_retriever.vector_store, "get_index_revision", lambda: 1)
    sparse_retriever.clear_cache()
    assert sparse_retriever.search("带薪年假", top_k=5) == []


def test_bm25_rebuilds_when_vector_revision_changes(monkeypatch):
    state = {"revision": 1, "chunks": [_chunk("c1", "旧内容")]}
    monkeypatch.setattr(sparse_retriever.vector_store, "get_all_chunks", lambda: state["chunks"])
    monkeypatch.setattr(sparse_retriever.vector_store, "get_index_revision", lambda: state["revision"])
    sparse_retriever.clear_cache()
    assert sparse_retriever.search("新型号", 5) == []
    state.update(revision=2, chunks=[_chunk("c2", "新型号 Qdrant")])
    assert sparse_retriever.search("Qdrant", 5)[0]["chunk_id"] == "c2"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_sparse_retriever.py -q -p no:cacheprovider`

Expected: import failure because `sparse_retriever.py` does not exist.

- [ ] **Step 3: Implement lazy BM25 index**

```python
import re
import threading
import unicodedata

import jieba
from rank_bm25 import BM25Okapi

from backend.config import settings
from backend.services import vector_store

_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.+%\-]+")
_HAS_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_lock = threading.Lock()
_cache = {"revision": None, "chunks": [], "model": None}


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return [
        token
        for part in _TOKEN_RE.findall(normalized)
        for token in jieba.lcut(part, cut_all=False)
        if token.strip() and _HAS_WORD_RE.search(token)
    ]


def clear_cache() -> None:
    with _lock:
        _cache.update(revision=None, chunks=[], model=None)


def _get_index():
    revision = vector_store.get_index_revision()
    with _lock:
        if _cache["revision"] != revision:
            chunks = vector_store.get_all_chunks()
            corpus = [tokenize(row["content"]) for row in chunks]
            model = BM25Okapi(corpus, k1=settings.BM25_K1, b=settings.BM25_B) if corpus else None
            _cache.update(revision=revision, chunks=chunks, model=model)
        return _cache["chunks"], _cache["model"]


def search(query: str, top_k: int) -> list[dict]:
    if top_k <= 0 or not query.strip():
        return []
    chunks, model = _get_index()
    if model is None:
        return []
    scores = model.get_scores(tokenize(query))
    ranked = sorted(
        ((index, float(score)) for index, score in enumerate(scores) if score > 0),
        key=lambda item: (-item[1], chunks[item[0]]["chunk_id"]),
    )[:top_k]
    return [
        {
            **chunks[index],
            "distance": None,
            "bm25_score": score,
            "bm25_rank": rank,
        }
        for rank, (index, score) in enumerate(ranked, 1)
    ]
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_sparse_retriever.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/services/sparse_retriever.py tests/test_sparse_retriever.py
git commit -m "feat: add Chinese BM25 retrieval"
```

### Task 4: Reciprocal rank fusion

**Files:**
- Create: `backend/services/fusion.py`
- Create: `tests/test_fusion.py`

- [ ] **Step 1: Add failing RRF tests**

```python
from backend.services.fusion import reciprocal_rank_fusion


def test_rrf_fuses_by_chunk_id_and_preserves_branch_ranks():
    dense = [_row("a", dense_rank=1), _row("b", dense_rank=2)]
    sparse = [_row("b", bm25_rank=1), _row("c", bm25_rank=2)]
    rows = reciprocal_rank_fusion(dense, sparse, rrf_k=60, top_k=3)
    assert [row["chunk_id"] for row in rows] == ["b", "a", "c"]
    assert rows[0]["dense_rank"] == 2
    assert rows[0]["bm25_rank"] == 1
    assert rows[0]["rrf_score"] == 1 / 62 + 1 / 61


def test_rrf_accepts_sparse_only_candidates_and_is_deterministic():
    rows = reciprocal_rank_fusion([], [_row("z"), _row("a")], 60, 2)
    assert [row["chunk_id"] for row in rows] == ["z", "a"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_fusion.py -q -p no:cacheprovider`

Expected: import failure because `fusion.py` does not exist.

- [ ] **Step 3: Implement pure RRF**

```python
def reciprocal_rank_fusion(
    dense_results: list[dict],
    sparse_results: list[dict],
    rrf_k: int,
    top_k: int,
) -> list[dict]:
    fused: dict[str, dict] = {}
    for source, rank_key in ((dense_results, "dense_rank"), (sparse_results, "bm25_rank")):
        for fallback_rank, row in enumerate(source, 1):
            rank = row.get(rank_key) or fallback_rank
            chunk_id = row["chunk_id"]
            current = fused.setdefault(chunk_id, {**row, "rrf_score": 0.0})
            current.update({key: value for key, value in row.items() if value is not None})
            current[rank_key] = rank
            current["rrf_score"] += 1.0 / (rrf_k + rank)
    return sorted(
        fused.values(),
        key=lambda row: (
            -row["rrf_score"],
            min(row.get("dense_rank") or 10**9, row.get("bm25_rank") or 10**9),
            row["chunk_id"],
        ),
    )[:top_k]
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_fusion.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/services/fusion.py tests/test_fusion.py
git commit -m "feat: fuse dense and sparse ranks with RRF"
```

### Task 5: Retrieval modes and Reranker integration

**Files:**
- Modify: `backend/services/retriever.py`
- Create: `tests/test_hybrid_retriever.py`
- Modify: `tests/test_retrieval_threshold.py`
- Modify: `tests/test_reranker.py`

- [ ] **Step 1: Add failing mode tests**

```python
def test_hybrid_mode_fuses_before_reranking(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 15)
    monkeypatch.setattr(retriever, "vector_search", lambda q, top_k: [_dense("d1", 0.2)])
    monkeypatch.setattr(retriever, "bm25_search", lambda q, top_k: [_sparse("s1", 2.0)])
    monkeypatch.setattr(
        retriever,
        "reciprocal_rank_fusion",
        lambda dense, sparse, rrf_k, top_k: calls.append((dense, sparse, top_k)) or dense + sparse,
    )
    monkeypatch.setattr(retriever, "rerank", lambda q, rows: list(reversed(rows)))
    rows = retriever.retrieve("测试", top_k=2)
    assert calls and calls[0][2] == 15
    assert [row["chunk_id"] for row in rows] == ["s1", "d1"]


def test_bm25_mode_does_not_call_dense(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(retriever, "vector_search", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(retriever, "bm25_search", lambda q, top_k: [_sparse("s1", 1.0)])
    assert retriever.retrieve("Qdrant", 5)[0]["chunk_id"] == "s1"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_retriever.py -q -p no:cacheprovider`

Expected: FAIL because mode dispatch and BM25/RRF imports do not exist.

- [ ] **Step 3: Refactor retriever into candidate generation and common post-processing**

Replace the single Dense path with these helpers and keep the existing document-cap loop unchanged:

```python
def _candidate_k(top_k: int) -> int:
    if settings.RERANKER_ENABLED:
        return max(top_k, settings.RERANKER_CANDIDATE_K)
    return top_k * 2


def _dense_candidates(question: str, candidate_k: int) -> list[dict]:
    return [
        {**row, "dense_rank": rank}
        for rank, row in enumerate(vector_search(question, top_k=candidate_k), 1)
        if row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD
    ]


def _bm25_candidates(question: str, candidate_k: int) -> list[dict]:
    return bm25_search(
        question,
        top_k=max(candidate_k, settings.BM25_TOP_K),
    )[:candidate_k]


def _generate_candidates(question: str, candidate_k: int) -> list[dict]:
    if settings.RETRIEVAL_MODE == "dense":
        return _dense_candidates(question, candidate_k)
    if settings.RETRIEVAL_MODE == "bm25":
        return _bm25_candidates(question, candidate_k)
    dense = _dense_candidates(question, candidate_k)
    sparse = _bm25_candidates(question, candidate_k)
    return reciprocal_rank_fusion(
        dense,
        sparse,
        rrf_k=settings.RRF_K,
        top_k=candidate_k,
    )


def retrieve(question: str, top_k: int = None) -> list[dict]:
    top_k = top_k or settings.RETRIEVAL_TOP_K
    candidates = _generate_candidates(question, _candidate_k(top_k))
    ranked = rerank(question, candidates) if settings.RERANKER_ENABLED else candidates
    doc_counts: dict[str, int] = {}
    deduped = []
    for row in ranked:
        metadata = row["metadata"]
        document_key = metadata.get("document_id") or metadata.get(
            "document_name", "unknown"
        )
        if doc_counts.get(document_key, 0) < 3:
            deduped.append(row)
            doc_counts[document_key] = doc_counts.get(document_key, 0) + 1
    return deduped[:top_k]
```

- [ ] **Step 4: Run focused regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_retriever.py tests\test_retrieval_threshold.py tests\test_reranker.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/services/retriever.py tests/test_hybrid_retriever.py tests/test_retrieval_threshold.py tests/test_reranker.py
git commit -m "feat: add dense bm25 and hybrid retrieval modes"
```

### Task 6: Dataset v2 and sparse-safe reports

**Files:**
- Create: `eval/dataset/questions_v2.json`
- Modify: `eval/report.py`
- Modify: `eval/retrieval_sweep.py`
- Modify: `tests/test_eval_report.py`
- Modify: `tests/test_retrieval_sweep.py`

- [ ] **Step 1: Add failing report tests for Sparse-only results**

```python
def test_format_retrieval_debug_handles_sparse_only_result():
    from eval.report import _format_retrieval_debug
    row = {
        "chunk_id": "c1",
        "content": "RBAC 访问控制",
        "metadata": {"document_name": "doc.pdf", "page_number": 7},
        "distance": None,
        "bm25_score": 2.3,
        "bm25_rank": 1,
    }
    text = _format_retrieval_debug(row)
    assert "bm25=2.300" in text
    assert "dist=—" in text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_eval_report.py tests\test_retrieval_sweep.py -q -p no:cacheprovider`

Expected: FAIL because current formatting applies `:.3f` to `None`.

- [ ] **Step 3: Make reports tolerate optional scores**

Add and use this helper instead of direct `distance:.3f` formatting:

```python
def _format_retrieval_debug(row: dict) -> str:
    metadata = row["metadata"]
    distance = row.get("distance")
    scores = [f"dist={distance:.3f}" if distance is not None else "dist=—"]
    for key, label in (
        ("bm25_score", "bm25"),
        ("rrf_score", "rrf"),
        ("reranker_score", "reranker"),
    ):
        if row.get(key) is not None:
            scores.append(f"{label}={row[key]:.3f}")
    return (
        f"《{metadata.get('document_name')}》第{metadata.get('page_number')}页"
        f"({', '.join(scores)})"
    )
```

In `eval/retrieval_sweep.py`, use `retrieval[0].get("distance")` for top-1 distance.

- [ ] **Step 4: Add dataset v2**

Copy q01-q21 unchanged, set version to 2, and append these objects:

```json
[
  {
    "id": "q22",
    "question": "报告列举了哪些企业工作流自动化中的 Agent 框架？",
    "category": "lexical",
    "answerable": true,
    "evidence": [{"document_name": "2024技术发展报告_RAG测试文档.docx", "snippet": "Claude Code、AutoGPT、CrewAI等", "page_hint": 1}],
    "keypoints": [["Claude Code"], ["AutoGPT"], ["CrewAI"]],
    "reference_answer": "报告列举了 Claude Code、AutoGPT 和 CrewAI。"
  },
  {
    "id": "q23",
    "question": "APE 是哪一种 Prompt Engineering 技术，它的作用是什么？",
    "category": "lexical",
    "answerable": true,
    "evidence": [{"document_name": "LLM应用指南_RAG测试文档.pdf", "snippet": "自动提示工程（APE）让模型自行优化 prompt", "page_hint": 5}],
    "keypoints": [["自动提示工程", "APE"], ["自行优化", "自动优化"]],
    "reference_answer": "APE 是自动提示工程，让模型自行优化 prompt。"
  },
  {
    "id": "q24",
    "question": "哪些向量数据库适合本地轻量开发，哪个适合大规模生产部署？",
    "category": "lexical",
    "answerable": true,
    "evidence": [
      {"document_name": "LLM应用指南_RAG测试文档.pdf", "snippet": "Chroma 和 Qdrant 适合本地轻量开发", "page_hint": 6},
      {"document_name": "LLM应用指南_RAG测试文档.pdf", "snippet": "Milvus 适合大规模生产部署", "page_hint": 6}
    ],
    "keypoints": [["Chroma"], ["Qdrant"], ["Milvus"]],
    "reference_answer": "Chroma 和 Qdrant 适合本地轻量开发，Milvus 适合大规模生产部署。"
  },
  {
    "id": "q25",
    "question": "企业知识库问答系统用什么缩写表示的机制限制敏感文档检索范围？",
    "category": "lexical",
    "answerable": true,
    "evidence": [{"document_name": "LLM应用指南_RAG测试文档.pdf", "snippet": "基于角色的访问控制（RBAC）", "page_hint": 7}],
    "keypoints": [["RBAC"], ["基于角色", "角色访问控制"]],
    "reference_answer": "使用基于角色的访问控制（RBAC）限制敏感文档检索范围。"
  },
  {
    "id": "q26",
    "question": "企业知识库问答系统可以减少员工多少比例的内部信息查找时间？",
    "category": "lexical",
    "answerable": true,
    "evidence": [{"document_name": "LLM应用指南_RAG测试文档.pdf", "snippet": "减少员工 30-50% 的内部信息查找时间", "page_hint": 7}],
    "keypoints": [["30-50", "30到50", "30至50"]],
    "reference_answer": "可以减少员工 30-50% 的内部信息查找时间。"
  },
  {
    "id": "q27",
    "question": "Qwen 2.5 在 MMLU 基准测试上的具体得分是多少？",
    "category": "unanswerable",
    "answerable": false,
    "evidence": [],
    "keypoints": [],
    "reference_answer": "语料只说 Qwen 2.5 在多项基准测试中接近或超过闭源模型，没有提供 MMLU 具体得分，应拒答。"
  }
]
```

- [ ] **Step 5: Validate dataset and run tests**

Create `tests/test_hybrid_dataset.py` with:

```python
import json
from pathlib import Path

from eval import adapters
from eval.eval import _validate_dataset


def test_questions_v2_snippets_are_unique_in_parsed_corpus():
    dataset = json.loads(Path("eval/dataset/questions_v2.json").read_text("utf-8"))
    assert dataset["version"] == 2
    assert [row["id"] for row in dataset["questions"][-6:]] == [
        "q22", "q23", "q24", "q25", "q26", "q27"
    ]
    _validate_dataset(dataset, adapters.parse_corpus(Path("eval/corpus")))
```

Then run:

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_eval_report.py tests\test_retrieval_sweep.py tests\test_eval_cli.py tests\test_hybrid_dataset.py -q -p no:cacheprovider`

Expected: PASS and dataset snippet validation succeeds without calling the LLM.

- [ ] **Step 6: Commit**

```powershell
git add eval/dataset/questions_v2.json eval/report.py eval/retrieval_sweep.py tests/test_eval_report.py tests/test_retrieval_sweep.py tests/test_hybrid_dataset.py
git commit -m "test: add lexical hybrid retrieval benchmark"
```

### Task 7: Five-way hybrid ablation command

**Files:**
- Create: `eval/hybrid_ablation.py`
- Modify: `eval/eval.py`
- Modify: `eval/adapters.py`
- Create: `tests/test_hybrid_ablation.py`
- Modify: `eval/README.md`

- [ ] **Step 1: Add failing aggregation and CLI state-restoration tests**

Create tests around this public configuration list and command behavior:

```python
from eval.hybrid_ablation import ABLATION_CONFIGS, build_hybrid_ablation


def test_ablation_configs_include_required_four_and_deployment_control():
    assert ABLATION_CONFIGS == [
        ("dense", "dense", False),
        ("bm25", "bm25", False),
        ("hybrid", "hybrid", False),
        ("hybrid_reranker", "hybrid", True),
        ("dense_reranker", "dense", True),
    ]


def test_ablation_compares_hybrid_reranker_to_dense_reranker():
    payload = build_hybrid_ablation(
        label="unit",
        config={},
        ingest={"total_chunks": 1, "ingest_s": 0.1},
        results=[_mode("dense_reranker", hit_rate=0.5), _mode("hybrid_reranker", hit_rate=1.0)],
        dataset_version=2,
    )
    assert payload["deployment_comparison"]["hit_rate_delta"] == 0.5
```

Add a CLI test that monkeypatches `adapters.ingest_corpus` and `retrieve_only`, asserts one ingest and 27 retrieval calls per mode, and verifies `settings.RETRIEVAL_MODE` plus `settings.RERANKER_ENABLED` are restored in `finally`.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_ablation.py -q -p no:cacheprovider`

Expected: import/CLI failure because the command does not exist.

- [ ] **Step 3: Implement ablation aggregation and renderer**

Define the configuration list and adapt the existing candidate evaluator without duplicating gold matching:

```python
from eval.retrieval_sweep import build_candidate_result

ABLATION_CONFIGS = [
    ("dense", "dense", False),
    ("bm25", "bm25", False),
    ("hybrid", "hybrid", False),
    ("hybrid_reranker", "hybrid", True),
    ("dense_reranker", "dense", True),
]


def build_mode_result(name, questions, retrievals, timings_s):
    result = build_candidate_result(
        candidate_k=0,
        questions=questions,
        retrievals=retrievals,
        timings_s=timings_s,
    )
    result.pop("candidate_k", None)
    result["name"] = name
    result["original_21"] = _aggregate_rows(result["per_question"][:21])
    result["lexical"] = _aggregate_rows(
        [row for row in result["per_question"] if row["category"] == "lexical"]
    )
    return result
```

Expose `_aggregate_rows` from `eval/retrieval_sweep.py` as `aggregate_rows`, then calculate deltas by name from `dense_reranker` to `hybrid_reranker`. Render one Markdown row per mode plus improved/regressed IDs.

- [ ] **Step 4: Implement CLI with `try/finally` configuration restoration**

Add:

```text
python -m eval.eval hybrid-ablation \
  --label p2-1-hybrid-ablation \
  --dataset eval/dataset/questions_v2.json
```

Implement the command with this state lifecycle:

```python
original_mode = settings.RETRIEVAL_MODE
original_reranker = settings.RERANKER_ENABLED
try:
    results = []
    for name, mode, reranker_enabled in ABLATION_CONFIGS:
        settings.RETRIEVAL_MODE = mode
        settings.RERANKER_ENABLED = reranker_enabled
        retrievals, timings = {}, []
        with open(run_dir / f"retrieval-{name}.jsonl", "w", encoding="utf-8") as raw:
            for question in questions:
                item = adapters.retrieve_only(question["question"])
                retrievals[question["id"]] = item["retrieval"]
                timings.append(item["retrieval_s"])
                raw.write(json.dumps({"id": question["id"], **item}, ensure_ascii=False) + "\n")
        results.append(build_mode_result(name, questions, retrievals, timings))
    payload = build_hybrid_ablation(
        label=args.label,
        config=adapters.get_run_config(),
        ingest=ingest_info,
        results=results,
        dataset_version=dataset["version"],
    )
    (run_dir / "hybrid-ablation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "hybrid-ablation.md").write_text(
        render_hybrid_ablation(payload), encoding="utf-8"
    )
finally:
    settings.RETRIEVAL_MODE = original_mode
    settings.RERANKER_ENABLED = original_reranker
```

- [ ] **Step 5: Run tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_ablation.py tests\test_retrieval_sweep.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add eval/hybrid_ablation.py eval/eval.py eval/adapters.py tests/test_hybrid_ablation.py eval/README.md
git commit -m "feat: add hybrid retrieval ablation evaluation"
```

### Task 8: Real ablation, deployment decision, documentation and verification

**Files:**
- Conditionally modify: `.env`
- Conditionally modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/交接_阶段一优化.md`
- Create: `eval/results/p2-1-hybrid-retrieval/` selected reports

- [ ] **Step 1: Run full local verification before experiments**

Run: `$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`

Expected: all non-external tests pass.

- [ ] **Step 2: Run five-way offline ablation**

Run:

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m eval.eval hybrid-ablation `
  --label p2-1-hybrid-ablation `
  --dataset eval/dataset/questions_v2.json
```

Inspect overall metrics, original 21 regression, new lexical questions, q27, multi-document Recall and P95 latency.

- [ ] **Step 3: Apply the deployment gate**

If `hybrid_reranker` improves at least one of Hit Rate/Recall/MRR over `dense_reranker`, has no material q01-q21 regression, and does not reduce unanswerable correctness, set `RETRIEVAL_MODE=hybrid` in `.env` and `.env.example`. Otherwise keep `dense` and explicitly record the negative result.

- [ ] **Step 4: Run same-dataset real DeepSeek evaluation only for required controls**

Run `dense_reranker` and `hybrid_reranker` on dataset v2 when offline retrieval passes the gate. Compare reports and run production HTTP smoke for one improved lexical question plus q18/q27. If the offline gate fails, do not spend API calls merely to force a positive conclusion.

- [ ] **Step 5: Persist reports and update docs**

Copy machine-readable reports, Markdown summaries and raw JSONL files into `eval/results/p2-1-hybrid-retrieval/`. Update README and handoff with actual values, improved/regressed questions, latency, deployment decision and next step. Do not write target placeholders.

- [ ] **Step 6: Run fresh final verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q -p no:cacheprovider
git diff --check
```

Expected: dependency check clean, full tests pass, CI subset passes, diff check clean.

- [ ] **Step 7: Commit completed P2-1 implementation**

Stage only P2-1 code, tests, docs and selected formal reports. Never stage `.env`, `eval/runs/`, model cache, Chroma or API credentials.

```powershell
git commit -m "feat: evaluate and deploy P2-1 hybrid retrieval"
```
