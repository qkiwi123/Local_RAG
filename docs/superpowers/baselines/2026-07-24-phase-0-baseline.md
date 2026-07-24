# RAG 下一阶段：阶段 0 基线记录

状态：工程基线已确认；真实 27 题 LLM 基线等待有效 `DEEPSEEK_API_KEY`。

本文只记录改造前证据、保留行为和未来清理边界。阶段 0 不删除数据库、索引、上传文件或历史评测产物，也不宣称任何检索或回答质量提升。

## 1. Git 与工程测试基线

- 基线提交：`5d2a2a35112f803bb6fcdc1604ebee47e0eb17ee`
- 基线分支：`master`
- 实施分支：`feature/rag-next-stage`
- 隔离工作区：`D:\ProgramFiles\RAG\.worktrees\rag-next-stage`
- Python：`3.14.0`
- pytest：`8.3.0`

执行命令：

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest -q
```

改造前结果：

```text
251 passed, 3 skipped, 19562 warnings in 14.88s
```

警告主要来自 Python 3.14 下 `pytest-asyncio` 的弃用提示，另有 FastAPI `on_event`、PyPDF2、Starlette TestClient 等弃用提示。大量警告会淹没新增告警，属于已确认的日志信噪比问题；它们不作为阶段 0 的失败条件，也不能据此推断这些警告可以长期忽略。后续应单独升级依赖或收敛已知警告，不能全局屏蔽未知 warning。

阶段 0 工程修复后的全量结果：

```text
261 passed, 3 skipped, 20083 warnings in 12.75s
```

新增 10 个测试覆盖：四个评测命令默认使用 v2、MD/PPTX 文档名归一化、显式位置标签与幻灯片位置、报告与 README 不再使用未经测量的固定噪声阈值。测试总数增加导致 `pytest-asyncio` 按测试收集产生的弃用警告数量同步增加，不代表新增 521 个独立缺陷。

## 2. 固定评测资产

### 2.1 数据集

| 文件 | 版本 | 题数 | 可回答 | 不可回答 | 字节数 | SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| `eval/dataset/questions.json` | 1 | 21 | 17 | 4 | 14458 | `d1320daee4af5b2b0dec6bb605fbabb587a1715e05269c457efec68f2f25c9a7` |
| `eval/dataset/questions_v2.json` | 2 | 27 | 22 | 5 | 18042 | `794817463e5a80b759b829ab469893d5e3d143b93e8f11306c44d636de7cbb73` |

`questions_v2.json` 是当前 legacy regression 的最新固定集。`questions.json` 作为历史 v1 保留，但不再作为默认入口。

### 2.2 语料

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `eval/corpus/2024技术发展报告_RAG测试文档.docx` | 13988 | `c377cbe9548947f5cb74b81bbc6d7c54b0b813b83813dd6e26a479cb4694bcdc` |
| `eval/corpus/AI Agent文献综述_v3.docx` | 33069 | `16b8abbf6b1e03233685bc8e7c41615c8fbcea2ecc843559c2883d8b8883f279` |
| `eval/corpus/AI技术概述_RAG测试文档.txt` | 10838 | `c17130f2bcc846a88b8280a33af450c6c1e9254b925a6f9cb33e81cabc2a1cfd` |
| `eval/corpus/LLM应用指南_RAG测试文档.pdf` | 435427 | `8c40a27b61526e05fb1656cf2de6118dd8ab7dcc068c382eea3391d023b8b3a0` |

### 2.3 已提交历史证据

`eval/results/` 当前包含 46 个已提交文件：

| 目录 | 文件数 |
|---|---:|
| `p1-1-bge/inst` | 3 |
| `p1-1-bge/noinst` | 3 |
| `p1-2-docx-table` | 3 |
| `p1-3-recursive-chunking` | 7 |
| `p1-4-threshold-calibration` | 8 |
| `p1-5-reranker` | 11 |
| `p2-1-hybrid-retrieval` | 8 |
| `p2-3-retrieval-trace` | 3 |

## 3. 当前生产路径快照

根项目 `D:\ProgramFiles\RAG\.env` 在 2026-07-24 的路径配置解析结果：

```text
UPLOAD_DIR=D:\ProgramFiles\RAG\data\uploads
CHROMA_PERSIST_DIR=D:\ProgramFiles\RAG\data\chroma_p2_4a
METADATA_DB_PATH=D:\ProgramFiles\RAG\data\metadata.db
```

其中 `.env` 明确配置了相对 `UPLOAD_DIR=./data/uploads` 和
`CHROMA_PERSIST_DIR=./data/chroma_p2_4a`；没有配置 `METADATA_DB_PATH`，
因此后者使用 `backend/config.py` 以项目根目录解析出的默认路径。

## 4. 一次性清理边界

以下均为旧派生数据候选。只有阶段 2 的结构化解析、存储、检索和预览在隔离目录全部通过后，才能按绝对路径逐项清理：

```text
D:\ProgramFiles\RAG\data\backups
D:\ProgramFiles\RAG\data\chroma_bge_large_zh_v1_5
D:\ProgramFiles\RAG\data\chroma_db
D:\ProgramFiles\RAG\data\chroma_p1_2_docx_table
D:\ProgramFiles\RAG\data\chroma_p1_3_recursive_400_80
D:\ProgramFiles\RAG\data\chroma_p2_4a
D:\ProgramFiles\RAG\data\uploads
D:\ProgramFiles\RAG\data\metadata.db
D:\ProgramFiles\RAG\data\metadata.db.bak-20260722-165150
```

清理时必须再次解析并核对每个绝对路径都位于
`D:\ProgramFiles\RAG\data` 内。禁止使用未解析的通配符、递归删除仓库根目录或把 PowerShell 枚举结果交给另一种 shell 删除。

必须保留：

- `eval/corpus/`
- `eval/dataset/`
- `eval/results/`
- 全部项目源代码、测试、设计与实施文档
- 根项目真实 `.env`

`eval/runs/<label>/` 是每次评测自己的隔离 Chroma、SQLite、uploads 和报告目录，不属于生产数据。是否保留某次 run 由证据价值决定，不能与生产清理混为一谈。

## 5. 必须保持的行为契约

以下现有测试是后续阶段的最低回归契约，测试类名与 node ID 已通过
`pytest --collect-only` 核对：

```text
tests/test_api.py::TestUploadAPI::test_upload_returns_stable_document_id
tests/test_api.py::TestUploadAPI::test_same_name_creates_independent_versions
tests/test_api.py::TestUploadAPI::test_failed_ingest_cleans_files_and_marks_record
tests/test_api.py::TestChatAPI::test_chat_uses_mocked_llm_and_returns_grounded_citation
tests/test_api.py::TestChatAPI::test_chat_returns_trace_without_changing_answer_or_citations
tests/test_api.py::TestUploadPageSnapshots::test_successful_upload_persists_page_snapshots
tests/test_retriever.py::TestRetrieve::test_results_contain_required_fields
tests/test_retriever.py::TestRetrieve::test_top_k_respected
tests/test_citation.py::TestExtractCitations::test_maps_source_id_to_retrieval_result
tests/test_citation.py::TestExtractCitations::test_no_marker_has_no_fallback
tests/test_citation_preview.py::TestCitationPreviewService::test_build_preview_success
```

它们分别锁定：稳定文档 ID、同名文档多版本、失败补偿、基于检索证据的回答与引用、Trace 不改变回答契约、解析页快照、检索结果字段与 top-k、可信来源编号映射、无标记时不伪造引用、引用原文预览。

后续可以用 `document_units` 和结构化预览替代旧页快照实现，但不能在没有等价新测试的情况下删除这些用户可见行为。

## 6. 隔离评测状态

- `questions_v2` 无 LLM 检索基线：已完成。
- `questions_v2` 真实 27 题 LLM 基线：等待有效 `DEEPSEEK_API_KEY`。

当前进程和根项目 `.env` 均未检测到有效 `DEEPSEEK_API_KEY`。这只说明本机当前配置不足，不能推断外部服务不可用。正式基线不得使用 mock、空答案、AI 生成答案或占位密钥替代真实系统回答。

### 6.1 无 LLM 检索基线

- label：`phase0-prechange-retrieval-v2`
- 完成时间：`2026-07-24T16:25:06`
- 数据集：v2，27 题
- 入库：68 chunks，40.01 秒
- 扫描：Reranker candidate k = 15、20、30
- 推荐：`k=15`
- 产物：`eval/runs/phase0-prechange-retrieval-v2/`

配置：

```text
embedding_model=BAAI/bge-large-zh-v1.5
chunk_size=400
chunk_overlap=80
chunk_strategy=recursive
chunk_max_tokens=480
top_k=5
retrieval_mode=dense
retrieval_distance_threshold=0.67
reranker_model=BAAI/bge-reranker-v2-m3
reranker_device=cpu
dataset_version=2
```

结果：

| candidate k | 题数 | Hit Rate | Recall | MRR | 不可回答题进入 LLM 比例 | mean | p50 | p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15 | 27 | 1.0000 | 0.9545 | 0.9545 | 0.8000 | 6.878s | 7.843s | 8.214s |
| 20 | 27 | 1.0000 | 0.9545 | 0.9545 | 0.8000 | 8.035s | 9.120s | 9.859s |
| 30 | 27 | 1.0000 | 0.9545 | 0.9545 | 0.8000 | 10.106s | 12.022s | 12.971s |

三个候选规模的检索质量指标相同，均无 answerable 检索失败；`k=15` 延迟最低，因此本次扫描推荐 15。这是当前 4 文档、27 题固定集上的结果，不能外推为更大新测试集上的最佳值。

运行命令完成后对 `D:\ProgramFiles\RAG\data` 下全部文件的长度与 UTC 修改时间进行了前后比较，结果为：

```text
PRODUCTION_DATA_UNCHANGED
```

首次尝试因执行工具的 120 秒上限在 `k=15` 第 10/27 题后被终止，该半成品未计入结果；随后从空隔离 Chroma 重新完整运行，耗时 718.3 秒并以退出码 0 结束。
