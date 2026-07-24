# RAG Next Stage Phase 0 Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a reproducible pre-change baseline, remove known evaluation-entry assumptions, and document the exact future cleanup boundary without deleting production-derived data.

**Architecture:** Keep Phase 0 confined to the existing evaluation layer and documentation. Centralize the legacy v2 dataset default, make document-name and report-location formatting ready for MD/PPTX metadata, and preserve all current RAG behavior through executable regression tests and a checked-in baseline record. A real 27-question LLM run remains mandatory; if no real API key is available, stop at that checkpoint rather than substituting mocked answers.

**Tech Stack:** Python 3.14, pytest, argparse, existing `eval` CLI, Markdown, Git worktree

---

## File map

- Create: `docs/superpowers/baselines/2026-07-24-phase-0-baseline.md`
  - Immutable inventory, hashes, test evidence, production-path snapshot, deferred cleanup list, and behavior contract.
- Modify: `eval/eval.py`
  - Define one `DEFAULT_DATASET` and use it for every legacy v2-compatible evaluation command.
- Modify: `eval/metrics.py`
  - Recognize `.md` and `.pptx` when normalizing document names.
- Modify: `eval/report.py`
  - Format a generic source location with an explicit `location_label`, slide fallback, and page fallback.
  - Remove the unsupported fixed “<5% noise” claim from newly generated reports.
- Modify: `eval/README.md`
  - Document v2 as the default legacy regression dataset and describe measured, rather than assumed, LLM variation.
- Modify: `tests/test_eval_cli.py`
  - Lock all CLI defaults to `questions_v2.json`.
- Modify: `tests/test_eval_metrics.py`
  - Lock MD/PPTX document-name matching.
- Modify: `tests/test_eval_report.py`
  - Lock generic page/slide/location-label rendering and the corrected uncertainty wording.

### Task 1: Capture the pre-change evidence and safety boundary

**Files:**
- Create: `docs/superpowers/baselines/2026-07-24-phase-0-baseline.md`

- [x] **Step 1: Record the immutable Git and test baseline**

Add the branch-point commit `5d2a2a35112f803bb6fcdc1604ebee47e0eb17ee`, the command:

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest -q
```

and the observed result:

```text
251 passed, 3 skipped, 19562 warnings in 14.88s
```

Classify the warning count as a known log-signal problem, not a Phase 0 failure and not evidence that the warnings are harmless.

- [x] **Step 2: Record dataset and corpus hashes**

Use the already measured SHA-256 values:

```text
questions.json
  version=1 questions=21 answerable=17 unanswerable=4
  sha256=d1320daee4af5b2b0dec6bb605fbabb587a1715e05269c457efec68f2f25c9a7
questions_v2.json
  version=2 questions=27 answerable=22 unanswerable=5
  sha256=794817463e5a80b759b829ab469893d5e3d143b93e8f11306c44d636de7cbb73
2024技术发展报告_RAG测试文档.docx
  sha256=c377cbe9548947f5cb74b81bbc6d7c54b0b813b83813dd6e26a479cb4694bcdc
AI Agent文献综述_v3.docx
  sha256=16b8abbf6b1e03233685bc8e7c41615c8fbcea2ecc843559c2883d8b8883f279
AI技术概述_RAG测试文档.txt
  sha256=c17130f2bcc846a88b8280a33af450c6c1e9254b925a6f9cb33e81cabc2a1cfd
LLM应用指南_RAG测试文档.pdf
  sha256=8c40a27b61526e05fb1656cf2de6118dd8ab7dcc068c382eea3391d023b8b3a0
```

Also record that `eval/results/` contains 46 checked-in historical evidence files.

- [x] **Step 3: Record production paths and the deferred cleanup list**

Document the resolved current production paths:

```text
UPLOAD_DIR=D:\ProgramFiles\RAG\data\uploads
CHROMA_PERSIST_DIR=D:\ProgramFiles\RAG\data\chroma_p2_4a
METADATA_DB_PATH=D:\ProgramFiles\RAG\data\metadata.db
```

State that `METADATA_DB_PATH` is absent from `.env` and therefore resolves through `backend/config.py`.

List these exact future cleanup candidates, but explicitly forbid deletion before Phase 2 isolated tests pass:

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

The preservation list must include `eval/corpus/`, `eval/dataset/`, `eval/results/`, the repository source, and the real `.env`.

- [x] **Step 4: Lock the retained behavior contract**

Reference these existing executable tests as the minimum contract:

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

Do not rename test classes speculatively; verify the collected node IDs with:

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest --collect-only -q tests/test_api.py tests/test_retriever.py tests/test_citation.py tests/test_citation_preview.py
```

- [x] **Step 5: Commit the baseline record**

```powershell
git add docs/superpowers/baselines/2026-07-24-phase-0-baseline.md
git commit -m "docs: capture phase 0 RAG baseline"
```

### Task 2: Make legacy regression v2 the single default dataset

**Files:**
- Modify: `tests/test_eval_cli.py`
- Modify: `eval/eval.py`
- Modify: `eval/README.md`

- [x] **Step 1: Write failing CLI-default tests**

Append:

```python
import sys
from pathlib import Path

import eval.eval as eval_cli


@pytest.mark.parametrize(
    ("argv", "handler_name"),
    [
        (["eval", "run", "--label", "test"], "cmd_run"),
        (
            [
                "eval",
                "threshold-sweep",
                "raw.jsonl",
                "--label",
                "test",
                "--thresholds",
                "0.67",
                "--output-dir",
                "out",
            ],
            "cmd_threshold_sweep",
        ),
        (
            [
                "eval",
                "retrieval-sweep",
                "--label",
                "test",
                "--candidate-ks",
                "15",
            ],
            "cmd_retrieval_sweep",
        ),
        (["eval", "hybrid-ablation", "--label", "test"], "cmd_hybrid_ablation"),
    ],
)
def test_all_evaluation_commands_default_to_questions_v2(
    monkeypatch, argv, handler_name
):
    captured = {}

    def capture(args):
        captured["dataset"] = args.dataset

    monkeypatch.setattr(eval_cli, handler_name, capture)
    monkeypatch.setattr(sys, "argv", argv)

    eval_cli.main()

    assert Path(captured["dataset"]).name == "questions_v2.json"
```

- [x] **Step 2: Run the tests and verify RED**

Run:

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_cli.py::test_all_evaluation_commands_default_to_questions_v2 -q
```

Expected: three parameter cases fail because they resolve to `questions.json`; `hybrid-ablation` already passes.

- [x] **Step 3: Add and use one default constant**

Near `EVAL_DIR` in `eval/eval.py`, add:

```python
DEFAULT_DATASET = EVAL_DIR / "dataset" / "questions_v2.json"
```

Replace all four command defaults with:

```python
default=str(DEFAULT_DATASET)
```

Update `eval/README.md` so `run`, `threshold-sweep`, `retrieval-sweep`, and `hybrid-ablation` are documented as using the 27-question v2 legacy regression set unless `--dataset` is supplied. Keep `questions.json` documented as historical v1, not the default.

- [x] **Step 4: Run the focused tests and verify GREEN**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_cli.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```powershell
git add eval/eval.py eval/README.md tests/test_eval_cli.py
git commit -m "fix: default evaluation commands to dataset v2"
```

### Task 3: Support MD and PPTX document-name matching

**Files:**
- Modify: `tests/test_eval_metrics.py`
- Modify: `eval/metrics.py`

- [x] **Step 1: Write failing normalization tests**

Add to `TestNormDocName`:

```python
    def test_strips_markdown_extension(self):
        assert norm_doc_name("《课程大纲.md》") == norm_doc_name("课程大纲")

    def test_strips_powerpoint_extension(self):
        assert norm_doc_name("课件.PPTX") == norm_doc_name("课件")
```

- [x] **Step 2: Run the tests and verify RED**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_metrics.py::TestNormDocName::test_strips_markdown_extension tests/test_eval_metrics.py::TestNormDocName::test_strips_powerpoint_extension -q
```

Expected: both comparisons fail because `.md` and `.pptx` remain in the normalized names.

- [x] **Step 3: Implement the minimal extension change**

Change the extension tuple in `norm_doc_name()` to:

```python
for ext in (".pdf", ".docx", ".txt", ".doc", ".md", ".pptx"):
```

- [x] **Step 4: Run focused tests and verify GREEN**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_metrics.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```powershell
git add eval/metrics.py tests/test_eval_metrics.py
git commit -m "fix: normalize markdown and powerpoint document names"
```

### Task 4: Remove page-only report assumptions and unsupported variance claims

**Files:**
- Modify: `tests/test_eval_report.py`
- Modify: `eval/report.py`
- Modify: `eval/README.md`

- [x] **Step 1: Write failing location-format tests**

Append:

```python
def test_format_retrieval_debug_prefers_explicit_location_label():
    from eval.report import _format_retrieval_debug

    row = {
        "content": "标题",
        "metadata": {
            "document_name": "slides.pptx",
            "location_label": "第 8 张幻灯片 · RAG 评测",
            "slide_number": 8,
            "page_number": 99,
        },
        "distance": 0.2,
    }

    assert "《slides.pptx》第 8 张幻灯片 · RAG 评测" in _format_retrieval_debug(row)


def test_format_retrieval_debug_uses_slide_fallback():
    from eval.report import _format_retrieval_debug

    row = {
        "content": "标题",
        "metadata": {"document_name": "slides.pptx", "slide_number": 8},
        "distance": 0.2,
    }

    assert "《slides.pptx》第 8 张幻灯片" in _format_retrieval_debug(row)
```

The existing sparse-only page test continues to lock the legacy `第7页` fallback.

- [x] **Step 2: Write failing uncertainty-wording tests**

Add:

```python
def test_generated_report_does_not_claim_unmeasured_five_percent_noise(tmp_path):
    payload = _payload()

    write_report_md(tmp_path, payload, [], {})

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "波动 <5%" not in report
    assert "实际波动需通过重复运行测量" in report


def test_readme_does_not_claim_unmeasured_five_percent_noise():
    readme = (
        Path(__file__).parents[1] / "eval" / "README.md"
    ).read_text(encoding="utf-8")

    assert "波动 <5%" not in readme
    assert "实际波动需通过重复运行测量" in readme
```

- [x] **Step 3: Run the tests and verify RED**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_report.py -q
```

Expected: the new slide/location tests fail and the unsupported `<5%` wording tests fail.

- [x] **Step 4: Implement generic location formatting**

Add:

```python
def _format_location(metadata: dict) -> str:
    location_label = metadata.get("location_label")
    if location_label:
        return str(location_label)
    slide_number = metadata.get("slide_number")
    if slide_number is not None:
        return f"第 {slide_number} 张幻灯片"
    page_number = metadata.get("page_number")
    if page_number is not None:
        return f"第{page_number}页"
    return "位置未知"
```

Use it in `_format_retrieval_debug()`:

```python
return (
    f"《{metadata.get('document_name')}》{_format_location(metadata)}"
    f"({', '.join(scores)})"
)
```

- [x] **Step 5: Replace the unsupported wording**

In generated reports and `eval/README.md`, replace the fixed noise threshold with:

```text
LLM 回答类指标可能存在非确定性；在取得重复运行证据前不预设固定噪声阈值，实际波动需通过重复运行测量。检索类指标在模型、语料和配置完全固定时应保持确定。
```

- [x] **Step 6: Run focused tests and verify GREEN**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest tests/test_eval_report.py -q
```

Expected: all tests pass.

- [x] **Step 7: Commit**

```powershell
git add eval/report.py eval/README.md tests/test_eval_report.py
git commit -m "fix: generalize evaluation source locations"
```

### Task 5: Produce isolated v2 baselines and close Phase 0

**Files:**
- Modify: `docs/superpowers/baselines/2026-07-24-phase-0-baseline.md`
- Runtime only, ignored: `eval/runs/phase0-prechange-retrieval-v2/`
- Runtime only, ignored: `eval/runs/phase0-prechange-llm-v2/`

- [x] **Step 1: Run a no-LLM retrieval baseline**

Load non-secret configuration values from `D:\ProgramFiles\RAG\.env` into the process, set `DEEPSEEK_API_KEY=offline-placeholder` only because this command never calls the LLM, and run:

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m eval.eval retrieval-sweep `
  --label phase0-prechange-retrieval-v2 `
  --dataset eval/dataset/questions_v2.json `
  --candidate-ks 15 20 30
```

Expected: isolated Chroma, SQLite, and uploads are created only under `eval/runs/phase0-prechange-retrieval-v2/`; a retrieval report and raw retrieval JSONL files are produced.

- [x] **Step 2: Verify isolation**

Run:

```powershell
Get-ChildItem -Force eval/runs/phase0-prechange-retrieval-v2
git status --short
```

Expected: runtime outputs are ignored; no file under `D:\ProgramFiles\RAG\data` has been modified by the evaluation.

- [x] **Step 3: Check for a real LLM credential**

Check presence without printing the value:

```powershell
if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    throw 'DEEPSEEK_API_KEY is missing'
}
```

If missing, stop Phase 0 here and report it as blocked. Do not use `offline-placeholder`, a mock, or generated answers for the full run.

- [ ] **Step 4: Run the real 27-question isolated baseline**

With a real key present, run:

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m eval.eval run `
  --label phase0-prechange-llm-v2 `
  --dataset eval/dataset/questions_v2.json
```

Expected: 27 questions complete and `raw_answers.jsonl`, `report.json`, `report.md`, and `review.md` exist under the isolated run directory.

- [ ] **Step 5: Record measured results**

Append the exact retrieval and LLM aggregate metrics, run configuration, report paths, and timestamps to the baseline document. State whether the full baseline is complete; never infer a quality improvement from a single run.

- [x] **Step 6: Run the complete regression suite**

```powershell
& 'D:\ProgramFiles\RAG\.venv\Scripts\python.exe' -m pytest -q
```

Expected: at least the branch-point result of `251 passed, 3 skipped`, plus the newly added tests; zero failures.

- [x] **Step 7: Run a clean-status and diff review**

```powershell
git status --short
git diff --check
git diff master...HEAD --stat
```

Expected: only planned source, test, and documentation files are tracked; no `.env`, `data/`, or `eval/runs/` content appears.

- [x] **Step 8: Commit the final evidence update**

```powershell
git add docs/superpowers/baselines/2026-07-24-phase-0-baseline.md
git commit -m "docs: record phase 0 evaluation evidence"
```

## Self-review

- Spec coverage: Phase 0 asset integrity, full tests, v2 isolated evaluation, default dataset, MD/PPTX name normalization, location-neutral reports, cleanup boundary, and retained behavior are all mapped above.
- Explicit non-goals: no parser, database, upload, retrieval, citation, or chat behavior is changed; no production-derived data is deleted.
- Placeholder scan: no TBD/TODO/“similar to” steps remain. The API-key gate is an explicit stop condition, not a substituted result.
- Type consistency: `location_label`, `slide_number`, and `page_number` match the approved `SourceLocator` design; legacy page metadata remains supported.
