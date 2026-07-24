# RAG 下一阶段优化：Chat 窗口操作交接指南

> 更新日期：2026-07-24  
> 面向对象：项目维护者，以及在普通 Chat 窗口中提供逐步指导的 AI。  
> 工作方式：由 AI 解释并指导，用户亲自执行命令；一次只推进一个可验证步骤。

## 一、交接目标

后续不要求 AI 在后台直接修改项目。Chat 中的 AI 应当：

1. 先解释当前步骤解决什么问题；
2. 给出一小组可复制的 PowerShell 命令；
3. 说明命令会读取或修改哪些文件；
4. 说明正常情况下应看到什么输出；
5. 等用户执行并贴回输出；
6. 根据真实输出判断下一步，不假设命令已经成功；
7. 遇到不理解的地方允许用户随时提问。

普通 Chat 窗口通常不能自动读取本地仓库。开始新对话时，应上传本文档和总设计文档，或把
相关段落、代码和终端输出复制到对话中。不要让 Chat 假装已经看过未提供的本地文件。

是否消耗同一类额度取决于实际产品和账号规则，本文不对此作保证；选择 Chat 的主要目的，是
采用用户可见、逐步确认的操作方式。

## 二、当前仓库状态

项目路径：

```text
D:\ProgramFiles\RAG
```

当前主项目目录已经签出：

```text
feature/rag-next-stage
```

当前提交：

```text
d47627c docs: record phase 0 evaluation evidence
```

`master` 仍停留在：

```text
5d2a2a3 docs: define next-stage RAG optimization design
```

功能分支现在跟踪 `origin/feature/rag-next-stage`，本地和远端跟踪引用都停在
`d47627c`。Git reflog 显示远端跟踪引用于 2026-07-24 17:34:28 通过 push 更新；本交接文档
没有执行或授权新的 push，当前未提交的 Markdown 修改也尚未进入远端。隐藏 worktree：

```text
D:\ProgramFiles\RAG\.worktrees\rag-next-stage
```

仍然存在，但已经处于 detached HEAD，不再占用 `feature/rag-next-stage`。其中的
`eval/runs/phase0-prechange-retrieval-v2/` 是被 Git 忽略的隔离评测运行产物。

用户自己的未跟踪文件：

```text
.claude/settings.local.json
```

不得修改、删除或提交该文件。

本轮为了让用户先审阅，没有自动提交以下文档改动：

```text
M  docs/superpowers/specs/2026-07-23-rag-next-stage-optimization-design.md
?? docs/交接_RAG下一阶段优化操作指南.md
?? docs/测试题集候选数据生成提示词.md
```

它们是有意保留的文档修改，不是实现代码或数据集变更。用户审阅后再决定是否提交。

## 三、已经做过什么

功能分支相对 `master` 有 5 个提交：

```text
df967ef docs: capture phase 0 RAG baseline
2300438 fix: default evaluation commands to dataset v2
a2f4367 fix: normalize markdown and powerpoint document names
51a993e fix: generalize evaluation source locations
d47627c docs: record phase 0 evaluation evidence
```

具体内容：

- 记录现有 4 份语料、21 题 v1、27 题 v2 和历史报告的 hash；
- 记录未来一次性清理的精确路径，但没有删除任何数据；
- 将四个评测命令的默认数据集统一为现有 `questions_v2.json`；
- 评测文档名匹配预先识别 `.md` 和 `.pptx`；
- 评测报告可以显示 `location_label`、幻灯片位置和旧页码；
- 删除没有重复实验依据的“LLM 单次波动小于 5%”说法；
- 新增 10 个相关单元测试；
- 完成 `questions_v2` 的无 LLM 检索基线。

明确没有做：

- 没有新增、删除或修改 `eval/dataset/` 中的题目；
- 没有新增、删除或修改 `eval/corpus/` 中的文档；
- 没有实现 MD parser；
- 没有实现 PPTX parser；
- 没有实现结构化来源预览；
- 没有实现多轮对话；
- 没有清空或重建生产 SQLite/Chroma；
- 阶段 0 执行结束时没有自动推送或合并 `master`；之后远端功能分支已更新到 `d47627c`，
  `master` 仍未合并。

## 四、为什么先做格式支持，再制作新测试集

新测试语料预计会包含真实课件，尤其是 PPTX 和 MD。如果先批量生成题目 JSON，之后才改变
parser，容易出现：

- evidence snippet 在新 parser 文本中找不到；
- 标题或列表的文本表示变化；
- 幻灯片编号、章节路径、unit ID 和 offset 需要重算；
- 同一题的证据位置需要人工返工；
- 不可回答题可能因为新增文档而失效。

因此正式顺序调整为：

```text
保留现有 v2 工程回归
  → 统一结构模型
  → 实现 PDF/DOCX/TXT/MD/PPTX adapter
  → 用 3–5 份真实课件验收解析
  → 完成结构化来源预览并确认引用定位
  → 收集 20–30 份正式语料
  → 先运行 corpus prepare 和解析检查
  → 项目外生成候选 JSON
  → 项目内补齐 locator/offset/hash
  → 人工逐题审核
  → 冻结 Dataset v3
```

用户可以提前收集候选文档，但在 MD/PPTX 解析验收前，不建议批量生成正式 JSON。

## 五、下一阶段边界

下一阶段只做“统一结构模型与格式输入”，不同时实现预览、多轮对话或 Dataset v3。

目标格式：

- PDF
- DOCX
- TXT
- MD
- PPTX

MD 支持：

- 标题
- 段落
- 列表
- 引用
- 代码块
- 表格

PPTX 支持：

- 原生标题占位符
- 普通文本框
- 项目列表
- 表格
- 幻灯片编号

不支持：

- 图片 OCR
- 扫描文档识别
- 图表数据理解
- SmartArt 语义
- 动画
- 字体和颜色复刻
- 演讲者备注
- 旧 `.ppt`
- Excel

阶段 1 通过条件：

1. 五种格式均输出统一 `ParsedUnit`；
2. 标题、段落、列表和表格有明确 block 类型；
3. `canonical_text[start:end]` 能恢复 block 和 chunk 原文；
4. PPTX 使用“第 N 张幻灯片”，不伪装为页码；
5. DOCX/PPTX 有 ZIP entry 数和解压后容量限制；
6. 所有测试使用隔离 SQLite、Chroma 和 uploads；
7. 不读取旧索引，不清理生产数据；
8. 至少一份真实 MD 和一份真实 PPTX 通过人工解析检查。

## 六、Chat 指导协议

Chat 中的 AI 必须遵守以下规则。

### 6.1 每一步的固定格式

每次只给一个小步骤，并按以下顺序说明：

```text
目的：
将要读取的文件：
将要修改的文件：
命令：
预期输出：
失败时怎么处理：
是否会影响数据：
```

用户执行后贴回输出。没有输出时，Chat 不得继续推断。

### 6.2 Git 规则

- 开始前先确认分支；
- 修改前先确认工作区状态；
- 不把 `.claude/` 加入 Git；
- 不使用 `git reset --hard`；
- 不使用 `git checkout -- <file>` 丢弃用户改动；
- 不自动合并 `master`；
- 不自动推送 GitHub；
- 每次提交前展示 `git diff --stat` 和 `git status --short`；
- 用户确认后再提交；
- 一次提交只包含一个可说明的主题。

### 6.3 数据安全规则

在阶段 2 切换前，禁止删除以下生产数据：

```text
D:\ProgramFiles\RAG\data
```

不得运行泛化的递归删除命令。未来确需清理时，必须：

1. 列出每个绝对路径；
2. 确认都位于 `D:\ProgramFiles\RAG\data`；
3. 再次确认新结构在隔离目录验收通过；
4. 用户明确同意；
5. 逐项处理并记录结果。

### 6.4 密钥规则

- 不把 `DEEPSEEK_API_KEY` 粘贴到 Chat；
- 不提交 `.env`；
- Chat 只检查密钥“存在或缺失”，不打印具体值；
- 没有真实密钥时，不用 mock 或占位回答冒充效果评测。

## 七、下次 Chat 对话的第一轮操作

第一轮只检查现场，不修改文件。

在 PowerShell 中运行：

```powershell
Set-Location 'D:\ProgramFiles\RAG'
git branch --show-current
git status --short --branch
git log --oneline master..HEAD
git diff --name-status master...HEAD
```

预期：

```text
当前分支是 feature/rag-next-stage
相对 master 有 5 个阶段 0 提交
总体设计文档和新交接文档是待审阅、未提交的文档修改
测试题集候选数据生成提示词是待审阅、未提交的新文档
.claude/settings.local.json 仍是未跟踪文件
eval/dataset 和 eval/corpus 没有改动
```

如果结果不同，停止并先定位差异，不直接开始阶段 1。

然后阅读：

```text
docs/superpowers/specs/2026-07-23-rag-next-stage-optimization-design.md
docs/superpowers/baselines/2026-07-24-phase-0-baseline.md
docs/superpowers/plans/2026-07-24-rag-next-stage-phase-0-baseline.md
docs/交接_RAG下一阶段优化操作指南.md
```

## 八、阶段 1 的推荐操作顺序

以下是要讨论并写成详细实施计划的顺序，不是要求一次性执行的命令。

### 8.1 先映射现有代码

只读检查：

- `backend/services/parser.py`
- `backend/services/chunker.py`
- `backend/models/schemas.py`
- `backend/services/page_store.py`
- `backend/routers/upload.py`
- `eval/adapters.py`
- `tests/test_parser.py`
- `tests/test_chunker.py`
- `tests/test_page_store.py`
- `requirements.txt`

输出一张文件职责表，指出复用点和必须替换的旧 `dict` 契约。

### 8.2 编写阶段 1 详细计划

计划保存为：

```text
docs/superpowers/plans/2026-07-24-rag-next-stage-phase-1-formats.md
```

计划至少拆为：

1. `ParsedBlock`、`LocatedBlock`、`SourceLocator`、`ParsedUnit`；
2. deterministic unit ID 和 canonical text 组装；
3. PDF adapter；
4. DOCX adapter；
5. TXT adapter；
6. MD adapter；
7. PPTX adapter；
8. ZIP 安全限制；
9. `document_units` SQLite；
10. chunk 与 unit offset 联调；
11. upload/eval 接缝；
12. 真实 MD/PPTX 解析检查。

每个任务先写失败测试，再写最小实现，再跑测试；不要在同一提交中混入预览 UI 或多轮对话。

### 8.3 工程 fixture 与真实文档分开

自动测试使用小型、确定性的 fixture：

- `sample.md`
- `sample.pptx`

fixture 只覆盖必要结构，便于精确断言，不要求用户先找正式语料。

真实验收先使用 3–5 份文档：

- 至少 1 份含多级标题和列表的 MD；
- 至少 1 份含标题、文本框、项目列表和表格的 PPTX；
- 可再加入 1 份 PDF、1 份 DOCX、1 份 TXT 做跨格式回归。

真实文档不直接作为单元测试 fixture 提交，除非许可、体积和隐私都适合。

### 8.4 格式验收后再扩大语料

阶段 1–2 通过后，用户再集中准备：

- 20–30 份真实文档；
- 以课件为主；
- 尽量不使用 AI 生成文档；
- 不包含隐私、内部机密或版权不允许存入仓库的材料；
- 文件名稳定，避免后续随意改名；
- 记录来源、许可和文档类型。

建议先导入 5 份试运行，再批量导入剩余文档。

## 九、正式新测试集生产流程

格式支持验收后：

```text
1. 用户整理正式语料
2. 项目生成 corpus manifest 和解析检查报告
3. 修正无法解析或结构异常的文档
4. 项目外让 AI 生成候选 questions/dialogues JSON
5. AI 可改写问题，但不得改变 evidence 和答案
6. 项目内 dataset prepare 定位 snippet
7. 零命中或多命中题退回修改
8. 用户逐题核对 evidence、答案和 keypoint
9. 标记 verified
10. 按 question_group 划分 60/20/20
11. 冻结 test
12. 使用 development/validation 调整，test 只用于阶段验收
```

目标规模：

- 20–30 份文档；
- 150–200 个样本；
- 约 70% 可回答；
- 约 30% 不可回答或信息不足；
- 8–10 组多轮对话；
- 20–30 个多轮 turn。

任何自动校验都不能替代人工确认答案是否真的被 evidence 支持。

## 十、当前未解决事项

1. 根项目和当前进程没有 `DEEPSEEK_API_KEY`，真实 27 题 LLM 基线尚未运行。
2. 该基线不阻塞格式工程，但必须在检索参数调优和最终质量对比前补跑。
3. 全量测试仍产生约 2 万条依赖弃用 warning，需要单独治理，不能全局屏蔽。
4. 远端功能分支目前停在 `d47627c`；本轮待审阅的 Markdown 修改尚未提交或推送。
5. 阶段 1 的详细实施计划尚未编写。
6. 新测试文档和 Dataset v3 尚未开始收集或生成。

## 十一、查看和回退方法

查看当前改动：

```powershell
git diff --stat master...feature/rag-next-stage
git diff master...feature/rag-next-stage -- eval/eval.py
git log --oneline --decorate --graph --all -15
```

查看某个提交：

```powershell
git show --stat 2300438
git show 2300438
```

如果只想回到 `master` 查看旧版本，必须先确认没有未提交改动：

```powershell
git status --short
git switch master
```

不要使用 `git reset --hard`。需要重新回到功能分支时：

```powershell
git switch feature/rag-next-stage
```

## 十二、复制到新 Chat 的开场提示

可以把下面内容连同本文档一起交给新 Chat：

```text
我要继续本地 RAG 项目的下一阶段优化，但请不要替我假设命令已经执行，也不要一次给出大量操作。

请先阅读我提供的《RAG 下一阶段优化：Chat 窗口操作交接指南》和整体设计文档。之后采用指导模式：

1. 每次只给一个小步骤；
2. 先说明目的、读取/修改的文件、命令、预期输出、风险和失败处理；
3. 等我亲自执行并贴回输出后，再决定下一步；
4. 不修改或提交 .claude/ 和 .env；
5. 未经我确认，不删除 data、不合并 master、不推送 GitHub；
6. 先完成阶段 1 的详细实施计划，不直接写功能代码；
7. 阶段 1 先扩充 MD/PPTX 格式支持，再制作新的正式测试集；
8. 如果你无法直接读取我的本地文件，请明确让我粘贴需要的内容，不要假装已经读取。

第一步请指导我检查当前分支、工作区状态和相对 master 的提交，不要修改文件。
```

## 十三、重要文档索引

- 总设计：`docs/superpowers/specs/2026-07-23-rag-next-stage-optimization-design.md`
- 阶段 0 基线：`docs/superpowers/baselines/2026-07-24-phase-0-baseline.md`
- 阶段 0 计划：`docs/superpowers/plans/2026-07-24-rag-next-stage-phase-0-baseline.md`
- 测试题候选生成提示词：`docs/测试题集候选数据生成提示词.md`
- 本交接指南：`docs/交接_RAG下一阶段优化操作指南.md`
- 旧阶段交接：`docs/交接_阶段一优化.md`

旧交接文档记录历史优化背景，但下一阶段顺序和边界以总设计及本指南为准。
