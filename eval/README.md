# RAG 评估基准使用文档（面向 AI / 人类通用）

> 本文档自足：按此文档即可运行评估、解读结果，无需阅读 eval/ 源码。
> 让 AI 跑评估时，直接说「跑 eval，label 叫 <名字>」或「对比 <A> 和 <B>」即可。

## 运行命令

**必须**：在仓库根目录 `D:\ProgramFiles\RAG` 下、用项目 venv 的 Python 运行。

```bash
# 完整评估（默认 v2 的 27 题，最多 27 次 DeepSeek 调用；空检索会短路）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval run --label <标签名>

# 对比两次运行（差值表 + 回归题清单，不消耗 API）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval compare \
    eval/runs/<A>/report.json eval/runs/<B>/report.json [-o 输出.md]

# 冒烟测试（只跑前 2 题，验证链路，改动 eval 代码后先跑这个）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval run --label smoke --limit 2 --overwrite

# 基于已有真实 retrieval 扫描阈值（不调用 DeepSeek）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval threshold-sweep \
    eval/runs/<标签名>/raw_answers.jsonl --label <扫描标签> \
    --thresholds 0.60 0.61 0.62 0.63 0.64 0.65 0.66 0.67 0.68 0.69 0.70 \
    --output-dir eval/runs/<扫描标签>

# 一次入库后比较多个 Reranker 候选数（不调用 DeepSeek）
HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe \
    -m eval.eval retrieval-sweep --label <扫描标签> --candidate-ks 15 20 30

# 一次入库后执行 Dense / BM25 / Hybrid 五组消融（不调用 DeepSeek）
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONIOENCODING=utf-8 \
    ./.venv/Scripts/python.exe -m eval.eval hybrid-ablation --label <实验标签>
```

可选参数：
- `--dataset <路径>`：覆盖数据集；`run`、`threshold-sweep`、`retrieval-sweep` 和
  `hybrid-ablation` 默认均使用 `questions_v2.json`
- `--overwrite`：同名 label 已有报告时必须加，否则拒绝运行（防覆盖历史数据）
- `--embedding-model <名>`：临时换 embedding 模型（模型必须已在 HuggingFace 本地缓存，
  embedding.py 是 `local_files_only=True`——这是刻意的，本机到 HF 网络不稳定，勿去掉；
  下载新模型用 `HF_ENDPOINT=https://hf-mirror.com huggingface-cli download <模型名>`）
- `--query-instruction <文本>`：仅在 query 编码前添加 instruction；传空字符串表示不添加，
  文档侧始终不添加
- `--top-k <N>`：临时改检索数量
- `--retrieval-mode dense|bm25|hybrid`：正式评测时临时切换召回模式
- `--distance-threshold <值>`：临时覆盖 Chroma cosine distance 阈值（越小越严格）
- `--bm25-top-k / --bm25-k1 / --bm25-b`：覆盖 BM25 候选数及参数
- `--rrf-k <N>`：覆盖 Hybrid 模式的 RRF 排名平滑常数
- `--reranker` / `--no-reranker`：临时开启或关闭 Cross-encoder 精排
- `--reranker-model <名>`：临时覆盖 Reranker 模型（必须已在本地缓存）
- `--reranker-candidate-k <N>`：精排前的 Dense 候选数量
- `--limit <N>`：只跑前 N 题

## label 命名约定

与优化步骤对应，方便回溯：`baseline`（已提交，勿覆盖）、
`p1-1-bge-large-noinst`、`p1-1-bge-large-inst`、`p1-2-docx-table`、
`p1-3-recursive-600-100`…… 一次改多项时合并命名。

## 产物（eval/runs/<label>/，除 baseline 报告外均被 gitignore）

| 文件 | 用途 |
|---|---|
| `report.md` | 汇总指标表 + 分类别表 + **失败案例明细**（gold证据 vs 实际检索 vs 实际引用）|
| `report.json` | 机器可读，compare 的输入 |
| `review.md` | 人工评审文档（每题：问题/系统回答/标准答案/证据原文），整体贴给网页端 AI 可批量判分 |
| `raw_answers.jsonl` | 每题原始回答+完整检索结果，逐题落盘，调试用 |
| `chroma/` | 本次运行的隔离向量库，可整目录删除 |

`threshold-sweep` 额外生成 `threshold-sweep.json` 和 `threshold-sweep.md`，分别用于机器读取
和人工审查；其中明确区分阈值诱发误拒答率与不可回答题进入 LLM 比例。

`retrieval-sweep` 只执行 parse/chunk/embed/retrieve，一次入库后为每个候选数生成
`retrieval-k<N>.jsonl`，并汇总为 `retrieval-sweep.json` 和 `retrieval-sweep.md`；它不会
调用 DeepSeek，适合先确定候选数，再用正式 `run --reranker` 验证回答与引用指标。

四个评测命令默认均使用 `questions_v2.json`。`hybrid-ablation` 一次入库后固定执行 Dense、BM25、Hybrid、
Hybrid + Reranker、Dense + Reranker 五组对照。原始结果写入 `retrieval-<模式>.jsonl`，汇总为
`hybrid-ablation.json` 和 `hybrid-ablation.md`。最后一组是当前生产链路的同数据集控制组，
用于判断 Hybrid + Reranker 是否真的值得部署。

## P2-3 检索 Trace 验收产物

检索 Trace 是聊天接口的受控调试能力，不改变 `eval run`、`retrieval-sweep` 或
`hybrid-ablation` 的输出。代码和 `.env.example` 默认设置
`RETRIEVAL_TRACE_ENABLED=false`；开启后聊天响应会包含未入选候选的完整 Chunk，禁止在
不可信环境中启用。

真实生产索引的 P2-3 验收证据固定保存在 `eval/results/p2-3-retrieval-trace/`：

| 文件 | 用途 |
|---|---|
| `trace-response.json` | 真实 `/api/chat` 响应，含完整 Trace、阶段候选与耗时 |
| `streamlit-trace.png` | 前端默认关闭、展开、阶段切换和完整 Chunk 展示的验收截图 |
| `validation.md` | 逐项断言、真实时延和安全结论 |

## 指标解读

| 指标 | 指标含义 | baseline 参考值 |
|---|---|---|
| hit_rate | 检索 top-k 中至少命中 1 条 gold 证据的可回答题占比 | 41.2% |
| recall | 每道可回答题命中的 gold 证据占该题全部 gold 证据的比例，再取平均 | 32.4% |
| mrr | 每道可回答题首个命中 chunk 的排名倒数，再取平均 | 18.7% |
| keypoint_coverage | 回答命中的关键点组占全部关键点组的比例，再取平均 | 57.7% |
| strict_accuracy | 全部关键点命中且未拒答的可回答题占比 | 41.2% |
| citation_precision | 有效引用文档中属于 gold 文档的比例，再取平均 | 50.0% |
| citation_recall | 被有效引用的 gold 文档占全部 gold 文档的比例，再取平均 | 5.9% |
| citation_emission_rate | 包含至少一个引用标记的可回答题占比 | 11.8%（baseline 旧引用协议） |
| refusal_accuracy | 不可回答题中被正确拒答的题目占比 | 100% |
| false_refusal_rate | 可回答题中被判定为拒答的题目占比，越低越好 | 41.2% |
| unanswerable_citation_fp | 不可回答题中出现引用标记的题目数量，越低越好 | 0 |

**噪声界**：LLM temperature=0.3，回答类指标（keypoint/strict/citation）单次波动 <5% 视为噪声；
检索类指标（hit_rate/recall/mrr）完全确定，任何变化都真实。

## 已知设计要点（改动相关代码前必读）

1. **q11/q12 是表格金丝雀**：证据在 docx 表格里。P1-2 已将表格按原顺序转为 Markdown，
   两题分别以第 2/第 1 名命中；后续 parser 改动必须保持这两题命中。
2. **引用指标解析原始回答文本中的 `【来源N】`**，并按 N 映射本次 retrieval。
   2026-07-17 的 P0-1 已移除 citation fallback：无引用标记时 ChatResponse.citations 为空；
   不存在的来源编号会被评估为无效引用。baseline 报告仍保留旧协议的 11.8% 作为历史起点。
3. **环境隔离机制**：eval.py 在 import 任何 backend 模块前设置 `CHROMA_PERSIST_DIR`、
   `METADATA_DB_PATH` 等环境
   变量（backend 的 Chroma/Embedding/LLM 客户端都是模块级单例，import 即初始化）。
   若 config.py 的 `load_dotenv` 被改为 `override=True`，隔离会失效——eval 启动时有断言兜底。
4. **若未来实施 Milvus 迁移，eval 需同步适配**：当前 P2-2 因混合检索收益门槛未通过而
   有意跳过，生产仍使用 Chroma。迁移时 `adapters.py` 的 `_get_store()` 应优先 import
   `backend.services.milvus_store`，不存在则回退 `vector_store`。迁移时的配套清单：
   - 新 store 保持 `add_chunks(List[DocumentChunk]) -> int` 签名；
   - `retrieve()` 返回契约不变：`[{"content", "metadata"{document_name, page_number}, "distance"}]`，
     注意 Milvus 返回相似度（越大越好）而 Chroma 是距离（越小越好），适配层必须统一语义，
     否则 retriever 的 `distance <= RETRIEVAL_DISTANCE_THRESHOLD` 方向会反；
   - Milvus Lite 的数据文件路径必须从环境变量读取（如 `MILVUS_LITE_PATH`），不能写死——
     同时更新 eval.py 的 `_setup_env`（注入隔离路径）和 adapters.py 的 `assert_isolation()`，
     否则 eval 会写进生产库；
   - 迁移本身不换算法，`compare baseline p0-2-milvus` 检索指标应基本持平（这就是迁移验收）。
5. **检索命中判定对切分策略鲁棒**：用 文档名 + snippet 归一化包含（NFKC+去空白+小写），
   不用页码。改数据集时 snippet 必须 ≤30 字、单句内、语料内唯一（启动时自动校验，重复即报错退出）。
6. **拒答短语表**在 `eval/metrics.py` 的 `REFUSAL_PHRASES`——若换 LLM 或大改 prompt 后
   发现拒答判定异常（看 raw_answers.jsonl 抽查），在此校准。

## 标准工作流（每步优化后）

```bash
# 1. 跑新 label
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval run --label p0-1-bge
# 2. 与 baseline 对比，重点看「回归题」清单是否为空
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval compare \
    eval/runs/baseline/report.json eval/runs/p0-1-bge/report.json
# 3. 有回归 → 打开新 label 的 report.md 失败案例段 + raw_answers.jsonl 定位原因
# 4. 回答质量存疑 → 把 review.md 交给人工/网页端 AI 评审
```

## 数据集与语料

- 语料：`eval/corpus/` 4 篇中文文档（docx×2、pdf、txt），已随 git 固化，勿随意改动
  （改动会使历史 run 不可比）。
- 历史 v1 数据集：`eval/dataset/questions.json` 21 题——fact 6、numeric 4、table 2、
  multi_doc 3、procedural 2、unanswerable 4（2 域外 + 2 域内相邻陷阱题 q20/q21）。
- 默认 legacy regression：`eval/dataset/questions_v2.json` 保留上述 21 题并追加 6 道
  lexical/精确术语题，用于观测 BM25 与 Hybrid 的增量；跨版本报告不能直接计算回归。
- 若增删题目：bump `version` 字段，并注意历史 report 的 `dataset_version` 不同则不可直接对比。
