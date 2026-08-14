# 阶段 2：OCR / 多模态识别实施计划

> 依赖：阶段 1（标签体系）。
> 设计依据：`docs/superpowers/specs/2026-08-13-rag-v2-optimization-design.md` 第 4 节。
> 原则：测试先行、视觉调用默认关闭、失败不阻塞入库、生产数据切换必须隔离验收。

## 1. 目标与边界

- 目标：A 层扫描 PDF 页 OCR、B 层 PDF 页内图表说明、C 层 DOCX/PPTX 内嵌图片识别，
  产出带 `ocr=True` 标记的结构化 blocks，可检索、可引用、可预览；
- 边界：MD 外部相对路径图片不支持（data-URI 可选）；聊天框传图问答不做；不新增
  视觉调用以外的 LLM 调用；DeepSeek 问答链路不动。

## 2. 任务分解（按顺序执行）

### 2.1 配置

- 文件：`backend/config.py`、`.env.example`
- 内容：
  - `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL`（默认 MiMo `mimo-v2.5`，
    base_url 以官方平台文档为准）；
  - `OCR_ENABLED=false`、`OCR_PDF_SCANNED=false`、`OCR_PDF_PAGE_IMAGES=false`、
    `OCR_EMBEDDED_IMAGES=false`（三层独立，总开关优先）；
  - `OCR_MAX_IMAGE_EDGE=1600`、`OCR_MAX_PER_DOCUMENT=20`（页+图总数上限）、
    `OCR_TIMEOUT_SECONDS=60`、`VISION_CACHE_DIR=data/vision_cache`；
  - 全部默认关闭：不配置时系统行为与现状完全一致。
- 测试：布尔严格解析与默认值断言。

### 2.2 视觉客户端

- 新文件：`backend/services/vision.py`
- 内容：OpenAI 兼容客户端（复用 openai SDK），`recognize_text(image_bytes)`、
  `describe_image(image_bytes, context)`；base64 data URL 传图；超时、重试
  （有限次数）、错误归一化（调用失败向上抛可识别的 `VisionError`）；模块级客户端
  惰性创建；函数签名便于单测注入 mock。
- 失败测试先行：mock HTTP 层验证请求体含图片、prompt 正确、错误分类正确。

### 2.3 渲染与缓存

- 新依赖：`pypdfium2`（PDF 页渲染）、`Pillow`（缩放/编码）；加入 `requirements.txt`
  与 `requirements-ci.txt`（CI 子集若不含本地模型则确认其归属）。
- 新文件：`backend/services/vision_cache.py`
- 内容：
  1. `render_pdf_page(file_path, page_index) -> bytes`（pypdfium2 渲染，150dpi 起，
     再按长边 ≤ `OCR_MAX_IMAGE_EDGE` 缩放为 JPEG）；
  2. 缓存键 = 文件 sha256 + 页/图标识 + 模型版本；读写原子（临时文件 + rename）；
  3. 页数/图片数上限计数与报错；命中缓存不发起网络调用。
- 失败测试先行：缓存命中不调 client；损坏缓存文件重算；上限触发明确报错。

### 2.4 A 层：扫描 PDF 页

- 文件：`backend/services/parsers/pdf.py`
- 内容：`extract_text()` 为空且 `OCR_PDF_SCANNED` 开 → 渲染 → 缓存 → `recognize_text`
  → 文本以 `paragraph` blocks 注入（attributes 带 `ocr=True`、`vision_model`）；
  关闭或失败 → 保留 `[第X页无可提取文本]` 占位并追加 warning。
- 失败测试先行：mock vision 返回文本 → blocks 与 offset 不变量成立；mock 失败 →
  warning 且占位保留；开关关闭 → 与当前 v1 输出逐字节一致。

### 2.5 B 层：PDF 页内图表说明

- 文件：`backend/services/parsers/pdf.py`
- 内容：页有文本且含图片对象（pypdfium2 对象遍历判定）且 `OCR_PDF_PAGE_IMAGES` 开 →
  整页渲染 → `describe_image`（prompt 明确「只描述图片/图表，不重复正文」）→ 产出
  `ocr=True` 的说明 blocks（置于该页 blocks 末尾）；
- 失败测试先行：含图 fixture 触发、无图页不触发、说明不混入正文位置、失败仅 warning。

### 2.6 C 层：DOCX/PPTX 内嵌图片

- 文件：`backend/services/parsers/docx.py`、`backend/services/parsers/pptx.py`
- 内容：
  1. DOCX：遍历段落 XML 中的 `pic` 引用定位图片关系 rId → 取 `related_parts[rId]`
     二进制 → 识别 → 在该段落后插入说明 block；
  2. PPTX：按既有 shape 阅读顺序，`shape.shape_type == PICTURE` → `shape.image.blob`
     → 识别 → 原位插入说明 block；
  3. 图片超限（像素/尺寸/数量）跳过并 warning；无 OCR 开关时行为与现状完全一致。
- 失败测试先行：含图 DOCX/PPTX fixture 原位插入与 offset 不变量；无开关时输出与
  现状一致；异常图片仅 warning。

### 2.7 版本升级与重建工具适配

- 文件：`backend/services/parsers/constants.py`、`scripts/rebuild_index.py`
- 内容：
  1. `PARSER_VERSION = "structured-v2"`；
  2. rebuild 脚本中写死 `structured-v1` 的文案与 chunk_id 校验规则改为读取
     `PARSER_VERSION` 常量；`--accept-chunk-count-change` 语义保持；
  3. eval/dataset v3 的 `parser_version` 同步 `structured-v2`，重跑 `dataset prepare`
     刷新 hash 并验证无 OCR 页文档 evidence/offset 不变；
  4. 相关测试（`tests/test_rebuild_index.py` 等）同步更新。
- 失败测试先行：重建脚本对新版本产物校验通过；eval prepare 后 evidence 零变化。

### 2.8 隔离验收与生产重建

1. 隔离目录完成全链路：上传真实扫描件（用户提供）→ 可检索 → 引用定位 → 预览带 OCR
   徽标；B/C 层用含图真实文档验证；
2. 确认 `OCR_ENABLED=false` 时上传、检索、引用行为与阶段 1 完全一致；
3. 向用户展示切换清单（见设计文档 3.4 节路径 A/B），确认后按 7.4 节安全流程执行
   生产切换；`rebuild_index.py` 在新目标目录重建全部文档。

## 3. 文件改动清单（预期）

```text
M  backend/config.py
M  .env.example
M  requirements.txt / requirements-ci.txt
A  backend/services/vision.py
A  backend/services/vision_cache.py
M  backend/services/parsers/constants.py
M  backend/services/parsers/pdf.py
M  backend/services/parsers/docx.py
M  backend/services/parsers/pptx.py
M  scripts/rebuild_index.py
M  eval/dataset/questions_v3.json（parser_version 同步）
M  eval/dataset/corpus_v3_manifest.json
A  tests/test_vision.py
A  tests/test_vision_cache.py
M  tests/test_pdf_adapter.py
M  tests/test_docx_adapter.py
M  tests/test_pptx_adapter.py
M  tests/test_rebuild_index.py
A  tests/fixtures/scanned_image_pdf.pdf（图片型 PDF 二进制 fixture）
```

## 4. 阶段验收标准

1. 三层开关全部默认关闭，关闭时输出与 structured-v1 逐字节一致；
2. mock 下单测覆盖：触发、回退、缓存、上限、warning、offset 不变量；
3. 至少一份真实扫描件可检索、可引用，预览显示 OCR 徽标；
4. eval v3 preflight 重跑通过，evidence 不变（无 OCR 页文档）；
5. 生产数据按确认路径切换为纯 `structured-v2`，切换证据存档；
6. 全量 pytest 绿色。

## 5. 风险与备注

- MiMo 计费与延迟：三层开关 + 缓存 + 上限控制；上线初期建议先开 A 层观察；
- 渲染依赖：pypdfium2 纯 pip 安装（BSD/Apache 许可），避免 PyMuPDF 的 AGPL 与
  pdf2image 的 poppler 依赖；
- OCR 文本为 AI 产物：始终带 `ocr` 标记，预览徽标明示，不伪装原生文本；
- 生产切换必须用户逐项确认，禁止泛化删除命令。
