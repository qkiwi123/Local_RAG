# P2-4 第一阶段：引用原文预览与解析文本高亮实施计划

> **供执行者使用：** 必须使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development`，严格按复选框逐项执行。先测试、后实现；每个任务单独验证、单独提交。本文只覆盖 P2-4 第一阶段，不得擅自扩展到 PDF 原页渲染或坐标高亮。

**目标：** 用户点击回答中的引用后，按需取得该引用对应的完整“解析后逻辑页文本”，在默认关闭的大尺寸对话框中精确高亮被引用 chunk，并展示文档名、页码、内容类型、chunk ID 和 chunk 原文；旧索引继续可问答，但不显示不可用的预览按钮。

**架构：** 上传或安全重建时，把 parser 输出按“同文档、同逻辑页、块间用 `\n\n` 连接”的规则组装成唯一规范页文本，并以 `document_id + parser_version + page_number` 存入现有 SQLite 元数据数据库。chunk 的 `start_char/end_char` 必须指向该规范页文本，parser 版本随 chunk 写入 Chroma。聊天响应只携带轻量引用定位信息；用户点击后，独立预览 API 再从 Chroma 读取精确 chunk、从 SQLite 读取匹配版本的完整页文本，并在服务端完成强一致性校验。Streamlit 使用 `st.dialog(width="large")` 展示预览，不使用不稳定的自定义 JavaScript 模拟侧滑抽屉。

**技术栈：** Python 3.14、FastAPI、Pydantic、SQLite、ChromaDB、Streamlit 1.59.1、httpx、pytest、Streamlit AppTest。

---

## 一、范围、非目标与已确认现状

### 本阶段必须交付

- 点击单条引用后按需请求完整解析页，不把所有页文本塞进 `/api/chat`。
- 展示文档名、逻辑页码、内容类型、chunk ID、完整 chunk 原文。
- 在完整解析页文本中按 `[start_char:end_char)` 精确高亮 chunk。
- PDF、DOCX、TXT 都支持“解析后文本预览”；DOCX/TXT 页码仍是现有虚拟页码。
- 同一逻辑页包含多个 parser block 时，统一用两个换行符连接，且 chunk offset 与该规则严格一致。
- 旧索引、缺少 parser 版本或页快照的数据继续正常问答，但 `preview_available=false`，前端不显示“查看原文”。
- 上传失败、删除文档、重建失败时，不得留下可被访问的孤儿页快照。
- 默认关闭预览能力；只在可信环境显式设置 `CITATION_PREVIEW_ENABLED=true`。

### 明确不做

- 不显示 PDF 原始页面图片。
- 不记录或处理 bounding box、字符框、词框、页面坐标。
- 不引入 PDFium、PyMuPDF、OCR 或新的 PDF 解析器。
- 不修改 embedding、检索、Reranker、Prompt、LLM、Trace 算法。
- 不实现严格意义的右侧滑出抽屉。当前 Streamlit 版本使用大尺寸 `st.dialog`；如未来必须做真正抽屉，应另立前端改造任务。
- 不在请求预览时临时重解析原文件，也不使用模糊搜索猜测高亮位置。
- 不自动覆盖或删除旧 Chroma 索引。

### 已确认的代码事实

- `ChunkMetadata` 已有 `document_id`、`chunk_id`、`page_number`、`content_type`、`start_char/end_char`，但没有 `parser_version`。
- `chunk_document()` 已按同逻辑页 block 间 `\n\n` 计算累计 offset，但规范页文本没有持久化，当前无法在 API 层复核。
- `Citation` 当前只有来源编号、chunk ID、文档名、页码和 200 字 excerpt。
- SQLite 当前只有 `documents` 表；Chroma 已保存 chunk 内容和 offset，但没有 parser 版本。
- 前端对“历史回答”和“新回答”各写了一套引用渲染逻辑，需要合并成一个组件。
- 规划时工作区存在用户自己的 `backend/services/chunker.py` 文档字符串改动。执行者不得覆盖、回退或顺手提交该改动。

---

## 二、不可变契约

### 1. 规范页文本

对 parser 输出列表按原始顺序遍历，以 `(document_id, page_number)` 分组。一个逻辑页的规范文本定义为：

```python
parsed_page_text = "\n\n".join(block["text"] for block in page_blocks)
```

禁止再次 `.strip()`、重复标准化、Markdown 重排或变更换行。parser block 的 `text` 已是最终标准化版本，页组装层只负责连接。

每个 chunk 必须满足以下强不变量：

```python
page.parsed_page_text[
    chunk.metadata.start_char:chunk.metadata.end_char
] == chunk.content
```

任何不满足都属于数据错误，必须在入库或重建阶段终止，不能进入 ready 状态。

### 2. parser 版本

第一阶段固定版本字符串：

```python
PARSER_VERSION = "text-v1-pypdf2"
```

版本描述的是“解析输出与 offset 语义”，不是应用版本。只要文本标准化、DOCX 表格 Markdown 化、虚拟分页、block 连接规则任一发生变化，都必须新建 parser 版本并完整重建，不得复用旧版本字符串。

### 3. SQLite 页表

在 `METADATA_DB_PATH` 指向的同一 SQLite 文件中新增：

```sql
CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    parsed_page_text TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (
        content_type IN ('paragraph', 'table', 'mixed')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, parser_version, page_number),
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
)
```

同一文档同一 parser 版本的整批页面必须在一个 SQLite 事务内替换，禁止逐页提交。

### 4. API 契约

聊天引用新增字段必须全部向后兼容：

```json
{
  "source_id": 1,
  "chunk_id": "doc-id_p1_b0_s10_e80",
  "document_id": "doc-id",
  "document_name": "handbook.pdf",
  "parser_version": "text-v1-pypdf2",
  "page_number": 1,
  "content_type": "paragraph",
  "start_char": 10,
  "end_char": 80,
  "excerpt": "...",
  "preview_available": true
}
```

预览端点固定为：

```text
GET /api/citations/{document_id}/chunks/{chunk_id}/preview
```

成功响应：

```json
{
  "document_id": "doc-id",
  "chunk_id": "doc-id_p1_b0_s10_e80",
  "document_name": "handbook.pdf",
  "parser_version": "text-v1-pypdf2",
  "page_number": 1,
  "content_type": "paragraph",
  "parsed_page_text": "完整规范页文本",
  "highlight_start": 10,
  "highlight_end": 80,
  "chunk_content": "被引用的完整 chunk"
}
```

状态码约定：

- `404`：功能关闭、文档不存在、文档非 ready、chunk 不存在或页快照不存在。
- `409`：chunk 的 document/parser/page 与页快照不匹配，或切片内容不等于 chunk 内容；错误文案必须提示“索引与解析版本不一致，请重建索引”。
- `422`：offset 越界、`start >= end` 或页文本超过安全上限。
- `200`：仅在所有强一致性校验通过后返回。

不得接收客户端传入的文件路径、页文本、offset 或 parser 版本作为可信来源。

### 5. 安全与容量

- 配置增加 `CITATION_PREVIEW_ENABLED=false` 和 `CITATION_PREVIEW_MAX_PAGE_CHARS=200000`。
- 功能关闭时聊天仍正常，所有引用 `preview_available=false`。
- API 只允许访问 SQLite 中 `status='ready'` 的登记文档。
- 响应增加 `Cache-Control: private, no-store`。
- 前端必须先 `html.escape()` 全部文档文本，再插入 `<mark>`；禁止直接把文档内容作为 HTML。
- 不打印完整页文本、chunk 原文或文档绝对路径到服务日志。

---

## 三、目标文件职责

- 修改 `backend/config.py`：增加预览开关和页文本长度上限。
- 修改 `.env.example`：记录开关、安全边界和本阶段含义。
- 修改 `tests/conftest.py`：测试默认关闭预览，避免环境污染。
- 修改 `backend/models/schemas.py`：增加 parser 版本、轻量引用定位字段和 `CitationPreview`。
- 创建 `backend/services/source_page.py`：组装规范页文本并校验 chunk/page 不变量。
- 创建 `backend/services/page_store.py`：管理 `document_pages` 表与事务。
- 修改 `backend/services/document_store.py`：启用 SQLite 外键并保证删除级联可用。
- 修改 `backend/services/parser.py`：声明并注入固定 parser 版本。
- 修改 `backend/services/chunker.py`：仅传播 parser 版本，不改变切分算法。
- 修改 `backend/services/vector_store.py`：持久化 parser 版本并按 chunk ID 精确读取。
- 修改 `backend/utils/citation.py`：从真实检索 metadata 生成可预览引用。
- 修改 `backend/routers/upload.py`：把页快照接入上传、失败补偿和删除链路。
- 创建 `backend/services/citation_preview.py`：封装预览一致性校验。
- 创建 `backend/routers/citations.py`：提供只读预览端点。
- 修改 `backend/main.py`：注册 citations router。
- 创建 `frontend/citation_view.py`：统一引用列表、按需请求、对话框和安全高亮。
- 修改 `frontend/app.py`：历史回答和新回答共用同一引用组件。
- 修改 `scripts/rebuild_index.py`：在独立目标索引验证成功后，补齐对应 parser 版本页快照。
- 新增或修改测试：`tests/test_source_page.py`、`tests/test_page_store.py`、`tests/test_parser.py`、`tests/test_chunker.py`、`tests/test_vector_store.py`、`tests/test_citation.py`、`tests/test_citation_preview.py`、`tests/test_api.py`、`tests/test_citation_view.py`、`tests/test_frontend_app.py`、`tests/test_rebuild_index.py`。
- 验收后修改 `README.md`、`eval/README.md`、`docs/交接_阶段一优化.md`。
- 验收后创建 `eval/results/p2-4-citation-text-preview/` 保存证据。

---

### 任务 0：锁定范围、保护用户改动并保存基线

**文件：** 无业务文件修改。

- [ ] **步骤 1：检查工作区并记录所有已有改动**

```powershell
git status --short
git diff -- backend/services/chunker.py
git log -1 --oneline
```

预期：能看到规划时已存在的 `backend/services/chunker.py` 中文文档字符串改动。把它标记为“用户改动”。不得执行 `git checkout --`、`git reset --hard` 或把该 hunk 混入 P2-4 提交。

- [ ] **步骤 2：解决同文件改动的提交边界**

由于 P2-4 也需要修改 `chunker.py`，执行前必须选择以下安全方式之一：

1. 用户先自行提交该文档字符串改动；或
2. 保留工作区改动，后续使用 `git add -p backend/services/chunker.py`，只暂存 parser_version 相关 hunk。

不得替用户自动提交或丢弃原改动。

- [ ] **步骤 3：保存当前索引与评测基线**

记录以下只读信息到临时笔记，不提交数据目录：

```powershell
.\.venv\Scripts\python.exe -c "from backend.services.vector_store import get_all_chunks; xs=get_all_chunks(); print(len(xs)); print(sum(len(x['content']) for x in xs))"
git status --short
```

同时记录当前生产索引目录、embedding 模型、chunk 参数、当前可比较的最新 `report.json` 路径。不要在此任务运行重建或完整 LLM 评测。

- [ ] **步骤 4：建立本阶段硬边界**

执行 Agent 在工作日志中写明：不安装 PDF 依赖、不修改 PDF 提取实现、不实现图片预览、不修改检索与生成算法。发现必须跨越边界的问题时停止并报告，不得自行扩大范围。

---

### 任务 1：增加配置与强类型数据契约

**文件：**
- 修改：`backend/config.py`
- 修改：`.env.example`
- 修改：`tests/conftest.py`
- 修改：`backend/models/schemas.py`
- 创建：`tests/test_citation_preview.py`

- [ ] **步骤 1：先写失败的配置测试**

覆盖：

```python
def test_citation_preview_is_disabled_in_tests():
    assert settings.CITATION_PREVIEW_ENABLED is False


def test_preview_page_limit_is_positive():
    assert settings.CITATION_PREVIEW_MAX_PAGE_CHARS > 0


def test_env_bool_rejects_invalid_preview_value(monkeypatch):
    monkeypatch.setenv("CITATION_PREVIEW_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="CITATION_PREVIEW_ENABLED"):
        _env_bool("CITATION_PREVIEW_ENABLED")
```

`tests/conftest.py` 必须在导入 backend 前设置 `CITATION_PREVIEW_ENABLED=false`，防止开发机 `.env` 让测试结果漂移。

- [ ] **步骤 2：先写失败的数据模型测试**

断言：

- 旧格式 `Citation` 不提供新增字段仍可构造，且 `preview_available` 默认 `False`。
- `ChunkMetadata.parser_version` 对旧数据默认空字符串。
- `CitationPreview` 完整序列化上述 API 契约。
- Pydantic 拒绝页码小于 1、负 offset 和 `highlight_end <= highlight_start`。如果跨字段约束不在模型层实现，则明确由服务层测试覆盖，不能两边都缺失。

- [ ] **步骤 3：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_preview.py -q -p no:cacheprovider
```

预期：因配置项、字段或模型不存在而失败。

- [ ] **步骤 4：实现最小配置和模型**

新增：

```python
CITATION_PREVIEW_ENABLED = _env_bool("CITATION_PREVIEW_ENABLED", False)
CITATION_PREVIEW_MAX_PAGE_CHARS = int(
    os.getenv("CITATION_PREVIEW_MAX_PAGE_CHARS", "200000")
)
```

在 `_validate()` 中验证上限大于 0。模型字段使用 `Optional` 保持旧响应兼容；`CitationPreview` 使用严格、非可选字段。

`.env.example` 写明：预览会向前端返回完整解析页文本，在没有认证的部署环境不得开启。

- [ ] **步骤 5：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_preview.py tests\test_api.py -q -p no:cacheprovider
git diff --check
git add backend/config.py backend/models/schemas.py tests/conftest.py tests/test_citation_preview.py .env.example
git commit -m "feat: define citation preview contracts"
```

预期：新增测试和原 API 契约测试均通过；`.env` 本机文件不得暂存。

---

### 任务 2：建立规范页组装器与 offset 强不变量

**文件：**
- 创建：`backend/services/source_page.py`
- 创建：`tests/test_source_page.py`
- 修改：`tests/test_chunker.py`

- [ ] **步骤 1：编写失败的组装测试**

至少覆盖四组：

1. 单 block PDF/TXT 页文本原样保留；
2. 同一 DOCX 虚拟页的 paragraph、table、paragraph 按输入顺序用 `\n\n` 连接；
3. 混合内容页的 `content_type == "mixed"`，纯表格页为 `table`；
4. 不同文档或页码绝不串页。

建议返回强类型 `ParsedPage`，字段为 `document_id`、`document_name`、`parser_version`、`page_number`、`parsed_page_text`、`content_type`。

- [ ] **步骤 2：编写失败的 chunk/page 对齐测试**

使用真实 `chunk_document()` 生成 overlapping chunks，并逐个断言：

```python
page = pages_by_key[(
    chunk.metadata.document_id,
    chunk.metadata.parser_version,
    chunk.metadata.page_number,
)]
assert (
    page.parsed_page_text[
        chunk.metadata.start_char:chunk.metadata.end_char
    ]
    == chunk.content
)
```

测试必须包含同页多 block，防止 `\n\n` 连接规则在两个模块中漂移。

- [ ] **步骤 3：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_source_page.py tests\test_chunker.py -q -p no:cacheprovider
```

预期：`source_page` 尚不存在或 parser_version 未传播。

- [ ] **步骤 4：实现单一页组装规则**

`source_page.py` 只提供纯函数：

- `assemble_source_pages(blocks) -> list[ParsedPage]`
- `index_source_pages(pages) -> dict[tuple, ParsedPage]`
- `validate_chunks_against_pages(chunks, pages) -> None`

校验失败应抛出包含 document/page/chunk ID 的 `ValueError`，但不得把完整文档文本写进异常。

不要在 chunker 内复制第二套页组装实现。当前 chunker 的累计规则必须与这里的 `\n\n` 规则通过交叉测试锁死。

- [ ] **步骤 5：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_source_page.py tests\test_chunker.py -q -p no:cacheprovider
git diff --check
git add backend/services/source_page.py tests/test_source_page.py tests/test_chunker.py
git commit -m "feat: assemble canonical parsed pages"
```

---

### 任务 3：持久化规范页并保证事务与删除语义

**文件：**
- 创建：`backend/services/page_store.py`
- 修改：`backend/services/document_store.py`
- 创建：`tests/test_page_store.py`
- 修改：`tests/test_document_store.py`

- [ ] **步骤 1：编写失败的页存储测试**

使用 `tmp_path / "metadata.db"`，覆盖：

- `replace_document_pages()` 一次写入多页并能精确读取。
- 相同 `(document_id, parser_version)` 第二次写入时整批替换，不残留旧页。
- 同一 `document_id` 的两个 parser 版本可以并存。
- 中途违反约束时整个事务回滚，不能只写入前几页。
- `get_document_page()` 对不存在的页返回 `None`。
- `delete_document_pages()` 只删除目标文档。
- 删除 `documents` 记录后对应页通过外键级联删除。

- [ ] **步骤 2：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_page_store.py tests\test_document_store.py -q -p no:cacheprovider
```

- [ ] **步骤 3：实现短连接事务存储**

沿用 `document_store.py` 的短连接模式。每个连接执行：

```sql
PRAGMA foreign_keys = ON
```

`replace_document_pages()` 必须：

1. 验证传入页面均属于同一 document/parser 版本；
2. 开启一个连接和一个事务；
3. 删除该 document/parser 版本旧行；
4. `executemany()` 写入全部新行；
5. 任一步失败整体回滚。

不得把完整页内容打印到日志。

- [ ] **步骤 4：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_page_store.py tests\test_document_store.py -q -p no:cacheprovider
git diff --check
git add backend/services/page_store.py backend/services/document_store.py tests/test_page_store.py tests/test_document_store.py
git commit -m "feat: persist versioned parsed pages"
```

---

### 任务 4：传播 parser 版本并支持按 chunk ID 精确读取

**文件：**
- 修改：`backend/services/parser.py`
- 修改：`backend/services/chunker.py`
- 修改：`backend/services/vector_store.py`
- 修改：`tests/test_parser.py`
- 修改：`tests/test_chunker.py`
- 修改：`tests/test_vector_store.py`

- [ ] **步骤 1：编写失败的版本传播测试**

断言：

- PDF、DOCX、TXT 的每个 parser block 都带 `PARSER_VERSION`。
- chunk 的 `metadata.parser_version` 等于来源 block 版本。
- Chroma 写入 metadata 包含 `parser_version`。
- `get_chunk_by_id(chunk_id)` 返回 `{chunk_id, content, metadata}`。
- 不存在的 chunk ID 返回 `None`，不抛底层 Chroma 异常。

- [ ] **步骤 2：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_parser.py tests\test_chunker.py tests\test_vector_store.py -q -p no:cacheprovider
```

- [ ] **步骤 3：实现最小传播，不改变解析和切分**

在 `parser.py` 声明 `PARSER_VERSION = "text-v1-pypdf2"`，由 `parse_document()` 给每个 block 注入。chunker 只读取并写入 `ChunkMetadata`；禁止改动 `_normalize_text()`、PDF 提取、DOCX 表格转换、切分边界或 chunk ID 规则。

在 `vector_store.py` 的 metadata 增加 parser 版本，并实现：

```python
def get_chunk_by_id(chunk_id: str) -> Optional[dict]:
    result = _collection.get(
        ids=[chunk_id],
        include=["documents", "metadatas"],
    )
    ...
```

只接受精确 ID，不允许回退到相似度搜索。

- [ ] **步骤 4：验证 chunk 文本零变化**

用现有 fixtures 分别在改动前基线快照与改动后结果比较：除新增 `parser_version` 外，chunk 数量、chunk ID、content、page、start/end 必须完全相同。若不同立即停止，P2-4 第一阶段不允许借机改变解析/切分。

- [ ] **步骤 5：只暂存 P2-4 hunk 并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_parser.py tests\test_chunker.py tests\test_vector_store.py tests\test_source_page.py -q -p no:cacheprovider
git diff --check
git add backend/services/parser.py backend/services/vector_store.py backend/models/schemas.py tests/test_parser.py tests/test_chunker.py tests/test_vector_store.py
git add -p backend/services/chunker.py
git diff --cached --check
git commit -m "feat: version citation source offsets"
```

提交前用 `git diff --cached -- backend/services/chunker.py` 确认用户原有文档字符串改动没有进入暂存区。

---

### 任务 5：把页快照接入上传、失败补偿和删除链路

**文件：**
- 修改：`backend/routers/upload.py`
- 修改：`tests/test_api.py`

- [ ] **步骤 1：编写失败的上传集成测试**

mock embedding/Chroma，覆盖成功顺序：

1. 创建 processing 文档记录；
2. parse；
3. 组装规范页；
4. chunk；
5. 校验所有 chunk/page 切片；
6. 写 Chroma；
7. 事务写页快照；
8. 临时文件原子改名；
9. mark ready。

成功后断言页快照存在，且每个 chunk 都能从对应页精确切片得到。

- [ ] **步骤 2：编写失败补偿测试**

分别让以下位置抛错：页组装校验、Chroma 写入、页快照写入、`os.replace()`、`mark_ready()`。每种情况均断言：

- API 返回明确 500；
- 临时和最终文件已清理；
- 该 document ID 的 Chroma chunks 已删除；
- 该 document ID 的页快照已删除；
- 文档记录保持 failed，且不可能通过预览 API 访问。

- [ ] **步骤 3：编写删除测试**

按稳定 ID 删除文档后，断言向量、原文件、页快照、document 记录全部消失。兼容的按名称删除路径也必须覆盖多版本文档。

- [ ] **步骤 4：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py -q -p no:cacheprovider
```

- [ ] **步骤 5：接入页快照与补偿清理**

扩展 `_cleanup_failed_ingest()`，无论页写入发生在错误前还是错误后，都安全调用 `delete_document_pages(document_id)`。删除链路在删除 document 记录前清理页数据；外键级联作为第二层防御。

跨 SQLite、Chroma 和文件系统无法形成单个 ACID 事务，因此这里使用“ready 状态门禁 + 幂等补偿删除”。文档只有最后 `mark_ready()` 后才能被预览 API 读取。

- [ ] **步骤 6：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_page_store.py tests\test_source_page.py -q -p no:cacheprovider
git diff --check
git add backend/routers/upload.py tests/test_api.py
git commit -m "feat: persist citation pages during ingest"
```

---

### 任务 6：生成轻量、可回查且向后兼容的 Citation

**文件：**
- 修改：`backend/utils/citation.py`
- 修改：`backend/routers/chat.py`
- 修改：`tests/test_citation.py`
- 修改：`tests/test_api.py`

- [ ] **步骤 1：编写失败的映射测试**

覆盖：

- `【来源N】` 仍只映射第 N 个真实检索结果，不允许 LLM 自由提供元数据。
- metadata 完整且开关开启时，Citation 带 document/parser/content type/start/end，`preview_available=True`。
- 开关关闭、缺 document ID、缺 parser version、缺 chunk ID、offset 无效任一情况，`preview_available=False`。
- `start_char=0` 是合法值，不能用简单 truthy 判断误判。
- excerpt 仍最多 200 字，`/api/chat` 不包含 `parsed_page_text` 或完整页内容。
- 旧格式检索 metadata 仍能生成原有文档名、页码和 excerpt。

- [ ] **步骤 2：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation.py tests\test_api.py -q -p no:cacheprovider
```

- [ ] **步骤 3：实现显式开关参数**

建议把签名改为：

```python
def extract_citations(
    answer: str,
    retrieval_results: list[dict],
    *,
    preview_enabled: bool = False,
) -> list[Citation]:
```

`chat.py` 显式传 `settings.CITATION_PREVIEW_ENABLED`，避免 citation 工具函数暗中依赖全局配置，现有单元测试也能稳定控制行为。

`preview_available` 只代表“引用 metadata 具备预览资格且功能开启”，不代表页快照已经验证；真正一致性由点击后的预览服务负责。

- [ ] **步骤 4：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation.py tests\test_api.py tests\test_retrieval_trace.py -q -p no:cacheprovider
git diff --check
git add backend/utils/citation.py backend/routers/chat.py tests/test_citation.py tests/test_api.py
git commit -m "feat: expose citation preview locators"
```

---

### 任务 7：实现强一致性的引用预览服务与只读 API

**文件：**
- 创建：`backend/services/citation_preview.py`
- 创建：`backend/routers/citations.py`
- 修改：`backend/main.py`
- 修改：`tests/test_citation_preview.py`
- 修改：`tests/test_api.py`

- [ ] **步骤 1：编写失败的服务层测试**

对 `build_citation_preview(document_id, chunk_id)` 覆盖：

- ready 文档 + 精确 chunk + 同版本页 + 精确切片返回成功。
- document ID 与 chunk metadata 不一致时冲突。
- parser 版本或页码不一致时冲突。
- `start < 0`、`end > len(page)`、`start >= end` 被拒绝。
- 切片内容与 Chroma chunk 不完全相等时冲突；即使页面中其他位置出现相同文本，也禁止模糊回退。
- 页面超过 `CITATION_PREVIEW_MAX_PAGE_CHARS` 被拒绝。
- legacy chunk、缺页、failed/processing 文档不可预览。

- [ ] **步骤 2：编写失败的 API 测试**

覆盖任务前述 `200/404/409/422` 契约，并断言：

- 功能关闭时不访问 Chroma 或 SQLite 内容读取函数；
- 成功响应头有 `Cache-Control: private, no-store`；
- 响应不包含服务器文件路径或 stored_name；
- 路径中的 document ID 不能读取另一个文档的 chunk。

- [ ] **步骤 3：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_preview.py tests\test_api.py -q -p no:cacheprovider
```

- [ ] **步骤 4：实现纯服务与薄路由**

服务层依次执行：

1. `get_document(document_id)` 并验证 ready；
2. `get_chunk_by_id(chunk_id)`；
3. 验证 chunk metadata 的 document ID；
4. 读取 parser version、page、offset；
5. `get_document_page(document_id, parser_version, page_number)`；
6. 验证范围和长度上限；
7. 验证 `page_text[start:end] == chunk.content`；
8. 构造 `CitationPreview`。

路由只负责开关、异常到 HTTP 状态码的映射和响应头。不要让 router 复制一致性逻辑。

- [ ] **步骤 5：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_preview.py tests\test_api.py tests\test_vector_store.py tests\test_page_store.py -q -p no:cacheprovider
git diff --check
git add backend/services/citation_preview.py backend/routers/citations.py backend/main.py tests/test_citation_preview.py tests/test_api.py
git commit -m "feat: serve verified citation previews"
```

---

### 任务 8：实现安全高亮与统一的 Streamlit 引用组件

**文件：**
- 创建：`frontend/citation_view.py`
- 修改：`frontend/app.py`
- 创建：`tests/test_citation_view.py`
- 修改：`tests/test_frontend_app.py`

- [ ] **步骤 1：编写失败的纯函数测试**

为 `build_highlight_html(text, start, end)` 覆盖：

- 正确把前段、高亮段、后段拼接，且只包裹一个 `<mark>`。
- 中文、换行、Markdown 表格字符保持原文本语义。
- `<script>`、`<img onerror>`、`&`、引号均被 HTML 转义。
- offset 越界或空范围抛出明确异常，不静默截断。
- 输出容器使用 `white-space: pre-wrap`，不丢失页面换行。

- [ ] **步骤 2：编写失败的 HTTP 与组件测试**

新增可独立测试的：

```python
def fetch_citation_preview(client, backend_url, citation) -> dict:
    ...

def render_citations(citations, message_key, client, backend_url) -> None:
    ...
```

覆盖：

- `preview_available=false` 时无“查看原文”按钮。
- 点击前不发预览 GET，证明是 lazy load。
- 同一回答的多个引用按钮 key 唯一；历史消息 rerun 后仍稳定。
- 404 显示“该引用暂无可用原文预览”。
- 409 原样显示“索引与解析版本不一致，请重建索引”。
- 非 JSON、空响应和超时复用 `frontend/http_utils.py` 的稳定错误解析。

- [ ] **步骤 3：编写 AppTest 失败测试**

构造一个带完整 locator 的聊天响应，断言：

- “参考来源” expander 仍默认关闭；
- 展开后出现“查看原文”；
- 历史回答与新回答都只通过统一组件渲染；
- 无预览资格的旧 Citation 仍显示 excerpt，但无按钮；
- 消息保存到 `st.session_state.messages` 后 locator 字段没有丢失。

AppTest 如果不能可靠检查 `st.dialog` 内部元素，只验证按钮和状态变化；对话框完整交互放到真实浏览器验收，不得为迁就 AppTest 放弃 dialog。

- [ ] **步骤 4：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_view.py tests\test_frontend_app.py -q -p no:cacheprovider
```

- [ ] **步骤 5：实现默认关闭的预览交互**

UI 固定结构：

1. 外层“📎 参考来源” expander 保持 `expanded=False`；
2. 每条引用继续显示文档名、页码和短 excerpt；
3. 只有 `preview_available=true` 才显示“查看原文”；
4. 点击后 GET 预览端点；
5. 使用 `@st.dialog("引用原文", width="large")` 展示元数据；
6. 先展示安全高亮后的完整解析页，再展示独立的“完整 Chunk”区域；
7. 对话框默认不存在，只有点击后打开；关闭后不影响聊天历史。

对历史与新回答删除原有重复循环，统一调用 `render_citations()`。按钮 key 至少包含 `message_key + source_id + chunk_id` 的稳定摘要。

- [ ] **步骤 6：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_view.py tests\test_frontend_app.py tests\test_frontend_http_utils.py -q -p no:cacheprovider
git diff --check
git add frontend/citation_view.py frontend/app.py tests/test_citation_view.py tests/test_frontend_app.py
git commit -m "feat: show highlighted citation text"
```

---

### 任务 9：扩展安全重建脚本，为现有文档补齐页快照

**文件：**
- 修改：`scripts/rebuild_index.py`
- 修改：`tests/test_rebuild_index.py`

- [ ] **步骤 1：编写失败的重建测试**

现有 `prepare_documents()` 需同时产出规范页和 chunks。可引入 `PreparedDocument` 数据类，避免匿名三元组。覆盖：

- 每份 ready 文档解析后，先验证全部 chunk/page 切片，再允许写目标 Chroma。
- 缺原文件、切片错位、目标目录非空时，页表完全不变。
- 目标 Chroma 全量校验失败时，页表完全不变。
- 目标 Chroma 验证成功后，才事务写入本 parser 版本的页快照。
- 旧 parser 版本页不被误删。
- 4A 不接受 chunk 数量变化；出现变化必须停止，不得使用 `--accept-chunk-count-change` 绕过。

- [ ] **步骤 2：运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rebuild_index.py -q -p no:cacheprovider
```

- [ ] **步骤 3：实现“先独立索引、后安装页快照”**

安全顺序：

1. 读取 ready 文档及原始文件；
2. parse、assemble pages、chunk、强不变量校验；
3. 写全新的 `--target-dir`；
4. 校验总 chunk 数、逐文档 chunk 数、document ID、embedding 维度；
5. 校验与基线相比 chunk ID/content/start/end 完全一致，唯一新增 metadata 是 parser_version；
6. 目标索引全部通过后，才把页面写入 `METADATA_DB_PATH`；
7. 输出 JSON 摘要，包含 parser 版本、页数、chunk 数和逐文档对齐结果。

不得让脚本自动改 `.env`、覆盖活动索引或删除旧索引。

- [ ] **步骤 4：运行 GREEN 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rebuild_index.py tests\test_source_page.py tests\test_page_store.py -q -p no:cacheprovider
git diff --check
git add scripts/rebuild_index.py tests/test_rebuild_index.py
git commit -m "feat: rebuild citation page snapshots"
```

---

### 任务 10：自动化回归与第一阶段准入门槛

**文件：** 无新增业务实现。

- [ ] **步骤 1：运行专项测试**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_source_page.py `
  tests\test_page_store.py `
  tests\test_parser.py `
  tests\test_chunker.py `
  tests\test_vector_store.py `
  tests\test_citation.py `
  tests\test_citation_preview.py `
  tests\test_api.py `
  tests\test_citation_view.py `
  tests\test_frontend_app.py `
  tests\test_rebuild_index.py `
  -q -p no:cacheprovider
```

预期：零失败。

- [ ] **步骤 2：运行完整非外部测试和依赖检查**

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

预期：零失败、零依赖冲突、零空白格式错误。

- [ ] **步骤 3：执行隔离重建，不切换生产索引**

先创建一个明确的新目录，例如：

```powershell
.\.venv\Scripts\python.exe .\scripts\rebuild_index.py `
  --target-dir data\chroma_db_p2_4a
```

若目录已存在且非空，脚本必须拒绝覆盖；执行者应换一个新的显式目录，不得删除现有目录冒险重跑。

- [ ] **步骤 4：核对零检索变更门槛**

第一阶段没有修改解析、切分、embedding 或检索，因此门槛不是“指标大致相同”，而是：

- 总 chunk 数与基线相同；
- 每份文档 chunk 数相同；
- chunk ID、content、page、start/end 完全相同；
- 仅新增 parser_version metadata；
- 使用同一模型和参数跑相同数据集时，11 项汇总指标不得回退；任何逐题检索结果变化都必须先解释，不能直接归因于随机波动。

如 chunk 内容或 offset 有任何变化，停止激活新索引并回到任务 2/4 排查。

- [ ] **步骤 5：检查工作区所有权**

```powershell
git status --short
git diff -- backend/services/chunker.py
git diff --check
```

确认用户原有文档字符串改动仍被保留，且未混入任何 P2-4 提交。

---

### 任务 11：真实环境验收、证据保存与文档交接

**文件：**
- 创建：`eval/results/p2-4-citation-text-preview/api-preview.json`
- 创建：`eval/results/p2-4-citation-text-preview/text-highlight.png`
- 创建：`eval/results/p2-4-citation-text-preview/validation.md`
- 创建：`eval/results/p2-4-citation-text-preview/regression-summary.json`
- 修改：`README.md`
- 修改：`eval/README.md`
- 修改：`docs/交接_阶段一优化.md`

- [ ] **步骤 1：先备份元数据数据库并人工切换新索引**

停止前后端后复制 `data/metadata.db` 到带时间戳的备份文件；确认 `data/chroma_db_p2_4a` 已通过任务 10 后，才把本机 `.env` 的 `CHROMA_PERSIST_DIR` 指向新目录，并设置：

```dotenv
CITATION_PREVIEW_ENABLED=true
CITATION_PREVIEW_MAX_PAGE_CHARS=200000
```

`.env` 和数据库备份不得提交。旧索引目录必须保留到本阶段验收和用户手测结束。

- [ ] **步骤 2：启动真实前后端并记录进程 ID**

```powershell
$startupOutput = @(.\scripts\start.ps1)
$startupOutput | ForEach-Object { Write-Host $_ }
$joinedOutput = $startupOutput -join "`n"
$backendPid = [int]([regex]::Match($joinedOutput, 'Backend PID: (\d+)').Groups[1].Value)
$frontendPid = [int]([regex]::Match($joinedOutput, 'Frontend PID: (\d+)').Groups[1].Value)
```

确认根健康检查返回 200，且 PID 已解析成功。

- [ ] **步骤 3：用真实上传流程建立至少三种格式样本**

通过 Streamlit 上传一份 PDF、一份 DOCX、一份 TXT，确保至少有一个 DOCX 逻辑页同时包含 paragraph/table block。不得直接往 SQLite 或 Chroma 塞测试数据代替真实上传。

逐份确认：document status ready、页快照数量正确、任一 chunk 的页切片等于 chunk 内容。

- [ ] **步骤 4：执行真实问答并保存 API 证据**

提出一个能稳定命中已上传文档且回答包含 `【来源N】` 的问题。保存 `/api/chat` 响应，并对其中一条 `preview_available=true` 引用调用预览端点，把成功响应保存为 `api-preview.json`。

保存前检查 JSON 不含 API key、绝对路径或不应公开的环境信息。测试文档为公开/AI 生成内容时可以保存完整解析页；否则只保存结构与脱敏文本。

- [ ] **步骤 5：完成浏览器交互验收**

在 `http://127.0.0.1:8501` 验证：

1. 参考来源默认折叠；
2. 展开后只有可预览引用出现“查看原文”；
3. 点击才发 GET；
4. 大尺寸对话框展示正确文档、页码、内容类型和 chunk ID；
5. 黄色高亮内容与“完整 Chunk”逐字符一致；
6. 长页可滚动，不把聊天主面板无限拉长；
7. 关闭对话框、再次展开、触发一次 Streamlit rerun 后仍可使用；
8. 历史回答中的按钮仍能打开正确引用；
9. 旧索引引用只显示 excerpt，不出现按钮；
10. 页面中的 `<script>` 等测试文本以普通字符显示，不执行；
11. 浏览器控制台无异常。

把包含完整对话框和黄色高亮的截图保存为 `text-highlight.png`。

- [ ] **步骤 6：完成错误路径验收**

在测试环境分别构造或 mock：功能关闭、页快照缺失、offset 错位。确认前端分别显示可理解错误，且不会把错误页或其他 chunk 当作正确预览。

- [ ] **步骤 7：运行同数据集回归并保存摘要**

使用与当前基线完全相同的 dataset、模型和参数运行评测，保存原始 `report.json/report.md` 到新的 run 目录，再生成 `regression-summary.json`。摘要至少包含：

- 基线与 P2-4A 的 commit、索引目录和 parser_version；
- 题数和 dataset version；
- 11 项指标 before/after/delta；
- chunk 总数和逐文档数量；
- 是否存在逐题 retrieval 变化；
- 明确结论 `pass/fail`。

准入条件：11 项指标无回退、chunk 内容和 offset 零变化、预览强一致性通过。若失败，恢复旧 `CHROMA_PERSIST_DIR` 并停止交付。

- [ ] **步骤 8：安全停止服务**

```powershell
Stop-Process -Id $backendPid,$frontendPid
Get-Process -Id $backendPid,$frontendPid -ErrorAction SilentlyContinue
```

最后一条命令必须不返回进程记录。

- [ ] **步骤 9：写验收报告和项目文档**

`validation.md` 必须基于实际保存证据填写，禁止预先写“通过”。至少记录：

- 环境、commit、parser_version、开关；
- 三种格式的上传与页/chunk 数；
- 成功预览响应的 document/page/chunk/offset；
- 切片一致性验证结果；
- 前端默认折叠、对话框、高亮、历史消息和错误状态；
- 11 项指标回归结论；
- 旧索引兼容与回滚路径；
- 当前限制：仅解析文本，不是 PDF 原页。

README 说明用户如何开启与使用；eval README 说明证据目录和回归命令；交接文档更新 P2-4 第一阶段状态、数据结构、迁移方法、限制和下一阶段边界。

- [ ] **步骤 10：提交证据和文档**

```powershell
git diff --check
git status --short
git add README.md eval/README.md docs/交接_阶段一优化.md eval/results/p2-4-citation-text-preview
git commit -m "docs: record P2-4 citation preview validation"
```

不要暂存 `.env`、`data/metadata.db`、数据库备份、活动或旧 Chroma 目录，也不要暂存用户原有 `chunker.py` 文档字符串 hunk。

- [ ] **步骤 11：最终完成前验证**

调用 `superpowers:verification-before-completion`，重新运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

只有测试输出、真实浏览器证据、回归摘要、文档和工作区所有权检查全部满足，才可向用户报告第一阶段完成。

---

## 四、失败与回滚规则

- 新索引未通过全部验证：继续使用旧 `CHROMA_PERSIST_DIR`，不得切换。
- 已切换后发现预览错位：立即关闭 `CITATION_PREVIEW_ENABLED`；问答继续可用，再切回旧索引定位问题。
- SQLite 页快照异常：恢复验收前的 `metadata.db` 备份，或只删除明确的 `parser_version='text-v1-pypdf2'` 行；执行删除前必须再次确认目标数据库绝对路径。
- 旧索引不支持预览不是故障；保持 `preview_available=false`，不得请求时临时重解析伪装兼容。
- 一致性失败不得使用 `str.find()`、近似匹配或截断 offset 修补。必须修复 parser/page/chunk 的版本和组装规则后重建。
- 不删除旧索引，直到用户完成手动测试并明确批准清理。

---

## 五、完成定义

P2-4 第一阶段只有同时满足以下条件才算完成：

- 三种格式真实上传均写入版本化规范页快照。
- 所有 chunk 的 `[start_char:end_char)` 都能从匹配版本页文本精确还原。
- `/api/chat` 保持轻量和向后兼容。
- 预览 API 对成功、legacy、缺页、错版本、越界和功能关闭均有确定行为。
- 前端引用列表默认折叠，预览对话框按点击打开，完整页文本安全转义并精确高亮。
- 旧索引仍可问答且不会显示虚假预览能力。
- 解析、切分、检索、生成算法没有被本阶段改变。
- 完整测试通过，11 项指标无回退，真实截图和 JSON/Markdown 证据已保存。
- `.env`、数据库、索引目录和用户原有改动没有被误提交或覆盖。
