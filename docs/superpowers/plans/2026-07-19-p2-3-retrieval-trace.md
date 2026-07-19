# P2-3 Retrieval Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-gated, default-collapsed Streamlit Trace panel that explains every executed retrieval stage, full deduplicated candidate chunks, context size, and end-to-end timing without changing retrieval quality or the existing answer/citation contract.

**Architecture:** Keep `retrieve()` backward compatible and add `retrieve_with_trace()` around a shared internal pipeline. A focused `retrieval_trace.py` collector snapshots stage metadata and deduplicates full chunk content; the chat route completes context/LLM timings, while a separate `frontend/trace_view.py` renders dynamic tabs and row-selected full text. `RETRIEVAL_TRACE_ENABLED=false` is the secure default and prevents candidate content from entering the API response.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, ChromaDB, Streamlit 1.59.1, pytest, Streamlit AppTest, httpx.

---

## File map

- Modify `backend/config.py`: secure Trace feature flag.
- Modify `.env.example`: document the disabled-by-default flag and leakage warning.
- Modify `tests/conftest.py`: force Trace off in the general test environment.
- Modify `backend/models/schemas.py`: API/internal Trace contracts.
- Create `backend/services/retrieval_trace.py`: stage snapshots, chunk catalog, timing and context finalization.
- Modify `backend/services/vector_store.py`: query by a precomputed embedding while preserving `search(query)`.
- Modify `backend/services/retriever.py`: shared retrieval pipeline and optional trace capture.
- Modify `backend/routers/chat.py`: expose Trace and complete context/LLM timing.
- Create `frontend/trace_view.py`: pure row shaping plus Streamlit Trace rendering.
- Modify `frontend/app.py`: persist and render Trace for new and historical assistant messages.
- Create `tests/test_retrieval_trace.py`: config, contracts, collector and retriever behavior.
- Create `tests/test_vector_store_trace.py`: precomputed embedding search contract.
- Modify `tests/test_api.py`: ChatResponse compatibility and Trace API behavior.
- Modify `tests/test_frontend_app.py`: default-collapsed Trace panel and history persistence.
- Modify `README.md`, `eval/README.md`, `docs/交接_阶段一优化.md`: configuration, P2-2 skip and P2-3 evidence.
- Create `eval/results/p2-3-retrieval-trace/validation.md`: measured real-environment evidence.
- Create `eval/results/p2-3-retrieval-trace/trace-response.json`: real API response with the full Trace.
- Create `eval/results/p2-3-retrieval-trace/streamlit-trace.png`: browser evidence of the expanded panel.

---

### Task 1: Add the secure configuration and typed Trace contract

**Files:**
- Modify: `backend/config.py`
- Modify: `.env.example`
- Modify: `tests/conftest.py`
- Modify: `backend/models/schemas.py`
- Create: `tests/test_retrieval_trace.py`

- [ ] **Step 1: Write failing configuration and schema tests**

Add the following contract tests to `tests/test_retrieval_trace.py`:

```python
import pytest

from backend.config import _env_bool, settings
from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalResponse,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)


def test_retrieval_trace_is_disabled_in_tests():
    assert settings.RETRIEVAL_TRACE_ENABLED is False


def test_env_bool_rejects_invalid_trace_value(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_TRACE_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="RETRIEVAL_TRACE_ENABLED"):
        _env_bool("RETRIEVAL_TRACE_ENABLED")


def test_retrieval_trace_contract_keeps_full_chunk_once():
    trace = RetrievalTrace(
        retrieval_mode="dense",
        reranker_enabled=True,
        chunks={
            "c1": RetrievalTraceChunk(
                chunk_id="c1",
                document_name="doc.txt",
                page_number=1,
                content="完整 chunk 原文",
            )
        },
        stages=[
            RetrievalTraceStage(
                name="dense",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        distance=0.2,
                        eligible=True,
                        selected=True,
                    )
                ],
            )
        ],
        selected_chunk_ids=["c1"],
        context=RetrievalContextStats(),
        timings_ms=RetrievalTraceTimings(),
    )
    response = RetrievalResponse(results=[], trace=trace)

    assert response.trace.chunks["c1"].content == "完整 chunk 原文"
    assert response.trace.stages[0].candidates[0].distance == 0.2
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py -q -p no:cacheprovider
```

Expected: collection fails because the Retrieval Trace models and setting do not exist.

- [ ] **Step 3: Add the flag and Pydantic models**

Add to `Settings` in `backend/config.py`:

```python
RETRIEVAL_TRACE_ENABLED: bool = _env_bool(
    "RETRIEVAL_TRACE_ENABLED", False
)
```

Add to `.env.example` directly below the retrieval settings:

```dotenv
# P2-3 调试面板；开启后 API 会返回未入选候选的完整 chunk，仅限可信环境
RETRIEVAL_TRACE_ENABLED=false
```

Add before any backend import in `tests/conftest.py`:

```python
os.environ["RETRIEVAL_TRACE_ENABLED"] = "false"
```

Add these models to `backend/models/schemas.py` and add `trace` to `ChatResponse`:

```python
class RetrievalTraceChunk(BaseModel):
    chunk_id: str
    document_name: str
    page_number: int
    content: str


class RetrievalTraceCandidate(BaseModel):
    chunk_id: str
    rank: int = Field(..., ge=1)
    distance: Optional[float] = None
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    bm25_score: Optional[float] = None
    rrf_score: Optional[float] = None
    reranker_score: Optional[float] = None
    eligible: bool = True
    filtered_reason: Optional[str] = None
    selected: bool = False


class RetrievalTraceStage(BaseModel):
    name: str
    candidates: list[RetrievalTraceCandidate] = Field(default_factory=list)


class RetrievalContextStats(BaseModel):
    chunk_count: int = 0
    char_count: int = 0
    token_count: int = 0


class RetrievalTraceTimings(BaseModel):
    query_embedding: float = 0.0
    dense_retrieval: float = 0.0
    sparse_retrieval: float = 0.0
    rrf: float = 0.0
    rerank: float = 0.0
    context_formatting: float = 0.0
    llm_generation: float = 0.0
    total: float = 0.0


class RetrievalTrace(BaseModel):
    retrieval_mode: str
    reranker_enabled: bool
    chunks: dict[str, RetrievalTraceChunk] = Field(default_factory=dict)
    stages: list[RetrievalTraceStage] = Field(default_factory=list)
    selected_chunk_ids: list[str] = Field(default_factory=list)
    context: RetrievalContextStats = Field(default_factory=RetrievalContextStats)
    timings_ms: RetrievalTraceTimings = Field(default_factory=RetrievalTraceTimings)


class RetrievalResponse(BaseModel):
    results: list[dict] = Field(default_factory=list)
    trace: Optional[RetrievalTrace] = None


class ChatResponse(BaseModel):
    answer: str = Field(..., description="LLM 生成的回答文本")
    citations: list[Citation] = Field(default_factory=list, description="引用来源列表")
    trace: Optional[RetrievalTrace] = Field(
        default=None,
        description="服务端允许时返回的检索调试链路",
    )
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command again. Expected: all tests in `test_retrieval_trace.py` pass.

- [ ] **Step 5: Commit the contract**

```powershell
git add backend/config.py backend/models/schemas.py tests/conftest.py tests/test_retrieval_trace.py .env.example
git commit -m "feat: add retrieval trace contracts"
```

---

### Task 2: Separate query embedding from Chroma retrieval

**Files:**
- Modify: `backend/services/vector_store.py`
- Create: `tests/test_vector_store_trace.py`

- [ ] **Step 1: Write the failing precomputed-embedding test**

Create `tests/test_vector_store_trace.py`:

```python
from backend.services import vector_store


def test_search_by_embedding_skips_query_encoding(monkeypatch):
    captured = {}

    class FakeCollection:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "ids": [["c1"]],
                "documents": [["正文"]],
                "metadatas": [[{"document_name": "doc.txt", "page_number": 1}]],
                "distances": [[0.25]],
            }

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    monkeypatch.setattr(
        vector_store,
        "embed_query",
        lambda query: (_ for _ in ()).throw(AssertionError("不应重复编码")),
    )

    rows = vector_store.search_by_embedding([0.1, 0.2], top_k=3)

    assert captured["query_embeddings"] == [[0.1, 0.2]]
    assert captured["n_results"] == 3
    assert rows[0]["chunk_id"] == "c1"
    assert rows[0]["distance"] == 0.25


def test_search_remains_a_compatible_wrapper(monkeypatch):
    monkeypatch.setattr(vector_store, "embed_query", lambda query: [0.3])
    monkeypatch.setattr(
        vector_store,
        "search_by_embedding",
        lambda embedding, top_k: [{"embedding": embedding, "top_k": top_k}],
    )

    assert vector_store.search("问题", top_k=4) == [
        {"embedding": [0.3], "top_k": 4}
    ]
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vector_store_trace.py -q -p no:cacheprovider
```

Expected: failure because `search_by_embedding` is missing.

- [ ] **Step 3: Extract the precomputed-vector query**

Implement in `backend/services/vector_store.py`:

```python
def search_by_embedding(query_embedding: List[float], top_k: int = 5) -> List[dict]:
    if top_k <= 0:
        return []
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]
    return [
        {
            "chunk_id": chunk_id,
            "content": document,
            "metadata": metadata,
            "distance": distance,
        }
        for chunk_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        )
    ]


def search(query: str, top_k: int = 5) -> List[dict]:
    if top_k <= 0:
        return []
    return search_by_embedding(embed_query(query), top_k=top_k)
```

- [ ] **Step 4: Run focused and existing vector tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vector_store_trace.py tests\test_vector_store.py -q -p no:cacheprovider
```

Expected: new tests pass; integration-marked vector tests pass or remain skipped according to the active marker selection.

- [ ] **Step 5: Commit the storage seam**

```powershell
git add backend/services/vector_store.py tests/test_vector_store_trace.py
git commit -m "refactor: separate query embedding from vector search"
```

---

### Task 3: Build the side-effect-free Trace collector

**Files:**
- Create: `backend/services/retrieval_trace.py`
- Modify: `tests/test_retrieval_trace.py`

- [ ] **Step 1: Add failing collector tests**

Append to `tests/test_retrieval_trace.py`:

```python
from backend.services.retrieval_trace import TraceCollector, finalize_chat_trace


def _row(chunk_id, content="完整正文", distance=0.2):
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {"document_name": "doc.txt", "page_number": 1},
        "distance": distance,
        "dense_rank": 1,
    }


def test_collector_deduplicates_full_chunk_across_stages():
    collector = TraceCollector(enabled=True, mode="dense", reranker_enabled=True)
    collector.add_stage("dense", [_row("c1")])
    collector.add_stage("reranker", [{**_row("c1"), "reranker_score": 2.5}])
    collector.add_stage("selected", [_row("c1")])

    trace = collector.finish([_row("c1")])

    assert list(trace.chunks) == ["c1"]
    assert [stage.name for stage in trace.stages] == [
        "dense", "reranker", "selected"
    ]
    assert all(stage.candidates[0].selected for stage in trace.stages)


def test_collector_records_dense_threshold_rejection_without_mutating_row():
    row = _row("filtered", distance=0.8)
    collector = TraceCollector(enabled=True, mode="dense", reranker_enabled=False)
    collector.add_stage(
        "dense",
        [row],
        eligibility={"filtered": (False, "distance_above_threshold")},
    )

    trace = collector.finish([])

    candidate = trace.stages[0].candidates[0]
    assert candidate.eligible is False
    assert candidate.filtered_reason == "distance_above_threshold"
    assert "eligible" not in row


def test_disabled_collector_never_retains_chunk_content():
    collector = TraceCollector(enabled=False, mode="dense", reranker_enabled=False)
    collector.add_stage("dense", [_row("c1")])
    assert collector.finish([_row("c1")]) is None
    assert collector.chunks == {}


def test_finalize_chat_trace_records_context_and_llm_timing(monkeypatch):
    collector = TraceCollector(enabled=True, mode="dense", reranker_enabled=False)
    trace = collector.finish([])
    monkeypatch.setattr(
        "backend.services.retrieval_trace.count_tokens", lambda text: 9
    )

    completed = finalize_chat_trace(
        trace,
        context="上下文",
        context_formatting_ms=1.2,
        llm_generation_ms=3.4,
        total_ms=5.6,
    )

    assert completed.context.char_count == 3
    assert completed.context.token_count == 9
    assert completed.timings_ms.llm_generation == 3.4
    assert completed.timings_ms.total == 5.6
```

- [ ] **Step 2: Run and verify RED**

Run the Task 1 focused command. Expected: import failure for `retrieval_trace`.

- [ ] **Step 3: Implement the collector**

Create `backend/services/retrieval_trace.py` with this public interface:

```python
from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
from backend.services.embedding import count_tokens


TIMING_FIELDS = (
    "query_embedding", "dense_retrieval", "sparse_retrieval", "rrf",
    "rerank", "context_formatting", "llm_generation", "total",
)


class TraceCollector:
    def __init__(self, *, enabled: bool, mode: str, reranker_enabled: bool):
        self.enabled = enabled
        self.mode = mode
        self.reranker_enabled = reranker_enabled
        self.chunks = {}
        self.stages = []
        self.timings = {field: 0.0 for field in TIMING_FIELDS}

    def set_timing(self, name: str, elapsed_seconds: float) -> None:
        if self.enabled:
            self.timings[name] = round(elapsed_seconds * 1000, 3)

    def add_stage(self, name: str, rows: list[dict], eligibility=None) -> None:
        if not self.enabled:
            return
        eligibility = eligibility or {}
        candidates = []
        for rank, row in enumerate(rows, 1):
            chunk_id = row["chunk_id"]
            metadata = row.get("metadata") or {}
            self.chunks.setdefault(
                chunk_id,
                RetrievalTraceChunk(
                    chunk_id=chunk_id,
                    document_name=metadata.get("document_name", "unknown"),
                    page_number=int(metadata.get("page_number") or 0),
                    content=row.get("content", ""),
                ),
            )
            eligible, reason = eligibility.get(chunk_id, (True, None))
            candidates.append(
                RetrievalTraceCandidate(
                    chunk_id=chunk_id,
                    rank=rank,
                    distance=row.get("distance"),
                    dense_rank=row.get("dense_rank"),
                    bm25_rank=row.get("bm25_rank"),
                    bm25_score=row.get("bm25_score"),
                    rrf_score=row.get("rrf_score"),
                    reranker_score=row.get("reranker_score"),
                    eligible=eligible,
                    filtered_reason=reason,
                )
            )
        self.stages.append(RetrievalTraceStage(name=name, candidates=candidates))

    def finish(self, selected_rows: list[dict]):
        if not self.enabled:
            return None
        selected_ids = [row["chunk_id"] for row in selected_rows]
        selected = set(selected_ids)
        stages = [
            stage.model_copy(
                update={
                    "candidates": [
                        candidate.model_copy(
                            update={"selected": candidate.chunk_id in selected}
                        )
                        for candidate in stage.candidates
                    ]
                }
            )
            for stage in self.stages
        ]
        return RetrievalTrace(
            retrieval_mode=self.mode,
            reranker_enabled=self.reranker_enabled,
            chunks=self.chunks,
            stages=stages,
            selected_chunk_ids=selected_ids,
            context=RetrievalContextStats(chunk_count=len(selected_ids)),
            timings_ms=RetrievalTraceTimings(**self.timings),
        )


def finalize_chat_trace(
    trace,
    *,
    context: str,
    context_formatting_ms: float,
    llm_generation_ms: float,
    total_ms: float,
):
    if trace is None:
        return None
    trace.context = RetrievalContextStats(
        chunk_count=len(trace.selected_chunk_ids),
        char_count=len(context),
        token_count=count_tokens(context) if context else 0,
    )
    trace.timings_ms.context_formatting = round(context_formatting_ms, 3)
    trace.timings_ms.llm_generation = round(llm_generation_ms, 3)
    trace.timings_ms.total = round(total_ms, 3)
    return trace
```

- [ ] **Step 4: Run collector tests and verify GREEN**

Run the Task 1 focused command. Expected: all collector/config/schema tests pass.

- [ ] **Step 5: Commit the collector**

```powershell
git add backend/services/retrieval_trace.py tests/test_retrieval_trace.py
git commit -m "feat: capture retrieval trace stages"
```

---

### Task 4: Instrument Dense, BM25, RRF and Reranker without changing `retrieve()`

**Files:**
- Modify: `backend/services/retriever.py`
- Modify: `tests/test_retrieval_trace.py`
- Verify: `tests/test_hybrid_retriever.py`
- Verify: `tests/test_reranker.py`
- Verify: `tests/test_retrieval_threshold.py`

- [ ] **Step 1: Add failing pipeline tests**

Append tests that patch `embed_query`, `vector_search_by_embedding`, `bm25_search`, fusion and rerank,
then assert:

```python
def test_hybrid_trace_has_only_executed_stages_and_full_chunks(monkeypatch):
    from backend.config import settings
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 2)
    monkeypatch.setattr(settings, "BM25_TOP_K", 2)
    monkeypatch.setattr(settings, "RETRIEVAL_TOP_K", 1)
    monkeypatch.setattr(retriever, "embed_query", lambda question: [0.1])
    monkeypatch.setattr(
        retriever,
        "vector_search_by_embedding",
        lambda vector, top_k: [_row("shared"), _row("filtered", distance=0.8)],
    )
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k: [
            {**_row("shared"), "bm25_rank": 1, "bm25_score": 5.0}
        ],
    )
    monkeypatch.setattr(
        retriever,
        "reciprocal_rank_fusion",
        lambda dense, sparse, rrf_k, top_k: [
            {**dense[0], "bm25_rank": 1, "bm25_score": 5.0, "rrf_score": 0.03}
        ],
    )
    monkeypatch.setattr(
        retriever,
        "rerank",
        lambda question, rows: [{**rows[0], "reranker_score": 2.0}],
    )

    response = retriever.retrieve_with_trace("问题", top_k=1)

    assert [stage.name for stage in response.trace.stages] == [
        "dense", "bm25", "rrf", "reranker", "selected"
    ]
    assert set(response.trace.chunks) == {"shared", "filtered"}
    dense = response.trace.stages[0].candidates
    assert dense[1].eligible is False
    assert response.trace.selected_chunk_ids == ["shared"]


def test_plain_retrieve_never_builds_trace_even_when_server_flag_is_on(monkeypatch):
    from backend.config import settings
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "dense")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(retriever, "vector_search", lambda question, top_k: [_row("c1")])

    assert [row["chunk_id"] for row in retriever.retrieve("问题", top_k=1)] == ["c1"]


def test_bm25_trace_omits_dense_and_rrf_stages(monkeypatch):
    from backend.config import settings
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k: [{**_row("c1"), "bm25_rank": 1, "bm25_score": 2.0}],
    )

    response = retriever.retrieve_with_trace("问题", top_k=1)
    assert [stage.name for stage in response.trace.stages] == ["bm25", "selected"]
    assert response.trace.timings_ms.query_embedding == 0.0
```

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_hybrid_retriever.py -q -p no:cacheprovider
```

Expected: failures because `retrieve_with_trace` and the instrumented imports do not exist.

- [ ] **Step 3: Refactor into one internal retrieval pipeline**

In `backend/services/retriever.py`:

- import `perf_counter`, `embed_query`, `search_by_embedding as vector_search_by_embedding`,
  `RetrievalResponse`, and `TraceCollector`;
- replace candidate generation and document limiting with these functions:

```python
def _dense_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    if collector.enabled:
        started = perf_counter()
        query_embedding = embed_query(question)
        collector.set_timing("query_embedding", perf_counter() - started)
        started = perf_counter()
        raw = vector_search_by_embedding(query_embedding, top_k=candidate_k)
        collector.set_timing("dense_retrieval", perf_counter() - started)
    else:
        raw = vector_search(question, top_k=candidate_k)

    ranked = [
        {**row, "dense_rank": rank}
        for rank, row in enumerate(raw, 1)
    ]
    eligibility = {
        row["chunk_id"]: (
            row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD,
            None if row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD
            else "distance_above_threshold",
        )
        for row in ranked
    }
    collector.add_stage("dense", ranked, eligibility=eligibility)
    return [row for row in ranked if eligibility[row["chunk_id"]][0]]


def _bm25_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    started = perf_counter()
    rows = bm25_search(
        question,
        top_k=max(candidate_k, settings.BM25_TOP_K),
    )[:candidate_k]
    collector.set_timing("sparse_retrieval", perf_counter() - started)
    collector.add_stage("bm25", rows)
    return rows


def _generate_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    if settings.RETRIEVAL_MODE == "dense":
        return _dense_candidates(question, candidate_k, collector)
    if settings.RETRIEVAL_MODE == "bm25":
        return _bm25_candidates(question, candidate_k, collector)

    dense = _dense_candidates(question, candidate_k, collector)
    sparse = _bm25_candidates(question, candidate_k, collector)
    started = perf_counter()
    fused = reciprocal_rank_fusion(
        dense,
        sparse,
        rrf_k=settings.RRF_K,
        top_k=candidate_k,
    )
    collector.set_timing("rrf", perf_counter() - started)
    collector.add_stage("rrf", fused)
    return fused


def _limit_per_document(rows: List[dict], top_k: int) -> List[dict]:
    max_per_document = 3
    document_counts = {}
    selected = []
    for row in rows:
        metadata = row["metadata"]
        document_key = metadata.get("document_id") or metadata.get(
            "document_name", "unknown"
        )
        if document_counts.get(document_key, 0) < max_per_document:
            selected.append(row)
            document_counts[document_key] = document_counts.get(document_key, 0) + 1
    return selected[:top_k]
```

- expose these wrappers exactly:

```python
def _retrieve_internal(question: str, top_k: int, include_trace: bool) -> RetrievalResponse:
    collector = TraceCollector(
        enabled=include_trace,
        mode=settings.RETRIEVAL_MODE,
        reranker_enabled=settings.RERANKER_ENABLED,
    )
    candidate_k = _candidate_k(top_k)
    candidates = _generate_candidates(question, candidate_k, collector)
    if settings.RERANKER_ENABLED:
        started = perf_counter()
        ranked = rerank(question, candidates)
        collector.set_timing("rerank", perf_counter() - started)
        collector.add_stage("reranker", ranked)
    else:
        ranked = candidates

    selected = _limit_per_document(ranked, top_k=top_k)
    collector.add_stage("selected", selected)
    return RetrievalResponse(results=selected, trace=collector.finish(selected))


def retrieve(question: str, top_k: int = None) -> List[dict]:
    effective_top_k = (
        settings.RETRIEVAL_TOP_K if top_k is None else top_k
    )
    return _retrieve_internal(question, effective_top_k, include_trace=False).results


def retrieve_with_trace(question: str, top_k: int = None) -> RetrievalResponse:
    effective_top_k = (
        settings.RETRIEVAL_TOP_K if top_k is None else top_k
    )
    return _retrieve_internal(
        question,
        effective_top_k,
        include_trace=settings.RETRIEVAL_TRACE_ENABLED,
    )
```

When `collector.enabled` is false, Dense must continue to call the current `vector_search(question, top_k)`
path so existing eval and monkeypatched tests keep their behavior and no Trace content is retained.

- [ ] **Step 4: Run the focused retrieval suite and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_hybrid_retriever.py tests\test_retrieval_threshold.py tests\test_reranker.py tests\test_retriever.py -q -p no:cacheprovider
```

Expected: all focused tests pass, with the external/cached-model test behavior unchanged.

- [ ] **Step 5: Commit the pipeline instrumentation**

```powershell
git add backend/services/retriever.py tests/test_retrieval_trace.py
git commit -m "feat: trace retrieval pipeline stages"
```

---

### Task 5: Complete Trace timing in the chat API

**Files:**
- Modify: `backend/routers/chat.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Update the fixture and add failing API tests**

Change the chat fixture to patch `retrieve_with_trace` with `RetrievalResponse(results=retrieval, trace=None)`.
Add one enabled Trace response and assert the API serializes full chunks while preserving citations:

```python
def test_chat_returns_trace_without_changing_answer_or_citations(client, monkeypatch):
    from backend.models.schemas import (
        RetrievalResponse,
        RetrievalTrace,
        RetrievalTraceChunk,
        RetrievalTraceStage,
    )
    from backend.routers import chat as chat_router

    retrieval = [{
        "chunk_id": "c1",
        "content": "Employees receive 5 days of annual leave.",
        "metadata": {"document_name": "handbook.pdf", "page_number": 1},
        "distance": 0.2,
    }]
    trace = RetrievalTrace(
        retrieval_mode="dense",
        reranker_enabled=False,
        chunks={
            "c1": RetrievalTraceChunk(
                chunk_id="c1",
                document_name="handbook.pdf",
                page_number=1,
                content=retrieval[0]["content"],
            )
        },
        stages=[RetrievalTraceStage(name="dense")],
        selected_chunk_ids=["c1"],
    )
    monkeypatch.setattr(
        chat_router,
        "retrieve_with_trace",
        lambda question: RetrievalResponse(results=retrieval, trace=trace),
    )
    monkeypatch.setattr(
        "backend.services.retrieval_trace.count_tokens", lambda context: 8
    )

    response = client.post("/api/chat", json={"question": "annual leave?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["chunks"]["c1"]["content"] == retrieval[0]["content"]
    assert payload["trace"]["context"]["token_count"] == 8
    assert payload["citations"][0]["chunk_id"] == "c1"


def test_chat_trace_is_null_when_retriever_does_not_capture_it(client):
    payload = client.post("/api/chat", json={"question": "annual leave?"}).json()
    assert payload["trace"] is None
```

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py::TestChatAPI -q -p no:cacheprovider
```

Expected: fixture or route failures because chat still calls `retrieve()` and never finalizes Trace timing.

- [ ] **Step 3: Instrument the route**

Modify `backend/routers/chat.py` to:

```python
from time import perf_counter

from backend.services.retriever import retrieve_with_trace, format_retrieval_context
from backend.services.retrieval_trace import finalize_chat_trace


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    total_started = perf_counter()
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    retrieval = retrieve_with_trace(question)
    if not retrieval.results:
        trace = finalize_chat_trace(
            retrieval.trace,
            context="",
            context_formatting_ms=0.0,
            llm_generation_ms=0.0,
            total_ms=(perf_counter() - total_started) * 1000,
        )
        return ChatResponse(
            answer="未找到与您问题相关的文档内容。请确认已上传相关文档，或尝试换个问法。",
            citations=[],
            trace=trace,
        )

    context_started = perf_counter()
    context = format_retrieval_context(retrieval.results)
    context_ms = (perf_counter() - context_started) * 1000
    llm_started = perf_counter()
    try:
        answer = ask(question, context)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {error}")
    llm_ms = (perf_counter() - llm_started) * 1000
    citations = extract_citations(answer, retrieval.results)
    trace = finalize_chat_trace(
        retrieval.trace,
        context=context,
        context_formatting_ms=context_ms,
        llm_generation_ms=llm_ms,
        total_ms=(perf_counter() - total_started) * 1000,
    )
    return ChatResponse(answer=answer, citations=citations, trace=trace)
```

- [ ] **Step 4: Run API and retrieval tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_retrieval_trace.py tests\test_citation.py -q -p no:cacheprovider
```

Expected: all tests pass; answer and citation assertions remain unchanged.

- [ ] **Step 5: Commit the API integration**

```powershell
git add backend/routers/chat.py tests/test_api.py
git commit -m "feat: expose retrieval trace from chat api"
```

---

### Task 6: Render the default-collapsed Stage Tabs UI

**Files:**
- Create: `frontend/trace_view.py`
- Modify: `frontend/app.py`
- Modify: `tests/test_frontend_app.py`

- [ ] **Step 1: Write failing pure-row and AppTest tests**

Add a representative Trace payload to `tests/test_frontend_app.py`, make `FakeBackendClient.post()` return
it for `/api/chat`, and add:

```python
from frontend.trace_view import candidate_rows, executed_stage_names


def test_candidate_rows_join_full_chunk_catalog_and_make_preview():
    rows = candidate_rows(TRACE_PAYLOAD, "dense")
    assert rows[0]["chunk_id"] == "c1"
    assert rows[0]["文档"] == "doc.txt"
    assert rows[0]["摘要"].endswith("…")
    assert rows[0]["是否入选"] == "是"


def test_executed_stage_names_follow_backend_order():
    assert executed_stage_names(TRACE_PAYLOAD) == ["dense", "reranker", "selected"]


def test_trace_panel_is_present_and_collapsed_after_chat(monkeypatch):
    FakeBackendClient.documents = [{
        "document_id": "doc-1",
        "original_name": "doc.txt",
        "status": "ready",
        "chunk_count": 1,
    }]
    FakeBackendClient.chat_payload = {
        "answer": "回答【来源1】",
        "citations": [],
        "trace": TRACE_PAYLOAD,
    }
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)
    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.chat_input[0].set_value("问题")
    app.run()

    trace_expanders = [item for item in app.expander if "检索详情" in item.label]
    assert len(trace_expanders) == 1
    assert trace_expanders[0].proto.expanded is False
    assert [tab.label for tab in trace_expanders[0].tabs] == [
        "Dense", "Reranker", "最终上下文"
    ]
    assert app.session_state.messages[-1]["trace"] == TRACE_PAYLOAD
```

Use a chunk content longer than 120 characters in `TRACE_PAYLOAD` so the preview assertion is meaningful.

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_app.py -q -p no:cacheprovider
```

Expected: import failure for `frontend.trace_view` and no Trace expander.

- [ ] **Step 3: Implement `frontend/trace_view.py`**

Create the module with:

```python
import streamlit as st


STAGE_LABELS = {
    "dense": "Dense",
    "bm25": "BM25",
    "rrf": "RRF",
    "reranker": "Reranker",
    "selected": "最终上下文",
}

TIMING_LABELS = {
    "query_embedding": "Query Embedding",
    "dense_retrieval": "Dense Retrieval",
    "sparse_retrieval": "Sparse Retrieval",
    "rrf": "RRF",
    "rerank": "Reranker",
    "context_formatting": "Context Formatting",
    "llm_generation": "LLM Generation",
}


def executed_stage_names(trace: dict) -> list[str]:
    return [
        stage["name"]
        for stage in trace.get("stages", [])
        if stage.get("name") in STAGE_LABELS
    ]


def candidate_rows(trace: dict, stage_name: str) -> list[dict]:
    chunks = trace.get("chunks", {})
    stage = next(
        (item for item in trace.get("stages", []) if item.get("name") == stage_name),
        {"candidates": []},
    )
    rows = []
    for candidate in stage.get("candidates", []):
        chunk = chunks.get(candidate.get("chunk_id"), {})
        content = chunk.get("content", "")
        preview = content if len(content) <= 120 else content[:120].rstrip() + "…"
        rows.append({
            "排名": candidate.get("rank"),
            "文档": chunk.get("document_name", "unknown"),
            "页码": chunk.get("page_number", 0),
            "chunk_id": candidate.get("chunk_id", ""),
            "distance": candidate.get("distance"),
            "BM25": candidate.get("bm25_score"),
            "RRF": candidate.get("rrf_score"),
            "Reranker": candidate.get("reranker_score"),
            "过滤状态": candidate.get("filtered_reason") or "通过",
            "是否入选": "是" if candidate.get("selected") else "否",
            "摘要": preview,
        })
    return rows


def render_retrieval_trace(trace: dict | None, message_key: str) -> None:
    if not trace:
        return
    context = trace.get("context", {})
    timings = trace.get("timings_ms", {})
    mode = trace.get("retrieval_mode", "unknown").title()
    if trace.get("reranker_enabled"):
        mode += " + Reranker"
    label = (
        f"🔎 检索详情 · {mode} · {context.get('chunk_count', 0)} chunks · "
        f"{timings.get('total', 0) / 1000:.2f}s"
    )
    with st.expander(label, expanded=False):
        summary = st.columns(3)
        summary[0].metric("候选阶段", len(executed_stage_names(trace)))
        summary[1].metric(
            "最终上下文",
            f"{context.get('chunk_count', 0)} chunks",
            f"{context.get('token_count', 0)} tokens",
        )
        summary[2].metric("总耗时", f"{timings.get('total', 0):.1f} ms")

        stage_names = executed_stage_names(trace)
        tabs = st.tabs([STAGE_LABELS[name] for name in stage_names])
        for stage_name, tab in zip(stage_names, tabs):
            with tab:
                rows = candidate_rows(trace, stage_name)
                event = st.dataframe(
                    rows,
                    hide_index=True,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"trace_table_{message_key}_{stage_name}",
                )
                selected_rows = event.selection.rows
                if selected_rows:
                    selected = rows[selected_rows[0]]
                    chunk = trace.get("chunks", {}).get(selected["chunk_id"])
                    if chunk:
                        with st.container(border=True):
                            st.caption(
                                f"完整 Chunk · {chunk['document_name']} · "
                                f"第 {chunk['page_number']} 页 · {selected['chunk_id']}"
                            )
                            st.markdown(chunk.get("content", ""))

        measured = {
            key: float(timings.get(key) or 0)
            for key in TIMING_LABELS
            if float(timings.get(key) or 0) > 0
        }
        if measured:
            st.markdown("**阶段耗时**")
            maximum = max(measured.values())
            for key, milliseconds in measured.items():
                st.progress(
                    milliseconds / maximum,
                    text=f"{TIMING_LABELS[key]} · {milliseconds:.1f} ms",
                )
```

- [ ] **Step 4: Integrate new and historical messages**

Import `render_retrieval_trace` in both frontend import branches. In the history loop, use the message index
as a stable key and call it after citations:

```python
for message_index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
# render the current citation expander before the Trace panel
        if msg["role"] == "assistant":
            render_retrieval_trace(msg.get("trace"), f"history_{message_index}")
```

For a new response, read and persist Trace:

```python
trace = result.get("trace")
render_retrieval_trace(trace, f"new_{len(st.session_state.messages)}")
st.session_state.messages.append({
    "role": "assistant",
    "content": answer,
    "citations": citations,
    "trace": trace,
})
```

- [ ] **Step 5: Run frontend tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_app.py tests\test_frontend_http_utils.py -q -p no:cacheprovider
```

Expected: all frontend tests pass; AppTest confirms the expander is closed by default.

- [ ] **Step 6: Commit the UI**

```powershell
git add frontend/trace_view.py frontend/app.py tests/test_frontend_app.py
git commit -m "feat: render retrieval trace in Streamlit"
```

---

### Task 7: Full verification and real-environment acceptance

**Files:**
- Modify locally but do not stage: `.env`
- Create: `eval/results/p2-3-retrieval-trace/trace-response.json`
- Create: `eval/results/p2-3-retrieval-trace/streamlit-trace.png`
- Create: `eval/results/p2-3-retrieval-trace/validation.md`
- Modify: `README.md`
- Modify: `eval/README.md`
- Modify: `docs/交接_阶段一优化.md`

- [ ] **Step 1: Run all automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: zero failures, zero dependency conflicts and no whitespace errors.

- [ ] **Step 2: Enable the local-only gate and start the real services**

Set exactly this ignored local value without staging `.env`:

```dotenv
RETRIEVAL_TRACE_ENABLED=true
```

Start in one PowerShell session and retain the emitted process IDs:

```powershell
$startupOutput = @(.\scripts\start.ps1)
$startupOutput | ForEach-Object { Write-Host $_ }
$joinedOutput = $startupOutput -join "`n"
$backendPid = [int]([regex]::Match(
  $joinedOutput, 'Backend PID: (\d+)'
).Groups[1].Value)
$frontendPid = [int]([regex]::Match(
  $joinedOutput, 'Frontend PID: (\d+)'
).Groups[1].Value)
```

Record the Backend and Frontend PIDs printed by the script. Confirm `GET http://127.0.0.1:8000/`
returns HTTP 200 before submitting the question.

- [ ] **Step 3: Execute one real production-index chat**

POST this stable P2-1 lexical question to the real backend:

```json
{"question":"Chroma、Qdrant 和 Milvus 分别适合什么部署规模？"}
```

Save the successful response verbatim as
`eval/results/p2-3-retrieval-trace/trace-response.json`. Verify from the saved JSON:

- `answer` contains the deployment distinction and at least one valid citation;
- `trace.retrieval_mode` equals `dense`;
- stage names equal `dense`, `reranker`, `selected`;
- `trace.chunks` contains complete non-empty content;
- `selected_chunk_ids` equals the chunk IDs marked `selected=true`;
- timings for query embedding, dense retrieval, rerank, LLM generation and total are positive;
- context chunk, character and token counts are positive.

- [ ] **Step 4: Verify the Streamlit interaction in a browser**

Open `http://127.0.0.1:8501`, submit the same question, and verify:

1. “检索详情” exists below the assistant answer and is closed initially;
2. expanding it shows `Dense`, `Reranker`, `最终上下文` tabs;
3. selecting a row displays the complete chunk;
4. timing progress bars render without clipping at desktop width;
5. the panel remains usable after one additional Streamlit rerun.

Save the expanded state screenshot to
`eval/results/p2-3-retrieval-trace/streamlit-trace.png`.

- [ ] **Step 5: Stop both services safely**

```powershell
Stop-Process -Id $backendPid,$frontendPid
Get-Process -Id $backendPid,$frontendPid -ErrorAction SilentlyContinue
```

The final command must return no process rows.

- [ ] **Step 6: Write the measured validation report and docs**

Create `validation.md` with these sections populated only from the saved JSON and browser observation:

- environment and exact commit;
- question and answer/citation result;
- executed stages and candidate counts;
- context chunk/character/token counts;
- every measured timing in milliseconds;
- default-collapsed, row-selection and full-chunk browser checks;
- security statement that full candidate content is returned only when the server gate is enabled;
- screenshot and JSON filenames.

Update README and eval README with the flag, launch behavior and panel description. Update the handoff document
to state that P2-2 was intentionally skipped because its Hybrid-benefit prerequisite failed, then record P2-3
implementation, tests, real timings and artifact paths.

- [ ] **Step 7: Re-run documentation and repository checks**

```powershell
git diff --check
git status --short
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_api.py tests\test_frontend_app.py -q -p no:cacheprovider
```

Expected: no whitespace errors, only intended P2-3 files staged or unstaged, and all focused tests pass.

- [ ] **Step 8: Commit the evidence and documentation**

```powershell
git add README.md eval/README.md docs/交接_阶段一优化.md eval/results/p2-3-retrieval-trace
git commit -m "docs: record P2-3 retrieval trace validation"
```

- [ ] **Step 9: Finish the development work**

Invoke `superpowers:verification-before-completion`, run the fresh full verification commands, then invoke
`superpowers:finishing-a-development-branch`. This repository is currently the user-approved normal `master`
workspace, so do not create, merge, push or delete a branch unless the user explicitly changes that instruction.
