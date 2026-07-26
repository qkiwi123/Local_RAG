# RAG 下一阶段整体优化设计

> 状态：已由用户于 2026-07-24 确认，并于同日根据实施反馈修订顺序。
> 阶段 0 的工程基线和评测入口修复已经完成；新测试集尚未收集或生成。

## 1. 背景与目标

当前项目已经具备一条可复用的本地 RAG 主链路：

```text
Streamlit
  → FastAPI
  → parser
  → chunker
  → Embedding / Chroma / 可选 BM25 与 Reranker
  → DeepSeek
  → 稳定来源编号
  → 解析页原文预览与 chunk 高亮
```

已有能力包括稳定 `document_id`、版本化同名上传、SQLite 文档登记、确定性 chunk
定位、检索 Trace、可信来源编号、隔离评测环境和自动报告。下一阶段不重写这些能力，也不迁移
向量数据库，而是在现有边界上完成四类改进：

1. 将效果评测从 4 份文档、27 道题扩展为更可信的通用测试集；
2. 在 PDF、DOCX、TXT 基础上增加 MD 和 PPTX；
3. 将引用预览从纯文本升级为标题、段落、列表和表格等语义结构预览；
4. 实现可持久化的多轮对话，刷新页面或重启应用后仍可恢复。

本设计的核心原则是：

- 复用现有 embedding、检索、引用、Trace、评测 CLI 和前端交互；
- 用明确的数据模型替代继续扩张的隐式 `dict` 契约；
- 先保证证据可定位、派生数据可重建和结果可回归，再增加格式或界面；
- 先完成 MD/PPTX 解析契约，再基于真实解析结果制作包含这些格式的新黄金测试集；
- 不因功能增加而预设检索质量一定提高，所有质量结论以冻结评测集为准。

## 2. 已确认的范围

### 2.1 本轮包含

- 文档格式：`.pdf`、`.docx`、`.txt`、`.md`、`.pptx`；
- DOCX：标题、段落、列表、表格；
- MD：标题、段落、列表、引用、代码块、表格；
- PPTX：原生标题占位符、文本框、项目列表和表格；
- PDF：页和段落；标题只能作为非强制的保守推测；
- TXT：虚拟页和段落；
- 来源预览：结构化重排、高亮引用范围；
- 对话：本地单用户、SQLite 持久化、历史会话管理、追问改写；
- 评测：项目外生成候选 JSON，项目内严格校验、运行和报告。

### 2.2 本轮不包含

- Excel `.xlsx`、旧 PowerPoint `.ppt`、旧 Excel `.xls` 和 `.xlsm`；
- 图片 OCR、扫描件识别、图表理解、SmartArt 语义、PPTX 演讲者备注；
- Word、PowerPoint 或 PDF 原始视觉版式复刻；
- 字体、颜色、动画、页眉页脚和像素级坐标；
- 用户账号、登录、权限和多租户隔离；
- 向量数据库迁移；
- 将测试数据生成 AI 集成进项目；
- 用历史助手回答替代当前文档证据。

Excel 不是永久放弃。统一来源定位模型预留 `sheet_name` 和 `cell_range` 扩展字段，但本轮不实现、
不添加依赖，也不设置 Excel 验收项。

## 3. 方案选择

采用“渐进式统一结构模型”：

- 各格式 parser 统一输出强类型 `ParsedBlock`；
- 同一页、幻灯片或章节的 blocks 组装为 `ParsedUnit`；
- chunker 只面对统一的规范文本与位置映射；
- SQLite 同时保存规范文本和结构化 blocks；
- Chroma 继续只保存检索所需文本及轻量定位 metadata；
- 现有 Chroma、旧 parser 页快照和旧 chunk metadata 不迁移、不双读；新版本从空索引启动；
- 多轮对话作为独立服务接入聊天路由，不与文档解析耦合。

不选择两种替代方案：

1. 不继续给现有 parser `dict` 零散添加字段，因为隐式字段、页码假设和 offset 规则会继续扩散；
2. 不引入重型通用解析框架，因为当前只需要基本语义结构，额外依赖、部署体积和解析漂移没有明确收益。

## 4. 目标架构

```text
                         ┌─────────────────────────────┐
真实文档 ──格式适配器──→ │ ParsedBlock[]               │
                         │ heading/paragraph/list/table │
                         └──────────────┬──────────────┘
                                        ↓
                         ┌─────────────────────────────┐
                         │ ParsedUnit                  │
                         │ 规范文本 + 结构范围 + 定位   │
                         └──────────┬──────────┬───────┘
                                    │          │
                           chunk + metadata    │
                                    ↓          ↓
                                 Chroma     SQLite
                                    │       document_units
                                    ↓          │
用户问题 → 会话历史 → 独立问题改写 → 检索 → LLM → 引用定位
              ↑                                      │
              └──── SQLite conversations/messages ───┘
                                                     ↓
                                          结构化来源预览
```

模块职责必须保持单一：

- parser adapter：忠实提取各格式的内容和结构；
- unit assembler：产生唯一规范文本和 block offset；
- chunker：按规范文本切分，不理解文件格式；
- source store：持久化解析版本、规范文本和结构；
- conversation store：持久化会话和消息；
- query contextualizer：把依赖上下文的追问改写成独立检索问题；
- eval validator：验证测试数据，不调用生产聊天逻辑；
- renderer：只负责安全展示服务端返回的结构。

## 5. 统一文档结构模型

### 5.1 `ParsedBlock`

新增内部模型，替代 parser 输出中的自由 dict：

```python
class ParsedBlock(BaseModel):
    block_id: str
    block_type: Literal[
        "heading", "paragraph", "list_item", "table", "code", "quote"
    ]
    text: str
    sequence: int
    heading_level: int | None = None
    list_level: int | None = None
    table_rows: list[list[str]] | None = None
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)
```

Parser adapter 只生成 `ParsedBlock`。Unit assembler 计算规范文本位置后，将其转换为：

```python
class LocatedBlock(ParsedBlock):
    start_char: int
    end_char: int
```

约束：

- `block_id` 由文档 ID、来源单元和顺序确定，不使用随机值；
- `text` 是用于构造规范文本的最终文本，不得在后续层再次隐式清洗；
- 标题级别限定为 1–6；
- 列表级别从 0 开始；
- 表格同时保留二维单元格和稳定的检索文本表示；
- `LocatedBlock` 使用半开区间，且必须满足
  `canonical_text[start_char:end_char] == block.text`；
- `attributes` 只存少量格式特有信息，不允许存任意二进制或完整原文件对象。

### 5.2 `SourceLocator`

统一来源位置，不再让所有格式假装只有“页码”：

```python
class SourceLocator(BaseModel):
    kind: Literal["page", "virtual_page", "slide", "section", "sheet"]
    unit_index: int
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    sheet_name: str | None = None
    cell_range: str | None = None
```

示例：

- PDF：`kind=page, page_number=3, location_label="第 3 页"`；
- DOCX：`kind=virtual_page, page_number=2, heading_path=["第二章", "安装"]`；
- TXT：`kind=virtual_page, page_number=4`；
- MD：`kind=section, unit_index=5, heading_path=["部署", "Windows"]`；
- PPTX：`kind=slide, slide_number=8, location_label="第 8 张幻灯片"`。

Chroma metadata 只接受轻量标量。`heading_path` 在写入 Chroma 时编码为 JSON 字符串，API
模型和 SQLite 中保持数组。`page_number` 作为 PDF、DOCX 和 TXT 的可选位置字段保留；
前端统一优先展示
`location_label`，不得再硬编码所有来源都是“第 X 页”。`sheet`、`sheet_name` 和
`cell_range` 只用于保证未来增加 Excel 时不需要改写引用与预览主契约；本轮所有 parser 都
不得生成 `kind=sheet`。

### 5.3 `ParsedUnit`

一个 unit 是可预览和可定位的最小来源容器：

```python
class ParsedUnit(BaseModel):
    unit_id: str
    document_id: str
    document_name: str
    parser_version: str
    locator: SourceLocator
    blocks: list[LocatedBlock]
    canonical_text: str
```

`canonical_text` 按 block 顺序以两个换行连接：

```python
canonical_text = "\n\n".join(block.text for block in blocks)
```

组装时为每个 block 计算 `[start_char, end_char)` 并生成 `LocatedBlock`。
SQLite `structure_json` 和预览 API 都只序列化 `LocatedBlock`，不保存没有 offset 的原始
`ParsedBlock`。chunker 直接切分
`canonical_text`，因此继续满足：

```python
unit.canonical_text[chunk.start_char:chunk.end_char] == chunk.content
```

与当前实现相比，chunk 可以覆盖标题和相邻正文，不会产生只有一个短标题的无效 chunk。
结构化预览也能根据 block 范围判断应该高亮标题、段落或表格区域。

## 6. 各格式解析规则

### 6.1 PDF

首期继续复用 PyPDF2，避免无依据地改变当前稳定文本基线：

- 每个物理页对应一个 `page` unit；
- 按空行保守拆成 paragraph blocks；
- 无文本页继续输出明确的“无可提取文本”提示；
- 不把字体大小或视觉位置当成可靠标题；
- 不承诺识别 PDF 标题、双栏阅读顺序、图片文字或表格结构。

因此 PDF 的结构化预览验收只要求“页 + 段落”，标题识别不作为通过条件。如果后续真实数据
证明 PDF 标题是核心需求，应另立布局解析实验，对 PyMuPDF 等候选做同语料回归后再决定是否
切换；不得在本阶段静默更换 PDF 文本引擎。

### 6.2 DOCX

继续使用 `python-docx`：

- 按文档 XML 顺序遍历段落和表格；
- 通过 outline level 和标题样式识别 heading；
- 普通段落输出 paragraph；
- 有编号属性的段落输出 list item，并保留层级；
- 表格保留二维 cells，同时生成确定性 Markdown 检索文本；
- 继续使用虚拟页，但 heading path 跨虚拟页延续；
- 合并单元格沿用当前去重策略。

DOCX 的虚拟页只用于内部 unit 划分，不向用户伪装为 Word 的真实渲染页。UI 应显示“逻辑页”
或优先显示标题路径。

### 6.3 TXT

- 按现有每 50 行虚拟分页规则保持兼容；
- 每个虚拟页再按空行拆成 paragraphs；
- 不推测标题层级；
- 保留当前 UTF-8 输入约束；其他编码属于后续独立需求。

### 6.4 Markdown

增加 `.md` adapter，使用 CommonMark token parser，而不是用一个正则表达式解析全部语法：

- 支持 ATX/setext 标题；
- 支持段落、有序/无序列表及嵌套层级；
- 支持 blockquote、fenced code 和表格；
- 链接保留可见文字和 URL 文本；
- 图片只保留 alt 文本，不下载、不 OCR；
- front matter 默认作为 metadata，不送入检索正文；
- 以一级或二级标题章节作为优先 section unit；无标题文档按稳定字符上限分 unit；
- heading path 写入 locator。

### 6.5 PPTX

增加 `.pptx` adapter，使用 `python-pptx`：

- 每张幻灯片对应一个 slide unit；
- 标题占位符输出 heading；
- 文本框按形状的上、左位置和原始顺序生成稳定阅读顺序；
- text frame 中的段落输出 paragraph 或 list item，并保留项目层级；
- 原生表格输出 table；
- 跳过空 shape、备注、图片、图表和 SmartArt；
- 只有图片而没有原生文字的幻灯片生成解析警告，但不伪造文本。

上传成功响应和文档列表应包含非致命解析警告数量。用户可以看到“3 张幻灯片未提取到原生
文本”，从而知道知识库不是完整理解了文件。

## 7. 存储、版本和干净重置

### 7.1 Parser 版本

当前 `PARSER_VERSION="text-v1-pypdf2"` 同时服务 PDF、DOCX 和 TXT，名称已经无法准确表达
跨格式语义。旧 parser 产物不迁移，新结构模型从版本 1 开始：

```text
structured-v1
```

版本代表规范文本、block 结构和 offset 语义。只要清洗、block 连接、阅读顺序或表格文本表示
改变，就必须升级版本并重建新索引。

### 7.2 新表 `document_units`

旧 `document_pages` 不进入新数据模型。新数据库只创建统一来源表：

```sql
CREATE TABLE document_units (
    document_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    locator_kind TEXT NOT NULL,
    location_label TEXT NOT NULL,
    canonical_text TEXT NOT NULL,
    structure_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, parser_version, unit_id),
    FOREIGN KEY (document_id)
        REFERENCES documents(document_id) ON DELETE CASCADE
)
```

`structure_json` 保存经过 Pydantic 校验后的 locator 和 block 列表。读取时必须再次校验 schema，
不能把数据库 JSON 直接当成前端 HTML。

### 7.3 新数据库版本

不为旧 metadata.db 编写迁移。新环境从空数据库创建完整 schema，并增加：

```sql
CREATE TABLE schema_meta (
    schema_version INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
)
```

首版写入固定 schema version。服务发现数据库非空但版本不受支持时直接拒绝进入可写状态，
不猜测或原地修改未知结构。未来真正需要保留业务数据时，再为新 schema version 单独设计迁移。

### 7.4 一次性清空与重建

项目已经确认保留以下独立评测资产：

- `eval/corpus/` 中的 4 份测试文档；
- `eval/dataset/questions.json`；
- `eval/dataset/questions_v2.json`；
- `eval/results/` 中已经固化的历史报告。

这些文件不依赖生产 `data/` 数据库。生产 Chroma、metadata.db 和 uploads 清空后，eval 仍能
在 `eval/runs/<label>/` 下创建隔离 Chroma、SQLite 和 uploads，重新运行完整评测。

清空生产数据必须放在阶段 2 的结构化解析、preview API 和前端 renderer 全部通过隔离测试
之后，不能在仍运行旧 schema 的阶段 0 或只有解析后端的阶段 1 提前执行。一次性切换步骤为：

1. 在临时测试目录完成 `structured-v1` schema、五种格式解析、上传、检索、引用和预览测试；
2. 确认 `eval/corpus`、dataset JSON 和已保存报告存在；
3. 停止前后端，避免 Chroma 或 SQLite 文件仍被占用；
4. 只读列出 `data/` 下实际存在的旧 Chroma 目录、metadata.db、备份和 uploads；
5. 核对 `.env` 中当前生产路径，明确本次删除清单；
6. 一次性清空旧生产 Chroma、metadata.db、数据库备份和旧 uploads；
7. 将配置统一恢复为新的空 `data/chroma_db`、`data/metadata.db`、`data/uploads`；
8. 启动新代码，由 schema bootstrap 创建 `structured-v1` 空数据库；
9. 完成空库启动、真实文档上传、检索、引用和结构预览冒烟。

新 `source_store` 只读取：

```text
document_units(document_id, parser_version, unit_id)
```

不存在 `document_pages` 回退、parser version 双读或旧 offset 猜测。发现非
`structured-v1` chunk 时按无效索引处理，并提示清空重建。

这是一项一次性项目操作，不开发通用 reset 命令。执行时必须解析并核对每个绝对路径，只能
删除 `D:\ProgramFiles\RAG\data` 下已经列出的目标；拒绝 symlink、junction、reparse point、
工作区根、`data/` 根和任何 `eval/` 路径。删除前再次向用户展示最终清单。由于旧数据已确认
无价值，本阶段不实现备份、双写、双读、原子索引指针或旧索引回滚。

## 8. 结构化来源预览

### 8.1 API

保留现有预览端点路径，扩展成功响应：

```json
{
  "document_id": "doc-id",
  "chunk_id": "chunk-id",
  "parser_version": "structured-v1",
  "target_unit_id": "unit-id",
  "location": {
    "kind": "slide",
    "unit_index": 8,
    "location_label": "第 8 张幻灯片",
    "slide_number": 8,
    "heading_path": ["RAG 评测"]
  },
  "units": [
    {
      "unit_id": "unit-id",
      "location": {
        "kind": "slide",
        "unit_index": 8,
        "location_label": "第 8 张幻灯片",
        "slide_number": 8
      },
      "canonical_text": "规范文本",
      "blocks": []
    }
  ],
  "highlight_start": 10,
  "highlight_end": 80
}
```

预览响应按原始顺序返回整份文档的 `units`，每个 unit 包含 `canonical_text` 和结构化
`blocks`；高亮 offset 只作用于 `target_unit_id`。不保留旧 `parsed_page_text` 回退，也不再
重复返回或展示 `chunk_content`。`page_number` 仅在 locator 对应页或逻辑页时出现。

服务端继续执行强一致性校验：

1. 文档存在且为 ready；
2. chunk 属于 URL 中的 document ID；
3. chunk parser 版本与 unit 一致；
4. locator 和 unit ID 一致；
5. offset 有效；
6. `canonical_text[start:end] == chunk.content`。

### 8.2 前端

预览对话框按 unit 原始顺序展示整份文档，并按 block 类型安全渲染：

- heading：按 1–6 级显示；
- paragraph/quote：保持段落和换行；
- list item：按层级缩进；
- table：使用二维 cells 渲染表格；
- code：使用代码块；
- 只有目标 unit 中与引用范围相交的 block 高亮交集，其余内容完整展示但不高亮。

所有文档文字必须先 HTML 转义。不得直接把 `structure_json`、Markdown 或原文作为
`unsafe_allow_html=True` 内容。表格单元格同样按普通文本处理。

结构化预览不加载原始文件，不返回文件系统路径，不把全部文档结构塞进 `/api/chat`。只有
用户点击引用时才按需加载对应 unit。

## 9. 评测集扩容与可信流程

### 9.1 数据规模

第一阶段目标暂定：

- 20–30 份真实公开文档或人工编写文档；
- 150–200 个经过人工核对的评测样本；
- 语料以容易获取的真实课件为主，并混入少量讲义、说明书或报告；
- 覆盖 PDF、DOCX、TXT、MD 和 PPTX；
- 可回答题约 70%，不可回答或信息不足题约 30%；
- 60% development、20% validation、20% frozen test；
- 同一知识点的原问和改写问属于同一 `question_group`，必须进入同一个 split；
- 其中保留 20–30 个 turn，组成 8–10 组多轮对话，用于单独评测追问改写。

比例是初始目标，不作为无法调整的硬编码。正式报告必须显示实际分布，样本不足的类别不得
给出过度概括的结论。

### 9.2 数据生产流程

测试数据不在项目内自动生成。由于正式语料可能包含 MD 和 PPTX，格式支持和稳定定位必须
先于正式候选 JSON 的制作完成：

```text
项目内完成 PDF/DOCX/TXT/MD/PPTX 的统一解析与定位
  → 用 3–5 份真实课件做格式试验和解析验收
  → 完成结构化来源预览，用真实界面确认结构可读和引用可定位
  → 项目外收集 20–30 份正式真实文档
  → 项目内先做纯文档 prepare，确认每份文档可解析并生成 corpus manifest
  → 项目外让 AI 生成候选题、证据、关键点和标准答案
  → AI 第二轮只改写问题，不改证据与答案
  → 候选 JSON 放入 eval 候选目录
  → 项目内 dataset prepare 根据已验收解析产物补齐 unit/offset/hash
  → 人工逐题核对补齐后的证据
  → 项目内严格校验 verified 数据
  → 冻结语料清单和 split
  → 复用 eval CLI 运行
```

AI 输出只能视为候选数据。每道正式题必须人工确认：

- evidence 来自指定原文；
- 标准答案没有超出 evidence；
- 每个 keypoint 都有证据支持；
- 问题改写没有改变含义；
- 不可回答题在整个对应 corpus 中都没有答案；
- 文档名、位置和 snippet 可以稳定定位。

真实文档的收集可以在阶段 1 开发期间提前开始，但在 MD/PPTX parser 和 locator 验收前，
不建议批量生成正式题目 JSON。工程开发使用小型确定性 fixture，不要求用户先准备完整的
20–30 份语料。

项目外候选 JSON 不要求 AI 猜测 `parser_version`、`unit_id`、offset 或 hash。项目内新增
`dataset prepare` 命令，根据文档名和 snippet 在确定性解析产物中解析这些派生字段：

- 唯一命中时写入派生字段，但 review 仍保持 `draft`；
- 零命中或多位置命中时输出错误，要求人工补充 locator 或修改 snippet；
- prepare 不生成问题、不修改标准答案、不自动把 review 标为 verified；
- 人工核对完成后才可运行 strict validate 和正式 eval。

### 9.3 Dataset v3

Dataset v3 固定为一个顶层 JSON 文件，不在“数组或独立文件”之间保留二选一：

```json
{
  "schema_version": 3,
  "dataset_id": "general-courseware-v1",
  "corpus_id": "general-courseware-v1",
  "parser_version": "structured-v1",
  "rubric_version": "rag-gold-v1",
  "questions": [],
  "dialogues": []
}
```

`questions` 保留当前字段并增加兼容字段：

```json
{
  "id": "g001",
  "question_group": "group-001",
  "split": "development",
  "question": "问题",
  "category": "fact",
  "difficulty": "medium",
  "document_types": ["pptx"],
  "answerable": true,
  "unanswerable_type": null,
  "evidence": [
    {
      "document_name": "课程课件.pptx",
      "snippet": "证据原文",
      "parser_version": "structured-v1",
      "unit_id": "courseware_u8",
      "start_char": 120,
      "end_char": 132,
      "snippet_sha256": "...",
      "locator": {
        "kind": "slide",
        "unit_index": 8,
        "slide_number": 8,
        "heading_path": ["RAG 评测"]
      }
    }
  ],
  "keypoints": [["关键点", "允许变体"]],
  "reference_answer": "标准答案",
  "review": {
    "status": "verified",
    "reviewer": "human-reviewer-1",
    "reviewed_at": "2026-07-23T12:00:00+08:00",
    "rubric_version": "rag-gold-v1",
    "evidence_checked": true,
    "answer_checked": true,
    "whole_corpus_checked": false
  }
}
```

`unanswerable_type` 的取值固定为：

- 可回答题：`null`；
- 完全不属于语料范围：`out_of_scope`；
- 语料含相近主题但缺少答案所需信息：`insufficient_detail`。

不可回答题的 `review.whole_corpus_checked` 必须为 `true`。

`dialogues` 中每个对象固定包含：

```json
{
  "dialogue_id": "dialogue-001",
  "question_group": "dialogue-001",
  "split": "validation",
  "difficulty": "hard",
  "document_types": ["pptx"],
  "turns": [
    {
      "turn_id": "dialogue-001-t1",
      "question": "第一轮问题",
      "standalone_question": "第一轮问题",
      "answerable": true,
      "unanswerable_type": null,
      "evidence": [],
      "keypoints": [],
      "reference_answer": "标准答案"
    },
    {
      "turn_id": "dialogue-001-t2",
      "question": "它的第二个优点是什么？",
      "standalone_question": "RAG 评测方法的第二个优点是什么？",
      "answerable": true,
      "unanswerable_type": null,
      "evidence": [],
      "keypoints": [],
      "reference_answer": "标准答案"
    }
  ],
  "review": {
    "status": "verified",
    "reviewer": "human-reviewer-1",
    "reviewed_at": "2026-07-23T12:00:00+08:00",
    "rubric_version": "rag-gold-v1",
    "evidence_checked": true,
    "answer_checked": true,
    "whole_corpus_checked": false
  }
}
```

示例中的空 evidence/keypoints 只为缩短展示；正式可回答 turn 仍必须满足与单题相同的非空
约束。后续 turn 的 `standalone_question` 是追问改写 gold，用于直接评测改写是否保持原意。
一个 dialogue 作为一个 question group，不允许跨 split。

### 9.4 语料冻结与隔离

新增 corpus manifest：

```json
{
  "corpus_id": "general-courseware-v1",
  "parser_version": "structured-v1",
  "parsed_corpus_sha256": "...",
  "documents": [
    {
      "file": "课程课件.pptx",
      "sha256": "...",
      "document_type": "pptx",
      "parsed_units_sha256": "..."
    }
  ]
}
```

运行前验证文件、原文件 hash、parser 版本和解析 units hash 一致，防止同名文件内容不变但
parser 升级后 locator/offset 已漂移，仍继续沿用旧 gold。

解析产物 hash 使用唯一规范化算法：

1. hash 对象只包含 corpus 相对文档名、`parser_version`、locator、LocatedBlock 的语义字段
   和 `canonical_text`，排除随机 upload document ID、数据库时间和运行目录；
2. hash 序列化显式剔除可能含随机 document ID 的 `unit_id` 和 `block_id`；unit 在 hash
   对象中规范化为 `u{unit_index}`，block 规范化为 `b{sequence}`；
3. 文档内 units 按 `unit_index` 排序，unit 内 blocks 按 `sequence` 排序；
4. JSON 使用 UTF-8、无 BOM、LF 换行、key 字典序、紧凑分隔符和 `ensure_ascii=false`；
5. 每个 unit 的规范 JSON 后追加一个 LF，按顺序拼接后计算 `parsed_units_sha256`；
6. corpus 按 manifest 文件名排序，将
   `file + NUL + parsed_units_sha256 + LF` 拼接后计算 `parsed_corpus_sha256`。

Windows 路径在 manifest 中统一使用相对 corpus 根目录的 `/` 分隔符。不同操作系统对相同
解析内容必须得到相同 hash。

黄金 evidence 使用的 `unit_id` 也不能来自生产随机上传 ID。Dataset prepare 按
`corpus_relative_path + ":u" + unit_index` 生成稳定 eval unit ID；生产索引继续使用真实
document ID 生成 unit/chunk ID，两者不得混用。

现有 `questions_v2.json` 和 4 份原语料保留为 `legacy-regression-v2`，不得直接把新文档加入
旧语料池。原因是新文档可能包含 q18–q27 中原本不存在的信息，使旧不可回答题失效。新通用
数据集使用独立 corpus、Chroma、SQLite 和 run 目录。

### 9.5 自动校验

数据工具分为 `prepare` 和 `validate --strict`。前者只补齐可确定计算的来源字段，后者验证
最终黄金集。当前 `_validate_dataset()` 对 snippet 找不到只警告；v3 strict 模式改为失败，
并增加：

- dataset、question 和 group ID 唯一；
- 必填字段、枚举和类型合法；
- 同一 group 不跨 split；
- manifest 文件和 hash 匹配；
- dataset 与 manifest 的 `corpus_id/parser_version` 一致；
- `parsed_corpus_sha256` 与本次确定性解析结果一致；
- evidence 文档存在；
- evidence parser 版本与 dataset 一致；
- `unit_id` 和 locator 指向实际 unit；
- `[start_char:end_char)` 精确等于 snippet；
- snippet hash 一致；
- snippet 多次出现时必须由 locator 唯一消歧；
- answerable 题至少一条 evidence 和一个 keypoint；
- unanswerable 题 evidence 必须为空；
- unanswerable 题必须提供合法 `unanswerable_type` 且完成 whole-corpus 人工核对；
- 所有正式题 `review.status=verified`；
- reviewer、reviewed_at 和 rubric version 完整；
- 分布和比例偏差输出报告，但不因轻微偏差阻塞；
- 开发集、验证集和冻结测试集分别运行，禁止 `--limit` 结果冒充正式报告。

自动校验只能证明数据结构和证据位置有效，不能自动证明答案逻辑完全正确；人工审核仍是
黄金集成立的必要条件。

### 9.6 指标与报告

继续复用 Hit Rate、Recall、MRR、keypoint coverage、strict accuracy、citation、拒答和延迟
指标，新增：

- 按 split、document type、difficulty、category 聚合；
- 按 `question_group` 聚合，避免大量改写题获得不合理权重；
- locator-level citation accuracy；
- 不可回答题的“完全域外”和“域内信息不足”分层；
- 多轮 standalone rewrite exact/keypoint coverage；
- 多轮回答的检索命中、引用和严格准确率；
- 对比例指标提供置信区间，对 MRR/Recall 提供按 question group bootstrap 区间；
- compare 报告同时显示总体变化和类别回归，不只显示单一平均值。

评测运行应使用独立的 LLM temperature 配置。当前报告中“temperature=0.3，单次波动小于
5%”没有重复实验依据，不应继续作为已确认事实。正式评测优先使用 temperature 0；若 API
行为仍不完全确定，则对冻结测试集重复运行并报告实测波动。

## 10. 持久化多轮对话

### 10.1 数据模型

在同一 metadata SQLite 中增加：

```sql
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'failed')),
    standalone_question TEXT,
    citations_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, sequence),
    UNIQUE (request_id, role),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE TABLE chat_requests (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    expected_last_message_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'failed')),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
);
```

首期不持久化 retrieval trace。Trace 体积较大、包含未入选候选原文，只保留在当前响应和当前
前端会话中。持久化 citation snapshot，使历史回答在文档删除后仍能显示当时的名称、位置和
摘录；原文预览必须重新检查当前文档是否仍存在。

### 10.2 API

新增：

- `POST /api/conversations`：创建会话；
- `GET /api/conversations`：按更新时间列出会话；
- `GET /api/conversations/{id}`：返回消息；
- `PATCH /api/conversations/{id}`：重命名；
- `DELETE /api/conversations/{id}`：级联删除；
- `POST /api/chat`：在兼容 `question` 的基础上接受 `conversation_id`、客户端生成的
  `request_id`、`expected_last_message_id` 和可选 `retry_failed`。

`ChatResponse` 增加 `conversation_id`、用户 `message_id`、助手 `message_id` 和
`standalone_question`。旧客户端未传 conversation ID 时继续执行当前单轮、非持久化行为，
不自动创建历史会话；新前端必须先创建或选择会话再调用聊天接口。该规则避免旧 API 调用在
升级后悄悄产生大量无法管理的会话记录。

首个标题直接由首条问题截断生成，不额外调用 LLM。

新前端调用持久化聊天时，`request_id`、`conversation_id` 必填；空会话的
`expected_last_message_id=null`，非空会话必须传当前最后一条 complete message ID。服务端
不接受客户端传入完整历史，历史始终从 SQLite 读取。

### 10.3 多轮检索流程

```text
原始追问
  → 读取同会话最近历史
  → 追问改写为独立问题
  → 用独立问题检索
  → 当前证据 + 必要历史 + 原始追问交给回答模型
  → 保存回答与引用
```

关键约束：

- 第一轮不执行改写；
- 后续轮次只在存在可用历史时改写；
- 检索使用 `standalone_question`；
- 回答界面展示用户原始问题，不用改写结果替换；
- 历史助手回答只用于解析指代和保持对话连贯，不是事实证据；
- 所有事实结论仍必须由本轮检索文档支持；
- 改写失败时回退原问题并记录非敏感诊断，不让整个聊天不可用；
- 空检索继续使用当前固定拒答行为。

### 10.4 历史窗口

增加可配置上限：

- 最近消息数；
- 历史字符或 token 数；
- 单条消息最大长度；
- 单会话最大消息数。

首期采用“最近若干完整轮次 + token 上限”的确定性裁剪，不实现长期记忆向量库，也不让模型
自动总结后覆盖原历史。达到会话上限时提示用户创建新会话。

### 10.5 一致性和并发

- 首次收到 `request_id` 时，在一个事务中验证 `expected_last_message_id`、确认会话没有其他
  pending 请求、插入 `chat_requests.pending` 和 `messages.user.pending`，并预留 sequence；
- LLM 成功后在一个事务中把 user 改为 complete、写入 assistant complete、把 request 改为
  complete，并更新会话时间；
- LLM 或持久化后半段失败时，把 user 和 request 都改为 failed，记录有限 error code，不把
  完整异常或文档内容写入数据库；
- 相同 `request_id` 再次到达时：complete 直接返回已保存响应，pending 返回 409
  `request_in_progress`，failed 只有 `retry_failed=true` 且该 user message 之后没有任何消息
  或请求时，才能重新进入 pending；
- 可重试的 failed 请求复用原 user message 和原 sequence，assistant 固定使用
  `user.sequence + 1`；新请求始终使用当前最大 sequence + 1；
- failed 请求之后一旦出现任何新消息，其重试返回 409 `stale_retry`。前端只能提供
  “作为当前会话末尾的新问题重新发送”，使用新 request ID 和新 sequence，不能插回历史；
- failed 消息可在 UI 显示，但不进入后续模型历史；用户可以显式删除整个会话，首期不支持
  在中间删除单条消息；
- 服务启动时把超过配置超时仍为 pending 的 request/user 原子标记为 failed
  `interrupted`，避免进程崩溃留下永久 pending；
- 同一会话同时提交两条不同请求时，后到请求因 pending request 或
  `expected_last_message_id` 不一致返回 409，而不是静默打乱；
- 幂等重放 complete 请求时从持久化 answer/citations 重建响应；由于 Trace 不持久化，
  重放响应的 `trace=null`；
- 删除会话使用外键级联；
- 服务重启后从数据库恢复，不依赖 Streamlit `session_state`；
- 当前会话 ID 保存在 URL query parameter 或可恢复的前端选择中，刷新后重新请求后端。

### 10.6 安全边界

首期是本地单用户/可信环境。没有认证时，任何能访问应用的人都能看到全部会话，不得宣传为
多用户安全系统。日志不得记录完整对话、文档原文或 API key。

## 11. 前端交互

侧栏增加：

- “新建对话”；
- 历史会话列表；
- 当前会话重命名和删除；
- 删除前确认；
- 文档列表继续独立存在。

主区域：

- 选择会话后从后端加载消息；
- 每条历史助手消息恢复回答和 citation snapshot；
- 当前文档仍存在时允许查看结构化来源；
- 来源已删除时显示“历史引用来源已删除”，不报通用连接错误；
- 请求进行中禁止同一会话重复提交；
- 失败消息显示重试入口；
- 清空当前对话改为删除或新建会话，避免只清空前端后刷新又恢复。

## 12. 错误处理与可观测性

### 12.1 文档解析

- 不支持的后缀：400；
- 文件过大：413；
- DOCX/PPTX 作为 ZIP 容器时校验 entry 数、解压后总大小和单个 XML 大小，拒绝 ZIP bomb；
- parser 不访问外部 relationship、远程图片或外部链接；
- 文件损坏或 parser 失败：422 或明确的处理失败响应；
- 部分 PPTX 幻灯片无文本：上传成功并返回 warnings；
- 整份文档无可索引文本：入库失败；
- unit/chunk 切片不一致：入库失败并补偿清理；
- 结构 JSON 无法通过 schema：不提供预览。

### 12.2 对话

- 会话不存在：404；
- 同会话并发冲突：409；
- 空问题：422；
- 改写失败：回退原问题；
- LLM 回答失败：502，并保留可重试状态；
- 持久化失败：不得返回“回答已保存”；错误必须与 LLM 错误区分。

### 12.3 诊断字段

Trace 可增加但默认隐藏：

- 原始问题；
- standalone question；
- 是否发生改写回退；
- 使用的历史消息数量和 token 数；
- 改写耗时；
- 检索和生成耗时。

不得在普通 API 日志输出历史全文。

文档内容和历史消息都属于不可信输入。System prompt 必须明确：文档或历史中出现的命令、
角色要求和“忽略此前规则”等文字只能被当作被引用内容，不能覆盖系统规则；历史助手回答也
不能被当成新的知识来源。该约束需要 prompt 单测和至少一条对抗性集成用例。

## 13. 测试策略

### 13.1 工程回归测试

所有功能按测试先行实现。自动化范围包括：

- `ParsedBlock`、`SourceLocator`、`ParsedUnit` 模型校验；
- 五种格式的 fixture 和解析顺序；
- DOCX 标题/列表/表格；
- MD 标题路径、列表、代码和表格；
- PPTX 标题、文本框顺序、列表层级、表格和无文本 warning；
- DOCX/PPTX ZIP entry、解压体积和外部 relationship 安全边界；
- unit 规范文本与 block/chunk offset 不变量；
- 新空数据库 bootstrap、schema version、事务和外键级联；
- 结构化预览不存在旧纯文本回退；
- 前端安全转义、标题/段落/列表/表格和高亮；
- dataset v3 schema、manifest hash、group split 和 locator 校验；
- 会话 CRUD、重启恢复、失败重试、并发冲突和删除级联；
- 第一轮不改写、追问改写、改写失败回退；
- 历史不作为事实来源的 prompt 契约；
- API 旧请求兼容；
- 全量 pytest、CI 子集和真实环境冒烟。

### 13.2 Parser 金丝雀

为每种格式保存小型、人工可检查的 fixture，并锁定：

- block 类型和顺序；
- locator；
- canonical text；
- chunk 数量或合理变化范围；
- 引用高亮位置。

解析器升级必须先更新 parser 版本，再明确审阅金丝雀差异；不得仅为让快照测试通过而批量接受
未知变化。

### 13.3 真实验收

至少完成：

1. 每种支持格式上传一份真实文档；
2. 用真实问题命中标题、正文、列表和表格；
3. 预览中结构正确且引用范围高亮；
4. PPTX 无文字幻灯片显示 warning；
5. 新建三轮对话，后两轮包含代词或省略；
6. 刷新页面后恢复；
7. 重启前后端后恢复；
8. 删除来源文档后历史引用仍显示 snapshot，但原文预览不可用；
9. 运行 legacy regression；
10. 运行新数据集 development/validation，冻结 test 只用于阶段验收。

## 14. 实施顺序与独立验收

本设计覆盖多个子系统，实施时应拆成独立计划和提交，不做一个超大改动。

### 阶段 0：基线确认

- 确认 `eval/corpus`、`questions.json`、`questions_v2.json` 和历史报告完整；
- 运行当前全量测试和 `questions_v2` 隔离评测，保存改造前结果；
- 修正默认 dataset、扩展名和报告位置假设；
- 记录 `.env` 和 `data/` 下当前实际生产路径，形成一次性清理清单；
- 锁定仍需保留的聊天、上传、检索和引用行为。

当前状态：

- 资产 hash、清理边界和行为契约已记录；
- 评测默认 dataset、MD/PPTX 文档名归一化和报告位置假设已修正；
- `questions_v2` 无 LLM 检索基线已经完成；
- 因本机没有 `DEEPSEEK_API_KEY`，真实 27 题 LLM 基线暂缓。

真实 LLM 基线不阻塞阶段 1 的格式工程，但必须在开始检索参数调优或最终质量对比前补跑。
旧行为仍可通过基线提交 `5d2a2a3` 重现，禁止用 mock 结果冒充效果基线。

### 阶段 1：统一结构模型与格式输入

- 实现 ParsedBlock/Unit/Locator；
- 将 PDF、DOCX、TXT 改造为统一 adapter；
- 增加 MD、PPTX；
- 写入 `document_units`；
- 在隔离临时目录通过 parser、unit、chunk、存储和检索测试。
- 先用确定性 fixture 测试，再由用户提供至少 1 份真实 MD 和 1 份真实 PPTX 做试验验收。

通过条件：五种格式在隔离环境可入库，所有 chunk 可从 unit 精确切片恢复；此时尚不清空或
切换生产数据。

### 阶段 2：真实格式验收与结构化来源预览

- 对 3–5 份真实课件生成解析检查报告，人工确认标题、段落、列表和表格；
- 扩展 preview API；
- 实现结构 renderer；
- 完成真实 UI 验收。

隔离测试通过后，按第 7.4 节一次性清空生产数据，从空数据库和空 Chroma 启动新系统。

通过条件：标题、段落、列表和表格可读，引用范围正确且无 HTML 注入；生产环境只包含
`structured-v1` 新数据。

### 阶段 3：Dataset v3 与扩容流程

- 实现 schema、manifest、validator、split 和报告；
- 保留 legacy suite；
- 用户在格式验收后集中准备 20–30 份真实文档；
- 文档先通过 corpus prepare 和解析检查，再在项目外生成候选 JSON；
- 人工核对后逐批导入；
- 先 development，再 validation，最后 frozen test。

通过条件：正式题全部 verified，语料 hash 固定，报告能分层展示且无 group 泄漏。

### 阶段 4：持久化多轮对话

- conversation/message store；
- 会话 API 和 UI；
- query contextualizer；
- 历史裁剪、失败恢复、并发控制；
- 多轮评测。

通过条件：追问检索正确，刷新和重启可恢复，历史不替代文档证据。

这个顺序允许文档收集在阶段 1–2 开发期间并行进行，但正式候选 JSON 的批量生成应等待
格式解析验收。任何 parser 或检索参数调优都只能使用 development/validation，不能反复查看
frozen test 后继续调参。

## 15. 代码审查中发现的附带问题

以下问题与本轮目标直接相关，建议纳入对应阶段，不做无关重构：

1. **评测默认数据集落后（阶段 0 已修复）。** `run`、`threshold-sweep`、
   `retrieval-sweep` 和 `hybrid-ablation` 现在统一默认 `questions_v2.json`。
2. **评测扩展名假设不完整（阶段 0 已修复）。** `norm_doc_name()` 已识别 MD/PPTX；真正的
   MD/PPTX parser 仍属于阶段 1。
3. **报告硬编码页码（阶段 0 已修复评测展示）。** 评测报告现在优先使用
   `location_label`，并支持幻灯片和旧页码 fallback；正式来源预览仍属于阶段 2。
4. **当前 dataset 校验过宽。** evidence snippet 找不到只警告；正式黄金集应失败。
5. **不可回答题会受语料扩容污染。** 如果新旧文档直接合并，旧题的 answerable 标签可能失效，
   必须按 corpus manifest 隔离。
6. **回答自动评分较脆弱。** 当前 keypoint 是子串命中，无法识别否定、语义相反或数字单位错误；
   继续保留该指标，但不能替代人工评审。
7. **拒答判定是短语启发式。** 回答中只要出现“未提及”等短语就可能被判为拒答，需增加失败
   案例审查或更严格的结构化判定。
8. **评测波动结论缺少证据（阶段 0 已修正文案）。** 新报告不再预设固定噪声阈值；后续需要
   真实重复运行才能给出波动范围。
9. **旧页表外键与既有设计不一致。** 当前 `document_pages` 创建时没有实际声明
   `documents(document_id)` 外键，删除依赖手工调用。本轮不修补该旧表，而是在清空后创建的
   `document_units` schema 中正确声明外键并覆盖级联测试。
10. **Parser 版本名过度绑定 PDF。** `text-v1-pypdf2` 无法表达 DOCX/TXT 及新格式的统一
    offset 语义，应在新结构版本中修正。
11. **测试告警噪声过大。** 当前 Python 3.14 + pytest-asyncio 收集测试时产生大量弃用告警，
    容易淹没真实 warning。应单独升级或收敛配置，但不能简单全局忽略未知 warning。
12. **压缩文档只有上传字节上限。** DOCX/PPTX 内部是 ZIP，50 MB 原文件上限不能防止高压缩
    比文件耗尽内存；新增格式前应补充解压后容量和 entry 数限制。

## 16. 成功标准

只有同时满足以下条件，才能认为下一阶段完成：

- 五种范围内格式均能稳定入库，MD/PPTX 不需要旁路代码；
- 结构信息通过统一模型进入存储、引用和预览；
- 应用只使用新的 `structured-v1` 空索引，不读取旧页快照或旧 chunk；
- 旧派生数据已按明确路径安全删除，原始语料和评测产物未被误删；
- 新评测集每题都经过人工 evidence/answer 核对；
- legacy 与新 corpus 隔离，冻结 test 不参与调参；
- 报告能按格式、难度、类别、split 和 question group 分析；
- 多轮追问使用上下文改写检索，且回答仍以当前文档证据为准；
- 刷新和应用重启后会话可恢复；
- 全量自动化测试、真实文档验收和回归评测均有保存证据；
- 没有因为增加功能而宣称未经评测证明的质量提升。

## 17. 审阅时需要重点确认

请重点检查以下设计取舍是否符合预期：

1. PDF 首期不承诺标题识别，只可靠保留页和段落；
2. Excel 本轮移除；
3. 新 150–200 样本与 legacy 27 题使用不同 corpus；
4. 现有 Chroma、metadata.db 和页快照不迁移，新系统从空索引开始；
5. 多轮会话为本地单用户持久化，不包含账号系统；
6. 多轮改写会增加一次模型调用的延迟和成本；
7. 实施拆成至少四个独立计划，不在一个分支中一次完成全部功能。
