# P2-3 检索链路可视化实施计划

> **供执行者使用：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 子技能，逐项执行本计划。所有步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 增加由服务端开关控制、默认折叠的 Streamlit Trace 面板，在不改变检索质量和现有回答/引用契约的前提下，展示每个实际执行的检索阶段、去重后的完整候选 chunk、上下文规模和端到端耗时。

**架构：** 保持 `retrieve()` 向后兼容，在共享内部检索流水线上增加 `retrieve_with_trace()`。独立的 `retrieval_trace.py` 负责快照阶段元数据并对完整 chunk 原文去重；聊天路由补齐上下文和 LLM 耗时；`frontend/trace_view.py` 负责动态阶段标签和选中行的完整原文展示。`RETRIEVAL_TRACE_ENABLED=false` 是安全默认值，关闭时候选内容不会进入 API 响应。

**技术栈：** Python 3.14、FastAPI、Pydantic、ChromaDB、Streamlit 1.59.1、pytest、Streamlit AppTest、httpx。

---

## 文件职责

- 修改 `backend/config.py`：增加安全的 Trace 功能开关。
- 修改 `.env.example`：说明默认关闭行为和内容泄露风险。
- 修改 `tests/conftest.py`：在通用测试环境中强制关闭 Trace。
- 修改 `backend/models/schemas.py`：定义 API 和内部 Trace 契约。
- 创建 `backend/services/retrieval_trace.py`：负责阶段快照、chunk 目录、耗时和上下文统计。
- 修改 `backend/services/vector_store.py`：支持预计算向量查询，同时保留 `search(query)`。
- 修改 `backend/services/retriever.py`：共享检索流水线与可选 Trace 捕获。
- 修改 `backend/routers/chat.py`：返回 Trace 并补齐上下文/LLM 耗时。
- 创建 `frontend/trace_view.py`：负责表格行转换与 Streamlit Trace 渲染。
- 修改 `frontend/app.py`：持久化并渲染新回答和历史回答的 Trace。
- 创建 `tests/test_retrieval_trace.py`：覆盖配置、契约、收集器和检索器行为。
- 创建 `tests/test_vector_store_trace.py`：覆盖预计算向量查询契约。
- 修改 `tests/test_api.py`：覆盖 ChatResponse 兼容性与 Trace API 行为。
- 修改 `tests/test_frontend_app.py`：覆盖默认折叠面板和历史消息持久化。
- 修改 `README.md`、`eval/README.md`、`docs/交接_阶段一优化.md`：记录配置、P2-2 跳过原因和 P2-3 证据。
- 创建 `eval/results/p2-3-retrieval-trace/validation.md`：保存真实环境实测证据。
- 创建 `eval/results/p2-3-retrieval-trace/trace-response.json`：保存包含完整 Trace 的真实 API 响应。
- 创建 `eval/results/p2-3-retrieval-trace/streamlit-trace.png`：保存面板展开状态的浏览器证据。

---

### 任务 1：增加安全配置与强类型 Trace 契约

**文件：**
- 修改：`backend/config.py`
- 修改：`.env.example`
- 修改：`tests/conftest.py`
- 修改：`backend/models/schemas.py`
- 创建：`tests/test_retrieval_trace.py`

- [ ] **步骤 1：编写失败的配置与数据模型测试**

在 `tests/test_retrieval_trace.py` 中加入以下契约测试：

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

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py -q -p no:cacheprovider
```

预期：测试收集失败，因为 Retrieval Trace 数据模型和配置项尚不存在。

- [ ] **步骤 3：增加开关和 Pydantic 数据模型**

在 `backend/config.py` 的 `Settings` 中加入：

```python
RETRIEVAL_TRACE_ENABLED: bool = _env_bool(
    "RETRIEVAL_TRACE_ENABLED", False
)
```

在 `.env.example` 的检索配置下方加入：

```dotenv
# P2-3 调试面板；开启后 API 会返回未入选候选的完整 chunk，仅限可信环境
RETRIEVAL_TRACE_ENABLED=false
```

在 `tests/conftest.py` 导入任何 backend 模块之前加入：

```python
os.environ["RETRIEVAL_TRACE_ENABLED"] = "false"
```

在 `backend/models/schemas.py` 中加入以下模型，并给 `ChatResponse` 增加 `trace` 字段：

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

- [ ] **步骤 4：运行专项测试并确认处于 GREEN 状态**

再次运行任务 1 的测试命令。预期：`test_retrieval_trace.py` 中所有测试通过。

- [ ] **步骤 5：提交数据契约**

```powershell
git add backend/config.py backend/models/schemas.py tests/conftest.py tests/test_retrieval_trace.py .env.example
git commit -m "feat: add retrieval trace contracts"
```

---

### 任务 2：拆分查询向量化与 Chroma 检索

**文件：**
- 修改：`backend/services/vector_store.py`
- 创建：`tests/test_vector_store_trace.py`

- [ ] **步骤 1：编写失败的预计算向量测试**

创建 `tests/test_vector_store_trace.py`：

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

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vector_store_trace.py -q -p no:cacheprovider
```

预期：测试失败，因为 `search_by_embedding` 尚不存在。

- [ ] **步骤 3：提取使用预计算向量的查询入口**

在 `backend/services/vector_store.py` 中实现：

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

- [ ] **步骤 4：运行专项测试和现有向量存储测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vector_store_trace.py tests\test_vector_store.py -q -p no:cacheprovider
```

预期：新增测试通过；带 integration 标记的向量测试根据当前 marker 选择正常通过或保持跳过。

- [ ] **步骤 5：提交向量存储接缝改造**

```powershell
git add backend/services/vector_store.py tests/test_vector_store_trace.py
git commit -m "refactor: separate query embedding from vector search"
```

---

### 任务 3：实现无副作用的 Trace 收集器

**文件：**
- 创建：`backend/services/retrieval_trace.py`
- 修改：`tests/test_retrieval_trace.py`

- [ ] **步骤 1：增加失败的收集器测试**

在 `tests/test_retrieval_trace.py` 末尾加入：

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

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

运行任务 1 的专项测试命令。预期：因 `retrieval_trace` 尚不存在而导入失败。

- [ ] **步骤 3：实现 Trace 收集器**

创建 `backend/services/retrieval_trace.py`，提供以下公共接口：

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

- [ ] **步骤 4：运行收集器测试并确认处于 GREEN 状态**

运行任务 1 的专项测试命令。预期：收集器、配置和数据模型测试全部通过。

- [ ] **步骤 5：提交 Trace 收集器**

```powershell
git add backend/services/retrieval_trace.py tests/test_retrieval_trace.py
git commit -m "feat: capture retrieval trace stages"
```

---

### 任务 4：在不改变 `retrieve()` 契约的前提下追踪 Dense、BM25、RRF 和 Reranker

**文件：**
- 修改：`backend/services/retriever.py`
- 修改：`tests/test_retrieval_trace.py`
- 验证：`tests/test_hybrid_retriever.py`
- 验证：`tests/test_reranker.py`
- 验证：`tests/test_retrieval_threshold.py`

- [ ] **步骤 1：增加失败的检索流水线测试**

追加测试，通过 monkeypatch 替换 `embed_query`、`vector_search_by_embedding`、
`bm25_search`、融合函数和 Reranker，然后断言：

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

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_hybrid_retriever.py -q -p no:cacheprovider
```

预期：测试失败，因为 `retrieve_with_trace` 和相关追踪导入尚不存在。

- [ ] **步骤 3：重构为单一内部检索流水线**

在 `backend/services/retriever.py` 中：

- 导入 `perf_counter`、`embed_query`、
  `search_by_embedding as vector_search_by_embedding`、`RetrievalResponse` 和
  `TraceCollector`；
- 用以下函数替换候选生成和文档限额逻辑：

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

- 严格按以下形式提供兼容包装函数：

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

当 `collector.enabled` 为 false 时，Dense 必须继续调用现有
`vector_search(question, top_k)` 路径，保证 eval 和已有 monkeypatch 测试行为不变，
同时不保留任何 Trace 内容。

- [ ] **步骤 4：运行检索专项测试并确认处于 GREEN 状态**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_hybrid_retriever.py tests\test_retrieval_threshold.py tests\test_reranker.py tests\test_retriever.py -q -p no:cacheprovider
```

预期：所有专项测试通过，外部 API 和本地缓存模型测试的执行/跳过规则保持不变。

- [ ] **步骤 5：提交检索流水线追踪改造**

```powershell
git add backend/services/retriever.py tests/test_retrieval_trace.py
git commit -m "feat: trace retrieval pipeline stages"
```

---

### 任务 5：在聊天 API 中补齐 Trace 耗时

**文件：**
- 修改：`backend/routers/chat.py`
- 修改：`tests/test_api.py`

- [ ] **步骤 1：更新 fixture 并增加失败的 API 测试**

修改聊天 fixture，用 `RetrievalResponse(results=retrieval, trace=None)` 替换
`retrieve_with_trace`。增加一组开启 Trace 的响应，断言 API 会序列化完整 chunk，同时保持
引用结果不变：

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

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py::TestChatAPI -q -p no:cacheprovider
```

预期：fixture 或路由测试失败，因为聊天路由仍调用 `retrieve()`，也没有补齐 Trace 耗时。

- [ ] **步骤 3：给聊天路由增加耗时追踪**

按以下方式修改 `backend/routers/chat.py`：

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

- [ ] **步骤 4：运行 API 和检索测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_retrieval_trace.py tests\test_citation.py -q -p no:cacheprovider
```

预期：所有测试通过，回答和引用断言保持不变。

- [ ] **步骤 5：提交 API 集成**

```powershell
git add backend/routers/chat.py tests/test_api.py
git commit -m "feat: expose retrieval trace from chat api"
```

---

### 任务 6：渲染默认折叠的阶段标签界面

**文件：**
- 创建：`frontend/trace_view.py`
- 修改：`frontend/app.py`
- 修改：`tests/test_frontend_app.py`

- [ ] **步骤 1：编写失败的纯数据转换测试和 AppTest 测试**

在 `tests/test_frontend_app.py` 中加入代表性的 Trace payload，让
`FakeBackendClient.post()` 在 `/api/chat` 请求时返回它，并增加：

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

`TRACE_PAYLOAD` 中的 chunk 原文必须超过 120 个字符，确保摘要截断断言有实际意义。

- [ ] **步骤 2：运行测试并确认处于 RED 状态**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_app.py -q -p no:cacheprovider
```

预期：因 `frontend.trace_view` 尚不存在而导入失败，页面中也没有 Trace 折叠面板。

- [ ] **步骤 3：实现 `frontend/trace_view.py`**

创建该模块，内容如下：

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
    "query_embedding": "查询向量化",
    "dense_retrieval": "Dense 检索",
    "sparse_retrieval": "Sparse 检索",
    "rrf": "RRF",
    "rerank": "Reranker",
    "context_formatting": "上下文组装",
    "llm_generation": "LLM 生成",
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

- [ ] **步骤 4：接入新回答与历史消息**

在前端的两条导入分支中都导入 `render_retrieval_trace`。渲染历史消息时使用消息序号作为
稳定 key，并在引用区之后调用：

```python
for message_index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
# 先渲染现有引用折叠区，再渲染 Trace 面板
        if msg["role"] == "assistant":
            render_retrieval_trace(msg.get("trace"), f"history_{message_index}")
```

对于新回答，读取并持久化 Trace：

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

- [ ] **步骤 5：运行前端测试并确认处于 GREEN 状态**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_app.py tests\test_frontend_http_utils.py -q -p no:cacheprovider
```

预期：所有前端测试通过，AppTest 确认折叠面板默认关闭。

- [ ] **步骤 6：提交前端界面**

```powershell
git add frontend/trace_view.py frontend/app.py tests/test_frontend_app.py
git commit -m "feat: render retrieval trace in Streamlit"
```

---

### 任务 7：全量验证与真实环境验收

**文件：**
- 仅本机修改且不暂存：`.env`
- 创建：`eval/results/p2-3-retrieval-trace/trace-response.json`
- 创建：`eval/results/p2-3-retrieval-trace/streamlit-trace.png`
- 创建：`eval/results/p2-3-retrieval-trace/validation.md`
- 修改：`README.md`
- 修改：`eval/README.md`
- 修改：`docs/交接_阶段一优化.md`

- [ ] **步骤 1：运行全部自动化验证**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

预期：零失败、零依赖冲突、零空白格式错误。

- [ ] **步骤 2：开启仅限本机的 Trace 开关并启动真实服务**

在忽略提交的本机 `.env` 中设置以下值，不暂存该文件：

```dotenv
RETRIEVAL_TRACE_ENABLED=true
```

在同一个 PowerShell 会话中启动服务并保存输出的进程 ID：

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

提交问题前，确认脚本打印出的后端和前端 PID 均已记录，并且
`GET http://127.0.0.1:8000/` 返回 HTTP 200。

- [ ] **步骤 3：使用真实生产索引执行一次问答**

向真实后端 POST 这道稳定的 P2-1 精确术语问题：

```json
{"question":"Chroma、Qdrant 和 Milvus 分别适合什么部署规模？"}
```

将成功响应原样保存为
`eval/results/p2-3-retrieval-trace/trace-response.json`。根据保存的 JSON 验证：

- `answer` 包含三种向量数据库的部署规模区别，并至少有一条有效引用；
- `trace.retrieval_mode` 等于 `dense`；
- 阶段名称依次为 `dense`、`reranker`、`selected`；
- `trace.chunks` 包含完整且非空的原文；
- `selected_chunk_ids` 与标记为 `selected=true` 的 chunk ID 一致；
- 查询向量化、Dense 检索、Reranker、LLM 生成和总耗时均大于零；
- 上下文 chunk 数、字符数和 token 数均大于零。

- [ ] **步骤 4：在浏览器中验证 Streamlit 交互**

打开 `http://127.0.0.1:8501`，提交同一问题并验证：

1. “检索详情” exists below the assistant answer and is closed initially;
2. expanding it shows `Dense`, `Reranker`, `最终上下文` tabs;
3. selecting a row displays the complete chunk;
4. timing progress bars render without clipping at desktop width;
5. the panel remains usable after one additional Streamlit rerun.

将展开状态截图保存到
`eval/results/p2-3-retrieval-trace/streamlit-trace.png`。

- [ ] **步骤 5：安全停止前后端服务**

```powershell
Stop-Process -Id $backendPid,$frontendPid
Get-Process -Id $backendPid,$frontendPid -ErrorAction SilentlyContinue
```

最后一条命令必须不返回任何进程记录。

- [ ] **步骤 6：写入实测验收报告和项目文档**

创建 `validation.md`，以下各节只能使用已保存 JSON 和浏览器观察到的事实填写：

- 环境与准确提交；
- 问题及回答/引用结果；
- 实际执行阶段及候选数量；
- 上下文 chunk/字符/token 数；
- 所有阶段的毫秒实测耗时；
- 默认折叠、行选择与完整 chunk 浏览器检查；
- 只有服务端开关开启时才返回完整候选内容的安全说明；
- 截图和 JSON 文件名。

在 README 和 eval README 中补充开关、启动行为和面板说明。更新交接文档，明确 P2-2 因
Hybrid 收益前提未满足而被有意跳过，并记录 P2-3 实现、测试、真实耗时和产物路径。

- [ ] **步骤 7：重新运行文档与仓库检查**

```powershell
git diff --check
git status --short
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_trace.py tests\test_api.py tests\test_frontend_app.py -q -p no:cacheprovider
```

预期：没有空白格式错误，仓库中只有预期的 P2-3 文件变更，全部专项测试通过。

- [ ] **步骤 8：提交验收证据和文档**

```powershell
git add README.md eval/README.md docs/交接_阶段一优化.md eval/results/p2-3-retrieval-trace
git commit -m "docs: record P2-3 retrieval trace validation"
```

- [ ] **步骤 9：完成开发收尾**

调用 `superpowers:verification-before-completion`，重新运行全量验证命令，然后调用
`superpowers:finishing-a-development-branch`。当前仓库是用户已批准直接使用的普通 `master`
工作区；除非用户明确改变指令，否则不得创建、合并、推送或删除分支。
