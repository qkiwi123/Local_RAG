"""Build the deterministic CRUD_RAG v3 unanswerable-question fixture.

This builder is deliberately separate from ``prepare_dataset.py``.  It only
writes below ``eval/crud_rag_1000/v3`` and never rewrites the existing 100
question dataset, the 1000-document corpus, or its manifest.  It performs
offline lexical/near-duplicate screening; Dense/BM25/Hybrid retrieval audit
slots are emitted as ``pending`` for the later retrieval stage.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SOURCE_DIR = ROOT / "source"
CORPUS_DIR = ROOT / "corpus"
V3_DIR = ROOT / "v3"
REVIEW_DIR = V3_DIR / "review"
OLD_DATASET_PATH = ROOT / "questions_v3.json"
OLD_MANIFEST_PATH = ROOT / "corpus_v3_manifest.json"
SPLIT_PATH = SOURCE_DIR / "split_merged.json"
SELECTION_OVERRIDES_PATH = V3_DIR / "selection_overrides.json"

SEED = 20260819
ALGORITHM_VERSION = "crud-rag-1000-unanswerable-v3-20260823"
UPSTREAM_REPO = "https://github.com/IAAR-Shanghai/CRUD_RAG"
UPSTREAM_COMMIT = "1aace383994e1f68efa12cf2a8e2dadfb4102ceb"
DATASET_ID = "crud-rag-1000-120q-v2"
CORPUS_ID = "crud-rag-1000-v1"
TARGETS = {
    "questanswer_1doc": 7,
    "questanswer_2docs": 7,
    "questanswer_3docs": 6,
}
NEW_SPLITS = {"development": 12, "validation": 4, "frozen_test": 4}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def normalize_text(text: str) -> str:
    """Use the same conservative normalisation as the existing fixture."""
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        re.sub(r"[^\S\r\n]+", " ", line).strip()
        for line in normalized.split("\n")
    ]
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        if line:
            compacted.append(line)
            previous_blank = False
        elif compacted and not previous_blank:
            compacted.append("")
            previous_blank = True
    return "\n".join(compacted).strip()


def compact_text(text: str) -> str:
    return "".join(normalize_text(text).split()).casefold()


def question_key(text: str) -> str:
    return compact_text(text)


def stable_key(category: str, upstream_id: str, question: str) -> str:
    value = f"{SEED}|unanswerable-v3|{category}|{upstream_id}|{question_key(question)}"
    return sha256_text(value)


def category_document_count(category: str) -> int:
    match = re.search(r"_(\d+)doc(?:s)?$", category)
    if not match:
        raise ValueError(f"无法从 QA 类别解析文档数量: {category}")
    return int(match.group(1))


def simhash(text: str) -> int:
    compact = compact_text(text)
    if not compact:
        return 0
    grams = {
        compact[index : index + 5]
        for index in range(max(0, len(compact) - 5 + 1))
    }
    bits = [0] * 64
    for gram in grams:
        value = int.from_bytes(hashlib.sha256(gram.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            bits[bit] += 1 if value & (1 << bit) else -1
    result = 0
    for bit, score in enumerate(bits):
        if score >= 0:
            result |= 1 << bit
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not OLD_DATASET_PATH.is_file():
        raise FileNotFoundError(OLD_DATASET_PATH)
    if not OLD_MANIFEST_PATH.is_file():
        raise FileNotFoundError(OLD_MANIFEST_PATH)
    if not SPLIT_PATH.is_file():
        raise FileNotFoundError(SPLIT_PATH)
    old_dataset = read_json(OLD_DATASET_PATH)
    old_manifest = read_json(OLD_MANIFEST_PATH)
    upstream = read_json(SPLIT_PATH)
    if len(old_dataset.get("questions", [])) != 100:
        raise ValueError("现有 questions_v3.json 不是恰好 100 道题，拒绝构建")
    if len(old_manifest.get("documents", [])) != 1000:
        raise ValueError("现有 corpus_v3_manifest.json 不是恰好 1000 篇，拒绝构建")
    rows: list[dict[str, Any]] = []
    for category in TARGETS:
        values = upstream.get(category)
        if not isinstance(values, list):
            raise ValueError(f"上游缺少固定 QA 类别: {category}")
        rows.extend(
            {"category": category, "record": row}
            for row in values
            if isinstance(row, dict)
        )
    return old_dataset, old_manifest, rows


def load_selection_overrides() -> dict[str, Any]:
    if not SELECTION_OVERRIDES_PATH.is_file():
        return {
            "schema_version": 1,
            "decision_version": None,
            "reviewer_type": None,
            "human_review_complete": False,
            "hard_rejects": {},
            "partial_support": {},
            "policy": {},
        }
    value = read_json(SELECTION_OVERRIDES_PATH)
    if value.get("schema_version") != 1:
        raise ValueError("selection_overrides.json schema_version 必须为1")
    if value.get("human_review_complete") is not False:
        raise ValueError("selection_overrides.json 不得声明人工审核已完成")
    if not isinstance(value.get("hard_rejects"), dict) or not isinstance(
        value.get("partial_support"), dict
    ):
        raise ValueError("selection_overrides.json 缺少 hard_rejects/partial_support 对象")
    overlap = set(value["hard_rejects"]) & set(value["partial_support"])
    if overlap:
        raise ValueError("同一 upstream ID 不能同时 hard reject 和 partial support")
    return value


def load_corpus() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in CORPUS_DIR.rglob("*.txt") if item.is_file()),
        key=lambda item: safe_relative(item, CORPUS_DIR),
    ):
        raw = path.read_text(encoding="utf-8")
        normalized = normalize_text(raw)
        documents.append(
            {
                "document_name": safe_relative(path, CORPUS_DIR),
                "file_sha256": sha256_bytes(path.read_bytes()),
                "normalized_sha256": sha256_text(normalized),
                "normalized_text": normalized,
                "compact_text": compact_text(normalized),
                "simhash": simhash(normalized),
            }
        )
    if len(documents) != 1000:
        raise ValueError(f"当前 corpus 发现 {len(documents)} 篇，不是 1000 篇")
    return documents


def extract_key_facts(answer: str) -> list[str]:
    compact = compact_text(answer)
    facts: list[str] = []
    # Keep dates, percentages, amounts and multi-digit values.  A single
    # digit is too common to be a useful rejection signal.
    patterns = (
        r"\d{4}年(?:\d{1,2}月)?(?:\d{1,2}日)?",
        r"\d{1,2}月(?:\d{1,2}日)?",
        r"\d+(?:\.\d+)?%",
        r"\d{2,}(?:\.\d+)?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            if match.group(0) not in facts:
                facts.append(match.group(0))
    return facts


def extract_question_anchors(question: str) -> list[str]:
    compact = compact_text(question)
    anchors = [value for value in re.findall(r"[\u4e00-\u9fff]{4,}", compact)]
    return list(dict.fromkeys(anchors))


def sentence_chunks(text: str) -> list[str]:
    chunks = [compact_text(value) for value in re.split(r"[。！？!?；;]", text)]
    return [value for value in chunks if len(value) >= 40]


def find_docs_containing(corpus: list[dict[str, Any]], needle: str) -> list[str]:
    if not needle:
        return []
    return [
        doc["document_name"]
        for doc in corpus
        if needle in doc["compact_text"]
    ]


def scan_candidate(
    *,
    category: str,
    row: dict[str, Any],
    document_count: int,
    corpus: list[dict[str, Any]],
) -> dict[str, Any]:
    gold_texts = [normalize_text(row[f"news{index}"]) for index in range(1, document_count + 1)]
    gold_hashes = [sha256_text(value) for value in gold_texts]
    corpus_by_hash: dict[str, list[str]] = {}
    for doc in corpus:
        corpus_by_hash.setdefault(doc["normalized_sha256"], []).append(doc["document_name"])
    exact_hash_matches = {
        digest: corpus_by_hash.get(digest, [])
        for digest in gold_hashes
        if corpus_by_hash.get(digest)
    }

    near_matches: list[dict[str, Any]] = []
    for index, gold_text in enumerate(gold_texts, start=1):
        gold_simhash = simhash(gold_text)
        distances = [
            (hamming_distance(gold_simhash, doc["simhash"]), doc["document_name"])
            for doc in corpus
        ]
        close = [
            {"document_name": name, "hamming_distance": distance}
            for distance, name in sorted(distances)[:20]
            if distance <= 5
        ]
        if close:
            near_matches.append({"news_index": index, "matches": close})

    long_matches: list[dict[str, Any]] = []
    for index, gold_text in enumerate(gold_texts, start=1):
        gold_compact = compact_text(gold_text)
        needles: list[tuple[str, str]] = []
        for sentence in sentence_chunks(gold_text):
            if len(sentence) >= 120:
                needles.append(("sentence", sentence))
        # Sample deterministic windows from the gold text.  The exact window
        # scan catches copied/truncated articles even when sentence boundaries
        # differ in the corpus.
        if len(gold_compact) >= 120:
            for start in range(0, min(len(gold_compact) - 119, 1200), 80):
                needles.append(("window", gold_compact[start : start + 120]))
        seen_needles: set[str] = set()
        matches: list[dict[str, Any]] = []
        for kind, needle in needles:
            if needle in seen_needles:
                continue
            seen_needles.add(needle)
            docs = find_docs_containing(corpus, needle)
            if docs:
                matches.append({"kind": kind, "length": len(needle), "documents": docs[:20]})
        if matches:
            long_matches.append({"news_index": index, "matches": matches[:20]})

    answer_compact = compact_text(row["answers"])
    answer_exact_matches = find_docs_containing(corpus, answer_compact)
    key_facts = extract_key_facts(row["answers"])
    fact_matches = {
        fact: find_docs_containing(corpus, fact)
        for fact in key_facts
    }
    anchors = extract_question_anchors(row["questions"])
    answer_fact_docs: dict[str, list[str]] = {}
    for doc in corpus:
        matched_facts = [fact for fact in key_facts if fact in doc["compact_text"]]
        matched_anchors = [anchor for anchor in anchors if anchor in doc["compact_text"]]
        if matched_facts or matched_anchors:
            answer_fact_docs[doc["document_name"]] = {
                "facts": matched_facts,
                "question_anchors": matched_anchors,
            }
    complete_fact_support = [
        name
        for name, values in answer_fact_docs.items()
        if len(key_facts) >= 2
        and len(values["facts"]) == len(key_facts)
        and len(values["question_anchors"]) >= 1
    ]

    rejection_reasons: list[str] = []
    if exact_hash_matches:
        rejection_reasons.append("gold_exact_hash_in_corpus")
    if near_matches:
        rejection_reasons.append("gold_simhash_hamming_le_5")
    if answer_exact_matches:
        rejection_reasons.append("answer_exactly_in_corpus")
    if any(
        match.get("length", 0) >= 120
        for entry in long_matches
        for match in entry["matches"]
    ):
        rejection_reasons.append("gold_long_sentence_or_window_in_corpus")
    if complete_fact_support:
        rejection_reasons.append("answer_key_facts_and_question_anchor_same_document")

    return {
        "category": category,
        "upstream_id": row["ID"],
        "upstream_question": row["questions"].strip(),
        "upstream_answer": row["answers"].strip(),
        "gold_documents": [
            {
                "news_index": index,
                "sha256": sha256_text(text),
                "text": text,
                "text_length": len(text),
                "simhash": f"{simhash(text):016x}",
            }
            for index, text in enumerate(gold_texts, start=1)
        ],
        "exact_hash_matches": exact_hash_matches,
        "simhash_near_matches": near_matches,
        "long_sentence_window_matches": long_matches,
        "answer_exact_matches": answer_exact_matches,
        "key_facts": key_facts,
        "key_fact_matches": fact_matches,
        "complete_fact_support": complete_fact_support,
        "auto_screen_pass": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "retrieval": {
            "status": "pending",
            "dense_top50": [],
            "bm25_top50": [],
            "hybrid_top50": [],
            "note": "未运行向量检索；后续 retrieval audit 阶段填充。",
        },
    }


def candidate_pool(
    rows: list[dict[str, Any]],
    old_dataset: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    old_ids = {
        item.get("source", {}).get("upstream_id")
        for item in old_dataset["questions"]
    }
    old_questions = {question_key(item.get("question", "")) for item in old_dataset["questions"]}
    eligible: list[dict[str, Any]] = []
    filtered: Counter[str] = Counter()
    for item in rows:
        category = item["category"]
        row = item["record"]
        if not isinstance(row.get("ID"), str) or not row["ID"].strip():
            filtered["missing_id"] += 1
            continue
        if row["ID"] in old_ids:
            filtered["existing_upstream_id"] += 1
            continue
        if not isinstance(row.get("questions"), str) or not row["questions"].strip():
            filtered["missing_question"] += 1
            continue
        if question_key(row["questions"]) in old_questions:
            filtered["existing_question"] += 1
            continue
        if not isinstance(row.get("answers"), str) or not row["answers"].strip():
            filtered["missing_answer"] += 1
            continue
        document_count = category_document_count(category)
        gold_values = [row.get(f"news{index}") for index in range(1, document_count + 1)]
        if any(not isinstance(value, str) or not value.strip() for value in gold_values):
            filtered["missing_gold_text"] += 1
            continue
        normalized_hashes = [sha256_text(normalize_text(value)) for value in gold_values]
        if len(normalized_hashes) != len(set(normalized_hashes)):
            filtered["duplicate_gold_within_question"] += 1
            continue
        eligible.append(
            {
                "category": category,
                "record": row,
                "stable_key": stable_key(category, row["ID"], row["questions"]),
            }
        )
    eligible.sort(key=lambda item: (item["stable_key"], item["category"], item["record"]["ID"]))
    return eligible, dict(sorted(filtered.items()))


def build_question(
    candidate: dict[str, Any], split: str, unanswerable_type: str
) -> dict[str, Any]:
    category = candidate["category"]
    row = candidate["record"]
    count = category_document_count(category)
    item_id = f"crud-unanswerable-{category.replace('questanswer_', '')}-{row['ID']}"
    return {
        "id": item_id,
        "question_group": item_id,
        "split": split,
        "question": row["questions"].strip(),
        "category": "unanswerable",
        "difficulty": {1: "easy", 2: "medium", 3: "hard"}[count],
        "document_types": ["txt"],
        "answerable": False,
        "unanswerable_type": unanswerable_type,
        "evidence": [],
        "keypoints": [],
        "reference_answer": "根据给定语料无法回答该问题。",
        "review": {
            "status": "draft",
            "reviewer": None,
            "reviewed_at": None,
            "rubric_version": "rag-gold-v1",
            "evidence_checked": False,
            "answer_checked": False,
            "whole_corpus_checked": False,
        },
        "source": {
            "upstream_task": category,
            "upstream_id": row["ID"],
            "upstream_event": row.get("event"),
            "gold_document_count": count,
        },
    }


def review_markdown(item: dict[str, Any], scan: dict[str, Any]) -> str:
    lines = [
        f"# 不可回答题审核包：{item['id']}",
        "",
        "> 当前为自动构建的 draft。不得将本文件视为人工审核结论。",
        "",
        f"- Dataset：`{DATASET_ID}`",
        f"- Corpus：`{CORPUS_ID}`（固定 1000 篇，未加入 excluded gold）",
        f"- Split：`{item['split']}`",
        f"- 上游类别：`{scan['category']}`",
        f"- 上游 ID：`{scan['upstream_id']}`",
        f"- 来源稳定键：`{scan['stable_key']}`",
        f"- 不可回答类型：`{item['unanswerable_type']}`",
        f"- 语义复核说明：{scan.get('semantic_review_reason') or 'Top50 中未发现明显局部答案支持'}",
        "",
        "## 问题与上游答案（仅供审核）",
        "",
        f"**问题：** {scan['upstream_question']}",
        "",
        f"**上游答案：** {scan['upstream_answer']}",
        "",
        "## Excluded gold 文档",
        "",
    ]
    for gold in scan["gold_documents"]:
        lines.extend(
            [
                f"### news{gold['news_index']}",
                "",
                f"- SHA-256（规范化正文）：`{gold['sha256']}`",
                f"- 长度：`{gold['text_length']}`",
                "- corpus 精确 hash 命中：`否`",
                "",
                "```text",
                gold["text"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 自动 whole-corpus 扫描",
            "",
            f"- 自动筛选：`{'pass' if scan['auto_screen_pass'] else 'reject'}`",
            "- 精确正文 hash：" + json.dumps(scan["exact_hash_matches"], ensure_ascii=False),
            "- SimHash 汉明距离 ≤ 5：" + json.dumps(scan["simhash_near_matches"], ensure_ascii=False),
            "- 长句/窗口命中：" + json.dumps(scan["long_sentence_window_matches"], ensure_ascii=False),
            "- 原答案精确扫描：" + json.dumps(scan["answer_exact_matches"], ensure_ascii=False),
            "- 数字/日期关键事实：" + json.dumps(scan["key_fact_matches"], ensure_ascii=False),
            "",
            "## Retrieval audit（待后续阶段）",
            "",
            "- Dense Top50：`pending`",
            "- BM25 Top50：`pending`",
            "- Hybrid Top50：`pending`",
            "- 本构建脚本没有加载 embedding/reranker，没有访问向量数据库。",
            "",
            "## 人工审核状态",
            "",
            "- status：`draft`",
            "- evidence_checked：`false`",
            "- answer_checked：`false`",
            "- whole_corpus_checked：`false`",
            "- Reviewer：",
            "- Reviewed at：",
            "- 结论：`approve / revise / reject`",
            "- 备注：",
            "",
        ]
    )
    return "\n".join(lines)


def build_readme(selection: dict[str, Any]) -> str:
    type_counts = Counter(
        item["unanswerable_type"]
        for item in selection["selection"]["questions"]
    )
    return f"""# CRUD_RAG Dataset v3：120 题 draft 不可回答扩展

本目录是在既有 `../questions_v3.json`、`../corpus/` 和
`../corpus_v3_manifest.json` 基础上的独立版本化产物。原有 100 题、1000
篇文档和 corpus manifest 不被覆盖；新数据集 ID 为 `{DATASET_ID}`，复用
不变的 corpus ID `{CORPUS_ID}`。

## 当前规模

- 题目：120（answerable 100 + unanswerable draft 20）
- 新增来源复杂度：1doc=7、2docs=7、3docs=6
- 新增 split：development=12、validation=4、frozen_test=4
- 不可回答类型：out_of_scope={type_counts.get('out_of_scope', 0)}、insufficient_detail={type_counts.get('insufficient_detail', 0)}
- corpus：仍为 1000 篇，未复制或修改任何文档
- 审核状态：20 题全部 `draft`，三个 checked 字段全部 `false`

## 确定性构建

从固定 commit `{UPSTREAM_COMMIT}` 的 `source/split_merged.json` 读取
`questanswer_1doc`、`questanswer_2docs`、`questanswer_3docs`。候选先排除
现有 upstream ID/问题、缺字段和题内重复 gold，再按
`SHA-256(20260819|unanswerable-v3|category|ID|normalized_question)`排序。
每个候选的全部 `news*` 正文必须不在当前 1000 篇 corpus 中；随后执行
规范化正文 hash、5-gram SimHash、长句/120 字符窗口、原答案和数字/日期
关键事实扫描。明确检测到完整答案泄漏的候选会被拒绝。

静态构建产物 `whole_corpus_audit.json` 中的检索槽位按设计保持 `pending`，
以证明静态构建本身没有访问数据库。独立检索阶段另写
`retrieval_top50.json`、`whole_corpus_retrieval_audit.json` 和
`retrieval_summary.md`；应以这些文件判断最近一次检索审计状态。本构建不联网、
不调用 LLM、不加载 embedding/reranker，也不创建数据库。

## 文件

- `questions_v3_120q.json`：运行时数据集；不可回答题不含 evidence/keypoints
  和上游答案。
- `unanswerable_selection_manifest.json`：20 题上游映射、完整 excluded gold
  正文和筛选结果。
- `whole_corpus_audit.json`：逐题自动审计和 pending retrieval 槽位。
- `selection_overrides.json`：语义复核的 hard reject 与 partial support 决策。
- `rejected_review/`：被淘汰候选的跨文档完整答案证据。
- `review/<question-id>.md`：逐题人工审核包。
- `retrieval_top50.json`：独立隔离索引的三路无阈值 Top50 完整结果。
- `whole_corpus_retrieval_audit.json`：逐题检索风险摘要。
- `retrieval_review/`：20 份三路 Top10 语义审核附录。
- `retrieval_summary.md`：检索设备、隔离、耗时和状态摘要。
- `provenance.json`：固定来源、旧产物 hash、新产物 hash 和算法版本。

## 验证

在仓库根目录执行：

```powershell
.\\.venv\\Scripts\\python.exe -m pytest -p no:cacheprovider `
  --basetemp eval\\crud_rag_1000\\.pytest-tmp-unanswerable-v3 `
  eval\\crud_rag_1000\\test_unanswerable_v3.py
```

测试明确验证 120/1000、7/7/6、12/4/4、旧 100 题和 corpus manifest 未变、
excluded gold 缺失、非严格校验通过以及严格校验因 draft 审核状态失败。
"""


def build() -> dict[str, Any]:
    old_dataset, old_manifest, rows = load_inputs()
    overrides = load_selection_overrides()
    hard_rejects = overrides["hard_rejects"]
    partial_support = overrides["partial_support"]
    corpus = load_corpus()
    eligible, filter_counts = candidate_pool(rows, old_dataset)
    by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in TARGETS}
    candidate_scans: list[dict[str, Any]] = []
    selected_scans: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for item in eligible:
        category = item["category"]
        upstream_id = item["record"]["ID"]
        if upstream_id in hard_rejects:
            rejection_counts["manual_semantic_complete_answer"] += 1
            continue
        document_count = category_document_count(category)
        scan = scan_candidate(
            category=category,
            row=item["record"],
            document_count=document_count,
            corpus=corpus,
        )
        scan["stable_key"] = item["stable_key"]
        scan["unanswerable_type"] = (
            "insufficient_detail" if upstream_id in partial_support else "out_of_scope"
        )
        scan["semantic_review_reason"] = (
            partial_support.get(upstream_id, {}).get("reason")
        )
        candidate_scans.append(scan)
        if not scan["auto_screen_pass"]:
            for reason in scan["rejection_reasons"]:
                rejection_counts[reason] += 1
            continue
        if len(by_category[category]) < TARGETS[category]:
            by_category[category].append(scan)

    if any(len(by_category[category]) != target for category, target in TARGETS.items()):
        raise RuntimeError(
            "自动筛选后无法达到 7/7/6："
            + json.dumps({key: len(value) for key, value in by_category.items()}, ensure_ascii=False)
        )
    selected_scans = [
        item
        for category in TARGETS
        for item in by_category[category]
    ]
    selected_scans.sort(key=lambda item: (item["stable_key"], item["category"], item["upstream_id"]))

    split_values: list[str] = []
    for split, count in NEW_SPLITS.items():
        split_values.extend([split] * count)
    new_questions: list[dict[str, Any]] = []
    for scan, split in zip(selected_scans, split_values, strict=True):
        candidate = {"category": scan["category"], "record": {"ID": scan["upstream_id"], "questions": scan["upstream_question"], "event": None}}
        # Recover the original event without exposing gold/answer in runtime data.
        source_row = next(
            item["record"]
            for item in rows
            if item["category"] == scan["category"] and item["record"].get("ID") == scan["upstream_id"]
        )
        candidate["record"]["event"] = source_row.get("event")
        new_questions.append(
            build_question(candidate, split, scan["unanswerable_type"])
        )

    combined = copy.deepcopy(old_dataset)
    combined["dataset_id"] = DATASET_ID
    combined["corpus_id"] = CORPUS_ID
    combined["questions"] = [*copy.deepcopy(old_dataset["questions"]), *new_questions]
    combined["dialogues"] = copy.deepcopy(old_dataset.get("dialogues", []))

    selection_questions: list[dict[str, Any]] = []
    audit_questions: list[dict[str, Any]] = []
    review_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for scan, item in zip(selected_scans, new_questions, strict=True):
        selection_questions.append(
            {
                "id": item["id"],
                "category": scan["category"],
                "difficulty": item["difficulty"],
                "split": item["split"],
                "unanswerable_type": item["unanswerable_type"],
                "upstream_id": scan["upstream_id"],
                "upstream_question": scan["upstream_question"],
                "upstream_answer": scan["upstream_answer"],
                "excluded_gold_documents": scan["gold_documents"],
                "stable_key": scan["stable_key"],
                "auto_screen_pass": scan["auto_screen_pass"],
                "rejection_reasons": scan["rejection_reasons"],
                "semantic_review_reason": scan["semantic_review_reason"],
            }
        )
        audit_questions.append(
            {
                "id": item["id"],
                "category": scan["category"],
                "upstream_id": scan["upstream_id"],
                "split": item["split"],
                "unanswerable_type": item["unanswerable_type"],
                "excluded_gold_hashes": [value["sha256"] for value in scan["gold_documents"]],
                "excluded_gold_in_corpus": False,
                "exact_hash_matches": scan["exact_hash_matches"],
                "simhash_near_matches": scan["simhash_near_matches"],
                "long_sentence_window_matches": scan["long_sentence_window_matches"],
                "answer_exact_matches": scan["answer_exact_matches"],
                "key_facts": scan["key_facts"],
                "key_fact_matches": scan["key_fact_matches"],
                "complete_fact_support": scan["complete_fact_support"],
                "auto_screen_pass": scan["auto_screen_pass"],
                "rejection_reasons": scan["rejection_reasons"],
                "whole_corpus_checked": False,
                "retrieval": scan["retrieval"],
            }
        )
        review_by_id[item["id"]] = (item, {**scan, "stable_key": scan["stable_key"]})

    old_question_hashes = [sha256_text(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) for item in old_dataset["questions"]]
    selection = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "seed": SEED,
        "dataset_id": DATASET_ID,
        "corpus_id": CORPUS_ID,
        "upstream": {
            "repo": UPSTREAM_REPO,
            "commit": UPSTREAM_COMMIT,
            "source_file": "source/split_merged.json",
            "source_sha256": sha256_bytes(SPLIT_PATH.read_bytes()),
        },
        "semantic_review_overrides": {
            "path": "selection_overrides.json",
            "sha256": (
                sha256_bytes(SELECTION_OVERRIDES_PATH.read_bytes())
                if SELECTION_OVERRIDES_PATH.is_file()
                else None
            ),
            "decision_version": overrides.get("decision_version"),
            "reviewer_type": overrides.get("reviewer_type"),
            "human_review_complete": False,
            "hard_rejects": hard_rejects,
            "partial_support": partial_support,
        },
        "candidate_pool": {
            "eligible_count": len(eligible),
            "filter_counts": filter_counts,
            "auto_screened_count": len(candidate_scans),
            "auto_screen_rejection_counts": dict(sorted(rejection_counts.items())),
            "candidate_scans_are_not_retrieval_results": True,
        },
        "selection": {
            "target_category_distribution": TARGETS,
            "actual_category_distribution": dict(
                sorted(Counter(item["category"] for item in selected_scans).items())
            ),
            "target_new_split_distribution": NEW_SPLITS,
            "actual_new_split_distribution": dict(
                sorted(Counter(item["split"] for item in new_questions).items())
            ),
            "questions": selection_questions,
        },
        "corpus_exclusion": {
            "corpus_manifest_path": "../corpus_v3_manifest.json",
            "corpus_document_count": len(corpus),
            "all_selected_excluded_gold_hashes_missing": True,
            "normalized_hash_method": "SHA-256(NFKC + structured whitespace normalization)",
            "simhash_method": "SHA-256 character 5-gram SimHash; hamming <= 5 rejected",
        },
        "review": {
            "status": "draft",
            "reviewer": None,
            "reviewed_at": None,
            "evidence_checked": False,
            "answer_checked": False,
            "whole_corpus_checked": False,
        },
        "invariants": {
            "old_question_count": len(old_dataset["questions"]),
            "old_question_canonical_hashes": old_question_hashes,
            "old_manifest_sha256": sha256_bytes(OLD_MANIFEST_PATH.read_bytes()),
        },
    }
    audit = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "dataset_id": DATASET_ID,
        "corpus_id": CORPUS_ID,
        "status": "draft",
        "whole_corpus_checked": False,
        "retrieval": {
            "status": "pending",
            "dense_top_k": 50,
            "bm25_top_k": 50,
            "hybrid_top_k": 50,
            "llm_called": False,
            "database_used": False,
            "note": "由后续隔离向量数据库检索审计填充；本构建只做离线静态扫描。",
        },
        "questions": audit_questions,
    }

    # All files below this point belong to the new versioned directory.
    V3_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    write_json(V3_DIR / "questions_v3_120q.json", combined)
    write_json(V3_DIR / "unanswerable_selection_manifest.json", selection)
    write_json(V3_DIR / "whole_corpus_audit.json", audit)
    expected_review_names = {f"{item_id}.md" for item_id in review_by_id}
    for stale_path in REVIEW_DIR.glob("*.md"):
        if stale_path.name not in expected_review_names:
            stale_path.unlink()
    for item_id, (item, scan) in sorted(review_by_id.items()):
        (REVIEW_DIR / f"{item_id}.md").write_text(
            review_markdown(item, scan), encoding="utf-8", newline="\n"
        )
    (V3_DIR / "README.md").write_text(build_readme(selection), encoding="utf-8", newline="\n")

    output_paths = [
        V3_DIR / "questions_v3_120q.json",
        V3_DIR / "unanswerable_selection_manifest.json",
        V3_DIR / "whole_corpus_audit.json",
        V3_DIR / "README.md",
        *sorted(REVIEW_DIR.glob("*.md")),
    ]
    provenance = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "dataset_id": DATASET_ID,
        "corpus_id": CORPUS_ID,
        "deterministic": True,
        "network_used": False,
        "llm_used": False,
        "database_used": False,
        "inputs": {
            "source_split_merged": {
                "path": "../source/split_merged.json",
                "sha256": sha256_bytes(SPLIT_PATH.read_bytes()),
            },
            "old_dataset": {
                "path": "../questions_v3.json",
                "sha256": sha256_bytes(OLD_DATASET_PATH.read_bytes()),
            },
            "old_corpus_manifest": {
                "path": "../corpus_v3_manifest.json",
                "sha256": sha256_bytes(OLD_MANIFEST_PATH.read_bytes()),
            },
            "old_corpus_document_count": len(corpus),
            "selection_overrides": {
                "path": "selection_overrides.json",
                "sha256": (
                    sha256_bytes(SELECTION_OVERRIDES_PATH.read_bytes())
                    if SELECTION_OVERRIDES_PATH.is_file()
                    else None
                ),
                "human_review_complete": False,
            },
        },
        "outputs": [
            {"path": safe_relative(path, V3_DIR), "sha256": sha256_bytes(path.read_bytes()), "bytes": path.stat().st_size}
            for path in output_paths
        ],
        "review_policy": {
            "status": "draft",
            "evidence_checked": False,
            "answer_checked": False,
            "whole_corpus_checked": False,
            "retrieval_top50": "pending",
        },
    }
    write_json(V3_DIR / "provenance.json", provenance)

    return {
        "dataset": safe_relative(V3_DIR / "questions_v3_120q.json", PROJECT_ROOT),
        "selection": safe_relative(V3_DIR / "unanswerable_selection_manifest.json", PROJECT_ROOT),
        "audit": safe_relative(V3_DIR / "whole_corpus_audit.json", PROJECT_ROOT),
        "selected": selection_questions,
        "category_counts": selection["selection"]["actual_category_distribution"],
        "split_counts": selection["selection"]["actual_new_split_distribution"],
        "output_count": len(output_paths) + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="生成 v3 静态产物")
    args = parser.parse_args()
    if not args.build:
        parser.error("请显式指定 --build；脚本不会默认写文件")
    result = build()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
