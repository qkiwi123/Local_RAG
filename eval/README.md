# RAG 评估基准使用文档（面向 AI / 人类通用）

> 本文档自足：按此文档即可运行评估、解读结果，无需阅读 eval/ 源码。
> 让 AI 跑评估时，直接说「跑 eval，label 叫 <名字>」或「对比 <A> 和 <B>」即可。

## 运行命令

**必须**：在仓库根目录 `D:\ProgramFiles\RAG` 下、用项目 venv 的 Python 运行。

```bash
# 完整评估（21 题，约 17 次 DeepSeek 调用，约 1 分钟）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval run --label <标签名>

# 对比两次运行（差值表 + 回归题清单，不消耗 API）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval compare \
    eval/runs/<A>/report.json eval/runs/<B>/report.json [-o 输出.md]

# 冒烟测试（只跑前 2 题，验证链路，改动 eval 代码后先跑这个）
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m eval.eval run --label smoke --limit 2 --overwrite
```

可选参数：
- `--overwrite`：同名 label 已有报告时必须加，否则拒绝运行（防覆盖历史数据）
- `--embedding-model <名>`：临时换 embedding 模型（模型必须已在 HuggingFace 本地缓存，
  embedding.py 是 `local_files_only=True`；未缓存会报错，需先联网下载一次）
- `--top-k <N>`：临时改检索数量
- `--limit <N>`：只跑前 N 题

## label 命名约定

与优化步骤对应，方便回溯：`baseline`（已提交，勿覆盖）、`p0-1-bge`、`p0-3-chunk`、
`p1-1-rerank`、`p1-2-hybrid`…… 一次改多项时合并命名如 `p0-all`。

## 产物（eval/runs/<label>/，除 baseline 报告外均被 gitignore）

| 文件 | 用途 |
|---|---|
| `report.md` | 汇总指标表 + 分类别表 + **失败案例明细**（gold证据 vs 实际检索 vs 实际引用）|
| `report.json` | 机器可读，compare 的输入 |
| `review.md` | 人工评审文档（每题：问题/系统回答/标准答案/证据原文），整体贴给网页端 AI 可批量判分 |
| `raw_answers.jsonl` | 每题原始回答+完整检索结果，逐题落盘，调试用 |
| `chroma/` | 本次运行的隔离向量库，可整目录删除 |

## 指标解读

| 指标 | 含义 | baseline 参考值 |
|---|---|---|
| hit_rate | 检索 top-k 命中至少 1 条 gold 证据的题占比（核心指标，确定性）| 41.2% |
| recall | 每题命中证据数/总证据数均值（多文档题看这个）| 32.4% |
| mrr | 首个命中 chunk 排名倒数的均值 | 18.7% |
| keypoint_coverage | 回答命中关键点组的比例（辅助信号，最终以 review.md 人工评审为准）| 57.7% |
| strict_accuracy | 全部关键点命中且未拒答的题占比 | 41.2% |
| citation_emission_rate | 回答中带 `【文档名，第X页】` 标记的占比 | 11.8% |
| refusal_accuracy | 4 道不可回答题的正确拒答率 | 100% |
| false_refusal_rate（越低越好）| 可回答题被误拒答的占比（通常是检索未命中所致）| 41.2% |

**噪声界**：LLM temperature=0.3，回答类指标（keypoint/strict/citation）单次波动 <5% 视为噪声；
检索类指标（hit_rate/recall/mrr）完全确定，任何变化都真实。

## 已知设计要点（改动相关代码前必读）

1. **q11/q12 是表格金丝雀**：证据在 docx 表格里，当前 parser 不解析表格，这两题检索必然
   不命中——做完 P3-4 表格提取后应转为命中。数据集校验时对它们报「snippet 未出现」警告属预期。
2. **引用指标解析原始回答文本**，不用 ChatResponse.citations（citation.py 的 fallback 会用
   检索 top3 伪造引用）。baseline 已发现：LLM 实际输出 `【来源N - ...】` 格式（照抄上下文
   标签），与正则不匹配 → 引用输出率低是真实系统缺陷（P3-5 优化点）。
3. **环境隔离机制**：eval.py 在 import 任何 backend 模块前设置 `CHROMA_PERSIST_DIR` 等环境
   变量（backend 的 Chroma/Embedding/LLM 客户端都是模块级单例，import 即初始化）。
   若 config.py 的 `load_dotenv` 被改为 `override=True`，隔离会失效——eval 启动时有断言兜底。
4. **P0-2 迁移 Milvus 后 eval 自动适配**：adapters.py 的 `_get_store()` 优先 import
   `backend.services.milvus_store`，不存在则回退 `vector_store`。迁移时的配套清单：
   - 新 store 保持 `add_chunks(List[DocumentChunk]) -> int` 签名；
   - `retrieve()` 返回契约不变：`[{"content", "metadata"{document_name, page_number}, "distance"}]`，
     注意 Milvus 返回相似度（越大越好）而 Chroma 是距离（越小越好），适配层必须统一语义，
     否则 retriever 的 `distance <= 1.5` 阈值方向会反；
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
- 数据集：`eval/dataset/questions.json` 21 题——fact 6、numeric 4、table 2、multi_doc 3、
  procedural 2、unanswerable 4（2 域外 + 2 域内相邻陷阱题 q20/q21）。
- 若增删题目：bump `version` 字段，并注意历史 report 的 `dataset_version` 不同则不可直接对比。
