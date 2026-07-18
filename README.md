# 中文企业知识库 RAG

一个以“可评估、可追溯、可复现”为核心的中文 RAG 项目。系统支持上传 PDF、DOCX、TXT，完成解析、切分、向量检索和 DeepSeek 问答，并通过稳定的 `【来源N】` 协议返回可落到真实 chunk 的引用。

当前工作重点不是堆叠功能，而是用固定黄金集、单变量实验和回归报告逐步提高中文检索质量。

## 当前架构

```text
Streamlit
   │ HTTP
   ▼
FastAPI
   ├── upload: file → parser → chunker → embedding → Chroma
   │                         └── SQLite document registry
   └── chat: query → retrieve → context → DeepSeek → source ID citations

eval
   └── isolated corpus/index → 21 questions → metrics/report/compare
```

核心目录：

```text
backend/                 FastAPI、数据模型和 RAG 服务
frontend/app.py          Streamlit 界面
eval/                    黄金集、隔离评估 CLI、报告生成
tests/                   unit/integration/external 测试
docs/交接_阶段一优化.md   当前阶段交接和施工约束
```

## 已有能力

- PDF、DOCX、TXT 解析；
- Token-aware 中文递归切分和 Chroma 余弦检索；
- DeepSeek OpenAI-compatible API；
- 基于 source ID 的可信引用，不使用 Top-K 伪引用 fallback；
- 稳定 `document_id`、版本化同名上传、SQLite 文档登记；
- 失败入库清理和按 document ID 删除；
- 21 题中文评估集、分类指标、失败案例和 run compare；
- 测试数据与生产 Chroma/SQLite/上传目录隔离；
- 真实外部 API 测试默认跳过。

## Baseline

当前 baseline 使用 `all-MiniLM-L6-v2`、500/50 字符滑窗、Top 5 检索。

| 指标 | 值 |
|---|---:|
| Hit Rate | 41.2% |
| Recall | 32.4% |
| MRR | 18.7% |
| Keypoint Coverage | 57.7% |
| Citation Emission Rate（旧引用协议） | 11.8% |
| Refusal Accuracy | 100% |
| False Refusal Rate | 41.2% |
| 平均总延迟 | 约 2.03s |

完整口径见 `eval/README.md` 和 `eval/runs/baseline/report.md`。baseline 的引用数据使用历史旧协议；新 run 使用 `【来源N】` 可信引用协议。

## P1-1 中文 Embedding 实验

保持 500/50 字符滑窗、Top 5、阈值和 parser 不变，对
`BAAI/bge-large-zh-v1.5` 做 query instruction A/B。最终选择 **noinst**：

| 指标 | baseline | BGE large noinst | 变化 |
|---|---:|---:|---:|
| Hit Rate | 41.18% | 82.35% | +41.17pp |
| Recall | 32.35% | 76.47% | +44.12pp |
| MRR | 18.73% | 69.61% | +50.88pp |
| Strict Accuracy | 41.18% | 82.35% | +41.17pp |
| False Refusal Rate | 41.18% | 11.76% | -29.42pp |
| Refusal Accuracy | 100% | 100% | 持平 |

noinst 与 inst 的 Hit Rate、Recall 相同；noinst 的 MRR 高 2.94pp，检索平均延迟
为 0.067s（inst 为 0.089s），且没有 baseline 通过而新方案失败的回归题。
默认配置因此切换为 BGE large，query instruction 留空。

完整实验数据已固化在 `eval/results/p1-1-bge/`：两组 `report.md`、
`report.json` 及 baseline/noinst/inst 对比报告均受 Git 跟踪。

## P1-2 文本清洗与 DOCX 表格

保持 BGE noinst、500/50、Top 5 和阈值不变，只增加保守空白清洗、DOCX 段落/表格
原序解析、表格 Markdown 化和 `content_type` 元数据：

| 指标 | P1-1 BGE noinst | P1-2 DOCX table | 变化 |
|---|---:|---:|---:|
| Hit Rate | 82.35% | 94.12% | +11.77pp |
| Recall | 76.47% | 85.29% | +8.82pp |
| MRR | 69.61% | 75.49% | +5.88pp |
| Strict Accuracy | 82.35% | 82.35% | 持平 |
| Citation Precision | 94.44% | 100.00% | +5.56pp |
| False Refusal Rate | 11.76% | 0.00% | -11.76pp |
| Refusal Accuracy | 100.00% | 100.00% | 持平 |

表格金丝雀 q11/q12 分别以第 2/第 1 名命中，且没有普通题从检索命中退化为未命中。
完整报告和 P1-1 对比保存在 `eval/results/p1-2-docx-table/`。

## P1-3 Token-aware 递归切分

保持 P1-2 parser、BGE noinst、Top 5 和阈值不变，比较 recursive 400/80、600/100、
800/120，使用 BGE tokenizer 将单 chunk 硬上限设为 480 tokens。最终选择 400/80：

| 指标 | P1-2 fixed 500/50 | P1-3 recursive 400/80 | 变化 |
|---|---:|---:|---:|
| Hit Rate | 94.12% | 94.12% | 持平 |
| Recall | 85.29% | 88.24% | +2.95pp |
| MRR | 75.49% | 81.37% | +5.88pp |
| Keypoint Coverage | 90.00% | 94.12% | +4.12pp |
| Strict Accuracy | 82.35% | 94.12% | +11.77pp |
| Citation Precision | 100.00% | 97.92% | -2.08pp |
| False Refusal Rate | 0.00% | 5.88% | +5.88pp |
| Refusal Accuracy | 100.00% | 100.00% | 持平 |

新切分器按段落、句子、子句、字符兜底的层级选择边界，保留 overlap，并记录稳定
chunk ID、逻辑页 `start_char/end_char`、字符数、token 数和 `content_type`。正式评估恢复
q02，并改善 q08、q16；已知回归是 q05 的黄金证据被同文档更高相似块挤出最终 Top 5。
完整离线候选、正式报告和 P1-2 对比保存在
`eval/results/p1-3-recursive-chunking/`。

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
```

Embedding 使用 `local_files_only=True`。运行前需确保
`BAAI/bge-large-zh-v1.5` 已存在于本地 Hugging Face 缓存；这样服务启动和测试不会
因网络检查随机失败。该模型输出 1024 维向量，旧 384 维 Chroma 索引不能直接复用，
切换现有环境前必须重建索引或重新上传文档。

已有 SQLite 文档登记和原始上传文件时，使用安全重建脚本迁移到独立目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.rebuild_index `
  --source-index-dir data/chroma_db `
  --target-dir data/chroma_bge_large_zh_v1_5 `
  --model BAAI/bge-large-zh-v1.5 `
  --expected-dimension 1024
```

校验成功后，再把 `.env` 中的 `EMBEDDING_MODEL` 和 `CHROMA_PERSIST_DIR` 一起切到新模型
与新目录。脚本不会覆盖旧索引或非空目标目录；旧索引可保留到人工回归完成后再清理。
parser 变化导致 chunk 数合理变化时，显式增加 `--accept-chunk-count-change`；脚本只会在
新索引完成数量、document_id 和维度校验后，事务同步 SQLite 登记。

## 启动

开发时分别启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --reload-dir backend
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py
```

或运行：

```powershell
.\scripts\start.ps1
```

一键脚本默认使用稳定模式，并将服务输出写入 `logs/`。只有需要边改后端边自动
重启时才开启热重载：

```powershell
.\scripts\start.ps1 -Reload
```

`start.ps1` 使用独立隐藏进程，脚本结束后 `Ctrl+C` 不会停止服务。优先使用启动输出中的
Backend/Frontend PID：

```powershell
Stop-Process -Id <Backend PID>,<Frontend PID>
```

若已找不到 PID，可按项目端口关闭：

```powershell
Get-NetTCPConnection -LocalPort 8000,8501 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ }
```

脚本会等待后端健康检查通过后再启动前端，避免页面先出现、后端尚未就绪的竞态。
如果当前 PowerShell 执行策略禁止直接运行 `.ps1`，使用一次性绕过命令（不会修改
系统执行策略）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

上传时若出现连接中断，先查看 `logs/backend.error.log`；失败文件不会被前端标记
为已处理，也不会因普通页面刷新而自动重复提交，需点击“重试失败文件”。
当前选择全部入库成功后，上传框会自动清空；存在失败项时则保留选择和重试入口。

访问：

- 前端：http://localhost:8501
- API 文档：http://localhost:8000/docs

## 文档生命周期

每次上传生成独立 `document_id`：

```text
临时文件
  → SQLite processing 记录
  → parse/chunk/embed
  → Chroma 写入（metadata 含 document_id）
  → 原子移动为 document_id.ext
  → SQLite ready
```

同名文件会成为两个独立版本，不再依赖原始文件名生成 chunk ID。失败时清理临时文件和可能写入的向量，并保留 `failed` 记录用于排查。

## 测试

默认测试不会调用 DeepSeek，也不会写 `data/`：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

真实 API 测试必须显式开启：

```powershell
.\.venv\Scripts\python.exe -m pytest -m external --run-external -q
```

不依赖本地模型权重的 CI 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q
```

## 评估

完整评估会创建隔离 Chroma、SQLite 和上传目录，但会调用 DeepSeek：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m eval.eval run --label <label>
```

对比两个 run：

```powershell
.\.venv\Scripts\python.exe -m eval.eval compare `
  eval/runs/baseline/report.json `
  eval/runs/<label>/report.json
```

每次只改变一个主要变量，label 与优化步骤对应。详细规则见 `eval/README.md`。

## 下一阶段

1. Token-aware 递归切分；
2. 检索阈值校准；
3. Reranker；
4. Dense + BM25 + RRF 混合检索。

所有简历指标都以实际 eval 报告为准，不预先填写目标值。
