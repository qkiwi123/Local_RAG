# 中文企业知识库 RAG

一个以“可评估、可追溯、可复现”为核心的中文 RAG 项目。系统通过统一文档结构处理
PDF、DOCX、TXT、Markdown 和 PPTX，完成解析、切分、向量检索、Cross-encoder
重排和 DeepSeek 问答，并使用稳定的 `【来源N】` 协议返回可定位到原文结构的引用。

## 当前状态

截至 2026-07-30，以下链路已经贯通：

- 五种文档格式统一输出 `ParsedBlock`、`ParsedUnit` 和 `SourceLocator`；
- 公共 Assembler 负责稳定 ID、连续 sequence、canonical text 和字符 offset；
- 生产上传和索引重建均使用 `structured-v1`，不再以旧 page-dict 作为最终数据结构；
- Chroma 保存检索所需的轻量定位 metadata，SQLite 保存文档、结构化 unit 和会话；
- Dense 检索使用 `BAAI/bge-large-zh-v1.5`，可选
  `BAAI/bge-reranker-v2-m3` 进行 Cross-encoder 重排；
- 引用预览按 unit 原序恢复整份文档结构并高亮命中区间；
- 支持持久化多轮对话、追问改写、有界历史、幂等重放和并发冲突检测；
- 检索 Trace 可在受控环境展示 Dense、BM25、RRF、Reranker 和最终上下文阶段。

当前仍未完成的正式验收项：

- Dataset v3 的 41 个审核组仍为 `draft`，尚未由真实审核者逐项签核；
- 自动关键点评分器对 Markdown、代码符号、空格和同义改写过于敏感；
- 不可回答题仍出现过一次“正确拒答但附带引用”的协议问题；
- CPU Reranker 的检索延迟仍然较高。

## 当前架构

```text
Streamlit
   │ HTTP
   ▼
FastAPI
   ├── upload
   │     file
   │       → format adapter
   │       → ParsedUnit
   │       → structured chunker
   │       → BGE embedding
   │       → Chroma
   │       └── SQLite documents / document_units
   │
   ├── chat
   │     question + bounded conversation history
   │       → standalone question rewrite
   │       → Dense / BM25 / Hybrid retrieval
   │       → optional Reranker
   │       → DeepSeek
   │       → answer + source-ID citations
   │
   └── citation preview / conversations / retrieval trace

eval
   └── isolated corpus + Chroma + SQLite
         → Dataset v3 validation
         → retrieval / answer / citation metrics
         → report and review packet
```

核心目录：

```text
backend/                  FastAPI、数据模型和 RAG 服务
backend/services/parsers/ structured-v1 五格式 Adapter
frontend/                 Streamlit 界面和引用预览
eval/                     数据集、隔离评测 CLI、历史结果
tests/                    unit / integration / external 测试
scripts/rebuild_index.py  安全索引重建
docs/                     设计、操作指南和项目说明
```

## 文档结构与定位

所有格式使用同一套结构模型，但保留格式自身的定位语义：

| 格式 | locator | 主要结构 |
|---|---|---|
| PDF | `page` | 真实页、段落 |
| DOCX | `virtual_page` | 标题路径、段落、列表、表格 |
| TXT | `virtual_page` | 固定物理行窗口、段落 |
| Markdown | `section` | 标题路径、段落、列表、引用、代码块 |
| PPTX | `slide` | 标题、文本框、组合形状、列表、表格 |

前端统一使用 `location_label` 展示来源，不把逻辑页、幻灯片或章节错误地称为“页码”。
SQLite 中保存的结构 JSON 在读取时会重新经过 Pydantic 校验；损坏或版本不兼容的数据会被拒绝。

当前不支持 Excel、旧版 PowerPoint/Excel、扫描 PDF OCR、图表理解、SmartArt 和演讲者备注。

## 环境准备

当前环境已在 Windows、Python 3.14.0 上验证。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，至少设置：

```dotenv
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
RETRIEVAL_TRACE_ENABLED=false
```

Embedding 和 Reranker 都以 `local_files_only=True` 加载。运行前需要确保以下模型已在
Hugging Face 本地缓存：

- `BAAI/bge-large-zh-v1.5`；
- 启用 `RERANKER_ENABLED=true` 时需要 `BAAI/bge-reranker-v2-m3`。

BGE large 输出 1024 维向量，旧的 384 维索引不能直接复用。文档结构、Embedding 模型或
chunk 规则变化后，应使用 `scripts/rebuild_index.py` 在独立目录安全重建，不要直接覆盖
现有索引。

## 启动

开发时分别启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --reload-dir backend
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py
```

或使用一键脚本：

```powershell
.\scripts\start.ps1
```

只有需要后端热重载时才使用：

```powershell
.\scripts\start.ps1 -Reload
```

访问地址：

- 前端：http://localhost:8501
- API 文档：http://localhost:8000/docs

`start.ps1` 使用独立隐藏进程，并在后端健康检查通过后再启动前端。停止服务时优先使用
启动输出中的 Backend/Frontend PID：

```powershell
Stop-Process -Id <Backend PID>,<Frontend PID>
```

如果上传中断，查看 `logs/backend.error.log`。失败文件不会因页面刷新而自动重试，需要
在前端显式点击“重试失败文件”。

## 文档生命周期

每次上传都会创建独立 `document_id`：

```text
临时文件
  → SQLite processing 记录
  → structured-v1 parse
  → document_units
  → chunk / embed
  → Chroma
  → 原子移动为 document_id.ext
  → SQLite ready
```

同名文件会成为两个独立版本。失败时会清理临时文件、结构化 unit 和可能写入的向量，
同时保留 `failed` 记录用于排查。删除文档时按 `document_id` 级联清理关联数据。

## 测试

### 本地完整测试

默认测试不会调用 DeepSeek，也不会写入生产 `data/`：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

2026-07-30 在 `phase1/unified-structure-model` 上的结果：

```text
344 passed, 3 skipped, 0 failed
```

其中包含 29 项使用本地 Embedding/Reranker 和隔离向量库的 integration 测试。

不依赖本地模型权重的 CI 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q
```

### 真实 DeepSeek 测试

`tests/test_llm.py` 包含基于上下文回答、无关问题拒答和流式输出三项真实 API 测试。
当前 `tests/conftest.py` 会为隔离测试覆盖 `DEEPSEEK_API_KEY`；在该问题修复前，可在明确
允许外部调用后，仅对这个测试文件禁用公共 conftest：

```powershell
.\.venv\Scripts\python.exe -m pytest --noconftest tests/test_llm.py -m external -q -s
```

2026-07-30 使用 `.env` 中配置的 `deepseek-v4-flash` 实测为 `3 passed`。

## Dataset v3

Dataset v3 使用单一顶层 JSON、独立 corpus manifest 和 `structured-v1` parser。当前包括：

- 35 道单题；
- 6 组对话、17 个 dialogue turn；
- 43 个可回答项、9 个不可回答项；
- 80 条确定性定位的 evidence；
- PDF、DOCX、TXT、Markdown、PPTX 共 23 份语料。

非严格校验用于检查结构、manifest 和证据绑定：

```powershell
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json
```

严格校验只应在真实审核者完成全部签核后通过：

```powershell
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json `
  --strict
```

当前非严格校验通过；严格校验按设计失败，因为所有审核组仍为 `draft`，不能自动补写为
`verified`。

## v3 完整预检结果

2026-07-30 运行了完整 structured-v1 预检。该运行创建隔离 Chroma、SQLite 和上传目录，
并在用户明确授权后，将每题检索到的 Top-K 片段发送给 DeepSeek：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m eval.eval run `
  --label <label> `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json `
  --allow-draft-v3
```

`--allow-draft-v3` 表示结果只能用于 preflight，不能作为正式冻结评测。

运行规模：

| 项目 | 结果 |
|---|---:|
| 文档 | 23 |
| Parsed units | 2,438 |
| Chunks | 2,584 |
| Parser warnings | 0 |
| 评测项 | 52 |
| DeepSeek 调用 | 51 |
| 空检索短路 | 1 |
| 结构化入库耗时 | 703.11s |

自动指标：

| 指标 | 结果 |
|---|---:|
| Hit Rate | 100.00% |
| Recall | 100.00% |
| MRR | 97.67% |
| Keypoint Coverage | 67.67% |
| Strict Accuracy | 53.49% |
| Citation Precision | 97.67% |
| Citation Recall | 100.00% |
| Citation Emission Rate | 100.00% |
| False Refusal Rate | 0.00% |
| Refusal Accuracy | 100.00% |
| 不可回答题伪引用 | 1 |

延迟：

| 阶段 | Mean | P50 | P95 |
|---|---:|---:|---:|
| Retrieval | 5.674s | 5.557s | 7.976s |
| LLM | 1.793s | 1.701s | 2.977s |
| Total | 7.432s | 7.469s | 10.036s |

### 指标解释

本次 20 个自动 Strict Fail 均已逐项进行 AI 语义复核，未发现明显的核心答案遗漏。
主要误差来自当前评分器使用连续字符串匹配，无法稳定处理以下等价表达：

- Markdown 加粗、代码反引号和标点差异；
- `O(nlog₂n)` 与 `O(nlog2n)` 等符号差异；
- “队尾是允许插入的一端”与“在队尾插入”等同义改写；
- 表格答案中冒号、空格或解释性文字造成的短语不连续。

因此 `Keypoint Coverage` 和 `Strict Accuracy` 当前会系统性低估回答质量，不能单独作为
合并或发布阻断指标。该 AI 复核不替代 Dataset v3 要求的真实人工签核。

当前确认的回答或引用问题：

1. `D-001-q0005` 正确拒答，但仍输出了一个来源标记；
2. `D-001-d0001-t1/t2` 各引用了一个额外的非 gold 文档；
3. CPU Reranker 是当前检索延迟的主要来源。

本次本地报告保存在
`eval/runs/codex-v3-full-preflight-20260730-211947/`。`eval/runs/` 默认被 Git 忽略，
避免把 API 原始回答和临时索引误提交到仓库。

## 历史评测证据

旧 Dataset v1/v2 和 P1/P2 实验结果继续保留，用于追溯历史决策，不作为当前
structured-v1 的主验收集，也不应与 Dataset v3 指标直接比较。

| 阶段 | 结论 | 证据目录 |
|---|---|---|
| Baseline | MiniLM、固定滑窗的初始基线 | `eval/runs/baseline/` |
| P1-1 | 选择 BGE large，无 query instruction | `eval/results/p1-1-bge/` |
| P1-2 | 保守文本清洗和 DOCX 表格原序解析 | `eval/results/p1-2-docx-table/` |
| P1-3 | 选择 token-aware recursive 400/80 | `eval/results/p1-3-recursive-chunking/` |
| P1-4 | 选择 cosine distance threshold 0.67 | `eval/results/p1-4-threshold-calibration/` |
| P1-5 | 启用候选数 15 的 BGE Reranker | `eval/results/p1-5-reranker/` |
| P2-1 | Hybrid 未超过 Dense + Reranker，未部署 | `eval/results/p2-1-hybrid-retrieval/` |
| P2-3 | 检索 Trace 完成受控验收 | `eval/results/p2-3-retrieval-trace/` |
| v3 structured preflight | 五格式统一结构和检索预检 | `eval/results/v3-structured-preflight/` |

完整评测命令、指标定义、隔离约束和历史数据集说明见 `eval/README.md`。

## 已知限制与后续工作

当前后续工作只记录未完成的验收或明确问题，不再保留已经完成的迁移计划：

1. 由真实审核者完成 Dataset v3 的问题、答案、证据和不可回答项签核；
2. 改进 keypoint 规范化或引入可靠的语义评分，减少自动误判；
3. 明确拒答时的引用策略，并消除不可回答题伪引用；
4. 检查多轮对话中的额外文档引用；
5. 评估 Reranker 加速方案，降低 CPU 检索延迟；
6. 处理 FastAPI lifespan、PyPDF2、Starlette TestClient 等依赖弃用警告。
