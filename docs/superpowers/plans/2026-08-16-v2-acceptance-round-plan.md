# 验收轮详细调整方案：OCR 文档结构 / structured-v2 迁入 / 文档搜索 / OCR 原图对照预览

> 状态：方案待审阅（未修改任何项目代码）
> 关联设计：`docs/superpowers/specs/2026-08-13-rag-v2-optimization-design.md`
> 关联分析：`docs/数据链路检索过程与阶段选择策略详解.md`、
> `docs/数据链路调优反馈分析与结构化v2迁入审查.md`
> 本轮范围：只做“验收与体验性开发”四件事；BM25、chunk 命中/召回、视觉缓存淘汰等
> 系统调优推迟到下一轮。

---

## 0. 任务边界与验收目标

### 0.1 本轮做

| 编号 | 任务 | 目标 |
|---|---|---|
| A | structured-v2 代码侧迁入收尾 | 评测代码与测试不再残留 v1 判断，正式 v2 评测可用 |
| B | OCR 文档结构 | OCR 识别文本在全文预览/引用预览中恢复标题、列表等结构 |
| C | 知识库文档搜索 | 按文档名称模糊搜索（与标签筛选可组合） |
| D | OCR 原图对照预览 | OCR 块旁显示“被识别的原图 + AI 识别文本（带徽标）”，可直观核对 |

### 0.2 本轮不做（按你的决定顺延）

- BM25 索引/缓存优化（T-08）；
- chunk 命中/召回与同文档配额策略（灵犀X3 类问题）；
- 视觉缓存淘汰/上限（T-12）；
- LLM 参数配置化、前端参数面板等下一轮优化；
- legacy 模块删除（保留到 React 对齐验收完成后再清理）。

### 0.3 完成标准

1. `pytest -q` 全量通过，新增测试覆盖本轮四件事；
2. `web` 的 lint / test / build 通过；
3. 生产库 31 份 ready 文档经 backfill 后：`document_units` 仍全部为
   `structured-v2`，canonical_text 与 chunk 数不变化，预览结构可见；
4. 你手动验收：文档预览里 OCR 内容有标题/列表结构、原图与识别文本同屏对照、
   知识库可按名称搜索。

---

## 1. 总体执行顺序与依赖

```text
P0 基线：记录当前测试/构建结果（只读）
P1 任务 A（v2 代码收尾）      ── 改动最小，独立完成
P2 任务 C（文档搜索）          ── 独立完成
P3 任务 B（OCR 结构）          ── parser + 回填脚本
P4 任务 D（OCR 原图对照）      ── 依赖 B 的 block 结构/属性约定
P5 回归 + 手动验收清单
```

P1/P2 互不依赖，可并行；P3 先于 P4 是因为 P4 复用 P3 的 OCR block 属性约定。

---

## 2. 任务 A：structured-v2 代码侧迁入收尾

### 2.1 现状（已审计）

- 生产数据层已 100% v2：SQLite `document_units` 2466 行全部 `structured-v2`；
  Chroma 2620 chunks 全部 `structured-v2`；schema v3；Dataset v3 与 manifest 均为 v2。
- 唯一实质阻塞：`eval/metrics.py:84` 硬编码 `parser_version == "structured-v1"`，
  会使 v2 下 MD/PPTX（无 page_number）的引用被判 invalid。
- 其余为文案/测试残留。

### 2.2 文件级改动

1. `eval/metrics.py:84`（核心）
   - 现状：`structured_location = meta.get("parser_version") == "structured-v1"
     and bool(meta.get("unit_id")) and bool(meta.get("location_label"))`。
   - 改为**版本无关的结构化判断**：
     `structured_location = bool(meta.get("unit_id")) and
     bool(meta.get("location_label")) and
     meta.get("locator_kind") in {"page","virtual_page","slide","section","sheet"}`。
   - 理由：v1/v2 的结构化 chunk 都具备这三个字段，不引用版本常量即可同时兼容；
     也避免 metrics.py 引入 backend import。
   - 同时给 `parse_citations` 增加 MD/PPTX 引用 valid 的回归测试（见 2.4）。

2. `tests/test_eval_metrics.py:85`、`tests/test_tag_retrieval.py:22`
   - 测试 fixture 的 `parser_version` 改为 `structured-v2`（或参数化 v1/v2 双版本）。
   - 新增断言：`structured-v2` 的 `section`/`slide` locator（无 page_number）
     引用必须判定 valid。

3. 文案统一（无行为变化）
   - `eval/adapters.py:242`：`"parser_mode": "structured-v1"` → `"structured-v2"`；
   - `backend/services/structured_citation_preview.py:72`：错误文案
     “chunk 不是当前 structured-v1 索引” → 使用 `PARSER_VERSION` 变量拼写；
   - `backend/models/schemas.py:157`、`backend/routers/upload.py:377`、
     `frontend/citation_view.py:197` 中的 structured-v1 注释改为通用描述或明确
     标注 legacy（不改变逻辑）。

4. 不删除 legacy 模块：`backend/services/parser.py`、`page_store.py`、
   `source_page.py`、`citation_preview.py`、`frontend/` 继续保留，待前端对齐验收
   通过后再单独立项清理。

### 2.3 验证

- `.\.venv\Scripts\python.exe -m pytest tests/test_eval_metrics.py tests/test_tag_retrieval.py tests/test_structured_ingest.py -q`
- 用当前 Dataset v3（draft 预检）跑一次无 LLM 检索基线，确认 MD/PPTX 引用指标
  统计路径不再受 v1 判断影响；
- 全量 `pytest -q`。

---

## 3. 任务 C：知识库文档名称模糊搜索

### 3.1 后端

文件：`backend/routers/upload.py` 的 `GET /api/documents`

1. 签名增加 `q: str | None = Query(default=None, max_length=200)`。
2. 在 `list_document_records(status="ready")` 之后：
   - `q` 为空/空白 → 不过滤；
   - 否则先转义 `\`、`%`、`_`，再执行
     `LOWER(original_name) LIKE LOWER(?) ESCAPE '\'`（ASCII 不区分大小写）；
   - 与 tags 解析出的 `allowed` 集合取交集，两个条件都满足才返回。
3. 保持响应结构不变（`documents` + `document_items`），不新增字段。

新增测试：

- 只按 `q` 搜索命中/未命中；
- `q` + `tags` 组合过滤；
- `%` / `_` 输入按字面匹配，不通配所有文档；
- 空 q 行为与旧接口一致；
- 超长 q 返回 422。

### 3.2 前端

文件：`web/src/api/client.ts`、`web/src/pages/knowledge/KnowledgePage.tsx`、
`web/src/pages/knowledge/DocumentTable.tsx`

1. `api.listDocuments(q?: string, tags?: string[])`：同时拼
   `q=` 与多个 `tags=`。
2. `KnowledgePage` 新增 `searchQuery` 状态：
   - `refreshDocuments` 改为同时携带 `searchQuery` 与 `documentFilter`；
   - 上传成功、标签增删改、刷新等所有重取路径都保留当前搜索词。
3. `DocumentTable`：
   - toolbar 增加 `Input.Search allowClear placeholder="按文档名称搜索"`；
   - 输入防抖 300ms 后调用 `onSearchChange(value)`；
   - 紧凑卡片视图与表格视图共用同一搜索词。
4. 组件 props 增加 `searchValue` / `onSearchChange`；空结果文案区分
   “无匹配文档”和“无文档”。

新增前端测试（vitest）：

- `api.listDocuments` 拼出的 query string；
- `DocumentTable` 输入触发防抖回调、清除后恢复全量。

### 3.3 验收

在知识库管理页输入文件名子串（中文、扩展名、大小写混合）能即时过滤；
与标签筛选叠加时结果取交集。

---

## 4. 任务 B：OCR 文档结构

### 4.1 设计决策（重要）

- 本轮采用**确定性后处理**，不改视觉模型 Prompt，不重写 OCR 文本。
- 原则：**只给已有 paragraph 文本“贴类型”，绝不改变文本内容**。
  因此 `canonical_text` 不变 → `unit_id`/`chunk_id`/向量全部不变 →
  **无需重建 Chroma**，只需回填 `document_units.structure_json`。
- 表结构转换（把 OCR 的 FAQ 等文本变成 Markdown 表格）会改变 canonical text，
  推迟到下一轮，避免本轮触发重建索引。
- 所有 OCR 块继续带 `attributes.ocr=True`；另加
  `attributes.ocr_structure = "heuristic-v1"`，便于未来识别是哪一代规则产生的结构。

### 4.2 新增模块 `backend/services/parsers/ocr_structure.py`

纯函数 `classify_ocr_blocks(text: str, *, ocr_kind: str) -> list[BlockDraft]`：

1. 输入：视觉模型返回的原始文本；
2. 先用与现在相同的 `normalize_text` + 空行切分，保证段落集合与当前
   `split_paragraphs` 完全一致（否则会改变 canonical_text）；
3. 对每个段落按规则判定 `block_type`：

| 规则（优先级从高到低） | 判定 | 示例 |
|---|---|---|
| R1 章节标题：`^(第[一二三四五六七八九十百\d]+[章节部分篇])` | heading, level 由“第X章”类推导 | `第四章 系统维护` |
| R2 中文编号标题：`^[一二三四五六七八九十]+、` 且段长 ≤ 40 字、不以句末标点结尾 | heading | `四、系统维护与更新` |
| R3 数字编号标题：`^\d+(\.\d+){0,2}[\s、.．]` 且段长 ≤ 40 字、不以句末标点结尾 | heading，`3.2` 两层 → level 2 | `3.2 通信与网络` |
| R4 列表项：以 `·`/`•`/`-`/`*`/`（数字）`/`数字.` 开头 | list_item，层级由前缀缩进/编号深度推导 | `·支持 5G 双卡双待` |
| R5 默认 | paragraph | 其余 |

- R2/R3 的“短标题 + 无句末标点”是保守条件，减少把正文误判为标题；
- 无法确定层级时 heading_level 默认 1，list_level 默认 0；
- 每个 BlockDraft 都带上 `ocr_attributes(kind)`，保持 AI 徽标；
- 若分类结果与原段落集合长度不一致，直接回退为全部 paragraph（fail-safe），
  绝不改变文本。

### 4.3 接入点

1. `backend/services/parsers/pdf.py`
   - A 层：`recognized = run_vision(...)` 成功后，
     `drafts = classify_ocr_blocks(recognized, ocr_kind="scanned_page")`
     替换现在的 `split_paragraphs` + paragraph 循环；
   - B 层图片/图表说明仍为 paragraph（描述文本不是文档正文结构）。
2. `backend/services/parsers/docx.py`
   - C 层：图片识别文本改用 `classify_ocr_blocks(..., ocr_kind="embedded_image")`，
     但只启用 R4/R5（不把图片内 UI 短文本误判为 heading，可加参数控制）。
3. `backend/services/parsers/pptx.py`
   - C 层同 DOCX：保守启用列表/段落规则，标题规则默认关闭或严格化。
4. 参数化开关：`classify_ocr_blocks(text, allow_headings: bool = True,
   allow_lists: bool = True)`，由各 adapter 按场景调用。

### 4.4 回填生产数据（不改向量）

新增 `scripts/backfill_ocr_structure.py`（只读原文件 → 重算 units → 安全替换）：

1. 读取 `documents WHERE status='ready'`；
2. 对每份文档重新 `parse_document_structure`（与上传同配置，含 OCR 缓存命中）；
3. **逐 unit 强校验**：
   - 新旧 `unit_id` 相同（locator 未变）；
   - 新旧 `canonical_text` 完全相同（证明没有文本漂移）；
   - 新旧 chunk 可由 `chunk_units` 复算得到相同 `chunk_id/content` 数量；
4. 全部校验通过后，事务性 `replace_document_units` 写入新 `structure_json`；
5. 提供 `--dry-run` 与 `--only-document <id>`；发现任何不一致立即终止，不写库；
6. 不触碰 Chroma、不修改 chunk_count、不修改原文件。

> 这是本方案最关键的安全阀：宁可回填失败，也不能让结构 JSON 与 Chroma 中的
> offset/内容语义脱节。

### 4.5 前端

`StructuredRenderer` 已支持 heading/list_item/table 渲染（
`web/src/components/StructuredRenderer.tsx:47-74`），本任务通常无需改前端；
OCR 块继续显示橙色“AI 识别”徽标。

### 4.6 测试

- 单元测试覆盖 R1-R5 及 fail-safe 回退；
- 强不变量测试：分类后 `"\n\n".join(block.text) == 原始 canonical_text`；
- PDF A 层、DOCX/PPTX C 层集成 fixture；
- `backfill_ocr_structure.py` 的 dry-run / 不一致拒绝 / 成功替换测试。

---

## 5. 任务 D：OCR 原图对照预览（你的想法评估）

### 5.1 结论：技术复杂度“中低”，容易实现，推荐本轮纳入

原因：现有数据契约已经为它留好了位置——`ParsedBlock.attributes` 是
`dict[str, str|int|bool]`，已随 `structure_json` 落库并原样传到前端
（`web/src/api/types.ts:87`），所以**不需要改表结构、不需要改核心模型**。
只需做三件事：入库时把图片落盘并写一个属性引用、加一个只读图片接口、
前端在 OCR 块旁渲染图片。

### 5.2 总体设计

```text
入库时（parser，仅 OCR_IMAGE_PREVIEW_ENABLED=true）
  PDF A/B 层：渲染页面 JPEG（已有 render_page_image）
  DOCX/PPTX C 层：内嵌图片 blob 转 JPEG（已有 normalize_image_bytes）
       ↓
  ocr_image_store.save(file_hash, object_id, jpeg_bytes)
       → image_ref = sha256(file_hash + object_id)（16 位短 id）
       → 写入 data/ocr_images/<image_ref>.jpg（tmp + os.replace 原子写）
       ↓
  OCR block.attributes["ocr_image_ref"] = image_ref
  attributes["ocr_image_kind"] = scanned_page | page_images | embedded_image

预览时（只读）
  document_units.structure_json 已含 attributes
       ↓
  前端 StructuredRenderer 对含 ocr_image_ref 的块：
     GET /api/documents/{document_id}/ocr-images/{image_ref}
       ↓
     左：原图（AntD Image，可点击放大）
     右：AI 识别文本（橙色徽标）
```

### 5.3 后端改动清单

1. `backend/config.py`
   - 新增 `OCR_IMAGE_PREVIEW_ENABLED`（默认 false；本地 `.env` 验收期 true）；
   - 新增 `OCR_IMAGE_DIR`（默认 `data/ocr_images`）；
   - `OCR_IMAGE_MAX_EDGE` 可选，复用现有 `OCR_MAX_IMAGE_EDGE`。
2. 新增 `backend/services/ocr_image_store.py`
   - `save(file_hash, object_id, image_bytes) -> image_ref`：幂等（存在即返回）；
   - `resolve(image_ref) -> Path | None`：只允许纯 hex id，防路径穿越；
   - 写入用 tmp + `os.replace`；写失败返回 None（不阻塞入库，前端降级隐藏图片）。
3. parser 接入
   - `pdf.py`：A 层在调用 `run_vision` 前把 `render_page_image` 的 bytes 保存到
     变量；识别成功且开关开启时 `save` 并把 ref 附加到该页所有 OCR blocks；
     B 层同理（页面图）。
   - `docx.py`：已有 `image_blobs` 和 `normalized`，在 `run_vision` 成功后 save
     并把 ref 附加到该图片识别块。
   - `pptx.py`：同上。
   - 开关关闭时：行为与现在完全一致，不产生任何图片文件或属性。
4. 新增只读接口 `GET /api/documents/{document_id}/ocr-images/{image_ref}`
   - 路由放 `backend/routers/upload.py` 或新建 `backend/routers/ocr_images.py`；
   - 校验：开关开启、document ready、image_ref 格式；
   - **关联性校验**：遍历该 document 的 units 的 blocks attributes，
     确认确实存在 `ocr_image_ref == image_ref`（复用 unit_store 读取），
     避免任意 id 扫盘；
   - 命中返回 `FileResponse(path, media_type="image/jpeg",
     headers={"Cache-Control": "private, no-store"})`；
   - 文档/图片/属性三者任一不一致 → 404。
5. 删除联动
   - `upload.py` 删除文档时：先读取该文档 units 收集 `ocr_image_ref`，
     对每个 ref 检查“其他文档 units 是否仍引用”，无引用才删除图片文件；
     删除失败只记 warning，不阻断文档删除。

### 5.4 前端改动清单

1. `web/src/api/client.ts`：新增 `ocrImageUrl(documentId, imageRef)` 辅助函数
   （encodeURIComponent）。
2. `web/src/components/StructuredRenderer.tsx`：
   - 组件增加可选 props `documentId?: string`、`showOcrImages?: boolean`；
   - `BlockView` 检测 `block.attributes.ocr_image_ref`：
     - 有 ref 且未渲染过 → 渲染两栏布局：左图 + 右文本；
     - 页面级 OCR 多个 block 共享同一 ref → 只在第一次出现时显示图片，
       后续块只显示文本 + 徽标（组件内用 `Set` 去重）；
     - 图片用 AntD `<Image width={...} src=... />`，自带预览放大。
3. `DocumentPreviewDrawer.tsx`：传入 `documentId={preview.document_id}`、
   `showOcrImages`；
4. `CitationList.tsx` 的引用预览：同样传入 document_id，让引用场景也能对照；
5. `api/types.ts` 无需改（attributes 已支持任意字符串值）。
6. 样式：`.ocr-compare` flex 布局；窄屏纵向堆叠；图片最大宽度约 45%；
   文本侧保持 OCR 徽标与高亮逻辑（引用高亮不受影响）。

### 5.5 工作量与风险

| 项目 | 估计 |
|---|---|
| 后端新增/修改 | 约 250-350 行（含测试） |
| 前端新增/修改 | 约 120-180 行（含样式、测试） |
| 测试 | parser 属性、图片接口 200/404/越权、渲染去重、删除清理 |

风险与对策：

| 风险 | 对策 |
|---|---|
| 图片占磁盘（单页 JPEG 约 100-500KB） | 独立开关；默认关闭；复用 1600px 压缩；本轮只加“删除文档级联清理”，容量淘汰归入下一轮视觉缓存优化 |
| 隐私：预览返回原始图片文字 | 与 `CITATION_PREVIEW_ENABLED` 同级治理：开关默认 false，只读接口校验 document/block 归属，`no-store` |
| 页面级 OCR 一张图对应多个块，重复显示 | 前端按 ref 去重，只在 unit 内首次出现时显示图片 |
| EMF/WMF 等不可转换图片 | `normalize_image_bytes` 失败则无 ref，前端不显示图片，不影响 OCR 文本 |
| 已有 31 份 ready 文档没有图片 ref | backfill 脚本（任务 B）一并重新解析生成 ref；dry-run 先行 |
| 原文件缺失导致 backfill 无法渲染 | 脚本逐文档失败即终止并列出缺失文件，不半途写库 |

### 5.6 对你问题的直接回答

- **技术复杂度**：中低。没有新表、没有向量/索引变更，核心是“文件落盘 + 一个
  string 属性 + 一个只读图片接口 + 一个前端组件”。
- **是否容易实现**：容易，且与任务 B 共享回填流程，一次重解析同时解决
  “结构类型”和“图片引用”两件事。
- **推荐范围**：本轮做 A 层（整页对照）和 C 层（内嵌图片对照）；B 层描述块
  也顺带支持（同一机制）。这样用户在全文预览和引用预览都能核对。

---

## 6. 执行序列与提交边界

建议按 6 个提交推进（每个提交一个主题，遵守项目 Git 纪律）：

1. `fix: structured-v2 eval metrics and wording cleanup`（任务 A）
2. `feat: document name fuzzy search`（任务 C）
3. `feat: heuristic OCR block structure`（任务 B 代码 + 测试）
4. `feat: OCR image reference store and preview endpoint`（任务 D 后端）
5. `feat: OCR image side-by-side preview in SPA`（任务 D 前端）
6. `chore: backfill script and acceptance evidence`（回填脚本 + 验收记录）

每个提交前展示 `git diff --stat`，确认后再提交。

---

## 7. 测试与验收清单（最终手动验收用）

### 7.1 自动测试

```powershell
# 后端全量
.\.venv\Scripts\python.exe -m pytest -q
# 前端
Set-Location web
npm.cmd test -- --reporter=dot
npm.cmd run lint
npm.cmd run build
Set-Location ..
```

### 7.2 手动验收步骤

1. **文档搜索**：知识库管理页输入 `scan` / `计算机网络` / `.md` / `v1`，
   确认列表即时过滤；清除搜索词恢复全量；搜索词 + 标签组合取交集。
2. **OCR 结构**：打开 `scan.pdf` 预览，确认“四、系统维护与更新”“五、安全注意
   事项与警告”等渲染为标题层级，`·` 项渲染为列表；所有 OCR 块有
   “AI 识别，可能存在误差”徽标；原生文档结构无回归。
3. **OCR 原图对照**：预览中 OCR 内容旁出现原图，可点击放大；图片与识别文本
   能对应；`111.docx` 的内嵌图片识别块同样显示图片；无图/无 ref 时布局不塌。
4. **引用预览**：对 OCR 文档提问并点击引用，引用预览同样显示结构与原图对照，
   高亮区间正确。
5. **structured-v2**：预览页 parser_version 显示 `structured-v2`；
   `GET /api/documents` 与预览正常；记录一次 Dataset v3 无 LLM 检索基线，
   确认 MD/PPTX 引用指标口径已修正。

### 7.3 数据安全

- backfill 脚本先 `--dry-run`，人工核对输出后再正式执行；
- 不执行任何泛化递归删除；删除文档时只删除该文档引用且无共享的图片文件；
- 生产库回填前备份 `data/metadata.db`（沿用项目既有备份惯例）。

---

## 8. 方案未决项（需要你拍板）

1. **OCR 标题识别范围**：DOCX/PPTX 内嵌图片是否也启用 heading 规则？
   本方案默认“图片内文字不判 heading，只判列表/段落”，避免 UI 短文本误判。
   若你希望图片内标题也识别，需在方案 4.3 中放开参数。
2. **回填范围**：是否本轮就对生产 31 份 ready 文档执行 backfill，还是先在
   1-2 份文档（如 scan.pdf、111.docx）上试点后全量？
3. **原图对照开关默认值**：建议代码/`.env.example` 默认 false，
   本地 `.env` 验收期 true；是否同意？
4. **表格结构**：OCR 表格转 Markdown 会改变 canonical text 并触发重建，
   本方案推迟到下一轮；是否接受？


---

## 9. 实施记录（2026-08-16，最终版）

### 9.1 本轮具体优化了什么

| 主题 | 优化内容 | 关键文件 |
|---|---|---|
| 1. structured-v2 迁入收尾 | 评测指标不再硬编码 v1：改为依据 `unit_id + location_label + locator_kind` 做版本无关判断，structured-v2 的 MD/PPTX 引用可正确统计；统一 v1 残留文案与测试 fixture | `eval/metrics.py`、`eval/adapters.py`、`backend/services/structured_citation_preview.py`、`backend/models/schemas.py`、`backend/routers/upload.py`、`tests/test_eval_metrics.py`、`tests/test_tag_retrieval.py` |
| 2. 文档名称模糊搜索 | `GET /api/documents` 新增 `q` 参数（大小写不敏感子串匹配，通配符按字面处理），与标签筛选取交集；前端新增 300ms 防抖搜索框，搜索词在刷新/上传/标签操作后保持 | `backend/routers/upload.py`、`tests/test_api.py`、`web/src/api/client.ts`、`KnowledgePage.tsx`、`DocumentTable.tsx`、`knowledgePage.test.tsx` |
| 3. OCR 文档结构（A 方案） | 新增确定性规则：独立短标题→`heading`，纯列表段→`list_item`，混合“标题+正文”段保留 `paragraph` 并新增 `ocr_leading_heading_level` 属性；前端把首行渲染为标题、保留换行；**文本零改写、未升版本、未重建索引** | `backend/services/parsers/ocr_structure.py`、`pdf.py`、`docx.py`、`pptx.py`、`StructuredRenderer.tsx`、`tests/test_ocr_structure.py` |
| 4. OCR 原图对照预览 | 解析期把 OCR 所用原图（PDF 页渲染图 / DOCX·PPTX 内嵌图）落盘 `data/ocr_images/<ref>.jpg`，block 属性写入 `ocr_image_ref`；新增只读接口校验文档-块-文件三者归属；前端左右对照、点击原图新标签打开；删除文档时清理无共享引用图片 | `backend/services/ocr_image_store.py`、`backend/config.py`、三个 parser、`backend/routers/upload.py`、`StructuredRenderer.tsx`、`index.css`、`tests/test_ocr_image_store.py`、`tests/test_ocr_image_endpoint.py` |
| 5. 生产数据回填工具 | `scripts/backfill_ocr_structure.py`：默认 dry-run 零写入；逐 unit 校验 unit_id/locator/canonical_text 与重算 chunk 数；`--apply` 前自动备份 metadata.db；支持按文档、跳过 canonical 变化文档 | `scripts/backfill_ocr_structure.py`、`tests/conftest.py`（隔离根支持） |

### 9.2 遇到的问题与如何解决

| # | 问题 | 原因 | 解决方案 |
|---|---|---|---|
| P1 | 前端图片全部不显示 | `api.ocrImageUrl` 漏掉后端路由中的 `/id/` 路径段，浏览器请求 404 | 修正 URL 为 `/api/documents/id/{document_id}/ocr-images/{image_ref}`，新增 `ocrImageUrl.test.ts` 回归 |
| P2 | 图片旁大面积空白、hover 高亮覆盖整块 | AntD `Image` 根节点宽度算法 + 遮罩覆盖整个容器，CSS 无法稳定收敛 | 改为原生 `<img>`（点击新标签打开原图），容器 `width: fit-content` + `line-height: 0`，图片 `max-height: 320px` 自适应 |
| P3 | 全量 dry-run 调用真实视觉 API 并触发 RateLimit | 部分文档旧数据没有 OCR 块，但 dry-run 仍以当前 OCR 开关重解析 | 按文档判断：旧 units 无 `ocr` 块时回填解析阶段临时关闭 OCR；dry-run 另关闭图片落盘，确保零副作用、零 API |
| P4 | 全量回填在 Chap02/Chap01 触发 canonical_text 安全闸 | 重解析时新增了旧库不存在的 OCR 图片文字（真实文本变化） | 严格保持“文本变了就不允许无版本回填”；脚本改为汇总所有变化并支持 `--skip-canonical-changes`；Chap01 由用户手动重传（新版本已含 52 units 与图片引用） |
| P5 | `scan.pdf` 重传出现 PyPDF2 回退提示 | 该文件 xref 结构异常，PyPDF2 无法解析 | 非致命 warning，系统按既有设计回退 pdfium 成功入库；无需修复 |
| P6 | 测试环境无法在系统临时目录建隔离目录 | 沙箱对系统临时目录写入受限 | `tests/conftest.py` 增加可选 `PYTEST_ISOLATION_ROOT`，默认行为不变；受限环境指定工作区内隔离根 |

### 9.3 验收与回填结果

- 后端全量：`482 passed, 3 skipped`；前端：`32 passed`；lint 0；build 成功（仅保留既有 >500kB chunk 体积警告）。
- 试点：`scan.pdf`、`111.docx` 回填并人工验收通过（标题/列表结构、原图对照、搜索均正常）。
- 全量：`--apply --skip-canonical-changes` 回填 30/31 份；跳过 Chap01（已手动重传完成）。
- 生产库：schema v3、31 ready、`document_units` 100% `structured-v2`、`data/ocr_images/` 27 张原图；Chroma 未改动。
- 备份：`data/backups/metadata-before-ocr-structure-2026-08-16T131220+0000.db`。

### 9.4 未决/顺延事项

- PPTX 每张幻灯片的整页快照：需引入渲染引擎，与 PPTX 文字提取优化一起作为下一轮专项；
- OCR 表格转 Markdown：需改 canonical_text 并升版本重建索引，下一轮；
- 检索调优（同文档配额、BM25 缓存、视觉缓存淘汰等）：下一轮；
- 验收期间产生的同名重复文档版本（旧 Chap01、旧 scan.pdf 等）待清理。
