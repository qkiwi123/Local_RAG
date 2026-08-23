"""Build the pinned CRUD_RAG 1000-document Dataset v3 fixture.

The script is intentionally self-contained and dependency-free apart from the
project's existing ``eval.dataset_v3`` helpers.  It only writes below the
directory containing this file.  Source files are expected to have already
been downloaded to ``source/`` with the URLs documented in ``README.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SOURCE_DIR = ROOT / "source"
CORPUS_DIR = ROOT / "corpus"
SUPPORT_DIR = CORPUS_DIR / "support"
DISTRACTOR_DIR = CORPUS_DIR / "distractor"

SEED = 20260819
ALGORITHM_VERSION = "crud-rag-1000-v1"
UPSTREAM_REPO = "https://github.com/IAAR-Shanghai/CRUD_RAG"
UPSTREAM_COMMIT = "1aace383994e1f68efa12cf2a8e2dadfb4102ceb"
RAW_BASE = (
    "https://raw.githubusercontent.com/IAAR-Shanghai/CRUD_RAG/"
    f"{UPSTREAM_COMMIT}"
)
TREE_URL = (
    "https://api.github.com/repos/IAAR-Shanghai/CRUD_RAG/git/trees/"
    f"{UPSTREAM_COMMIT}?recursive=1"
)
DATASET_ID = "crud-rag-1000-v1"
CORPUS_ID = "crud-rag-1000-v1"

SPLIT_SOURCE_NAME = "split_merged.json"
DISTRACTOR_SOURCE_SHARDS = ("documents_dup_part_1_part_1",)
CATEGORY_SPECS: tuple[tuple[str, int, int], ...] = (
    ("questanswer_1doc", 1, 34),
    ("questanswer_2docs", 2, 33),
    ("questanswer_3docs", 3, 33),
)

_BODY_MARKER = "，正文："


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def normalize_text(text: str) -> str:
    """Match the project's conservative structured text normalizer."""
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


def question_key(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value.rstrip() + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def prune_generated_text_files(directory: Path, expected_names: set[str]) -> None:
    """Remove only stale, directly generated ``.txt`` files in a safe directory."""
    root = ROOT.resolve()
    resolved = directory.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"拒绝清理新数据目录以外的路径: {resolved}")
    if directory.is_symlink():
        raise ValueError(f"拒绝清理符号链接目录: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_symlink():
            raise ValueError(f"生成目录中不允许符号链接: {path}")
        if not path.is_file() or path.suffix.lower() != ".txt":
            raise ValueError(f"生成目录中存在非预期条目，拒绝清理: {path}")
        if path.name not in expected_names:
            path.unlink()


def _stable_key(category: str, item: dict[str, Any]) -> str:
    raw = f"{SEED}|{category}|{item['ID']}|{question_key(item['questions'])}"
    return _sha256_text(raw)


def load_upstream_questions() -> tuple[dict[str, Any], dict[str, Any]]:
    path = SOURCE_DIR / SPLIT_SOURCE_NAME
    if not path.is_file():
        raise FileNotFoundError(f"缺少上游 split 文件: {path}")
    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("split_merged.json 顶层必须是对象")

    upstream_schema = {
        "top_level_keys": list(data.keys()),
        "top_level_counts": {
            key: len(value) if isinstance(value, list) else None
            for key, value in data.items()
        },
        "qa_fields": {},
    }
    for category, _, _ in CATEGORY_SPECS:
        rows = data.get(category)
        if not isinstance(rows, list):
            raise ValueError(f"上游缺少 QA 类别数组: {category}")
        first = rows[0] if rows else {}
        upstream_schema["qa_fields"][category] = list(first.keys())
    return data, upstream_schema


def select_questions(
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter obvious invalid rows and choose the requested stratified sample."""
    seen_question_keys: set[str] = set()
    eligible_by_category: dict[str, list[dict[str, Any]]] = {}
    filter_counts: dict[str, Counter[str]] = {}

    for category, document_count, _requested in CATEGORY_SPECS:
        rows = data.get(category, [])
        eligible: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        filter_counts[category] = counts
        for item in rows:
            if not isinstance(item, dict):
                counts["not_object"] += 1
                continue
            if not isinstance(item.get("ID"), str) or not item["ID"].strip():
                counts["missing_id"] += 1
                continue
            if not isinstance(item.get("questions"), str) or not item["questions"].strip():
                counts["missing_question"] += 1
                continue
            if not isinstance(item.get("answers"), str) or not item["answers"].strip():
                counts["missing_answer"] += 1
                continue
            news = [item.get(f"news{index}") for index in range(1, document_count + 1)]
            if any(not isinstance(value, str) or not value.strip() for value in news):
                counts["missing_gold_text"] += 1
                continue
            normalized_gold = [normalize_text(value) for value in news]
            gold_hashes = [_sha256_text(value) for value in normalized_gold]
            if len(set(gold_hashes)) != len(gold_hashes):
                counts["duplicate_gold_within_question"] += 1
                continue
            qkey = question_key(item["questions"])
            if qkey in seen_question_keys:
                counts["duplicate_question"] += 1
                continue
            seen_question_keys.add(qkey)
            eligible.append(item)
        eligible.sort(key=lambda row: _stable_key(category, row))
        eligible_by_category[category] = eligible

    requested = {category: requested for category, _, requested in CATEGORY_SPECS}
    actual = {
        category: min(requested[category], len(rows))
        for category, rows in eligible_by_category.items()
    }
    remaining = 100 - sum(actual.values())
    reallocation: list[dict[str, Any]] = []
    while remaining > 0:
        candidates = [
            category
            for category, rows in eligible_by_category.items()
            if actual[category] < len(rows)
        ]
        if not candidates:
            break
        category = sorted(
            candidates,
            key=lambda value: (
                -(len(eligible_by_category[value]) - actual[value]),
                value,
            ),
        )[0]
        actual[category] += 1
        remaining -= 1
        reallocation.append({"category": category, "added": 1})

    if sum(actual.values()) != 100:
        raise ValueError(
            "过滤后有效候选不足 100 题: "
            + json.dumps(
                {category: len(rows) for category, rows in eligible_by_category.items()},
                ensure_ascii=False,
            )
        )

    selected: list[dict[str, Any]] = []
    for category, _document_count, _requested in CATEGORY_SPECS:
        selected.extend(
            {
                "category": category,
                "record": item,
                "stable_rank": index,
            }
            for index, item in enumerate(eligible_by_category[category][: actual[category]])
        )
    return selected, {
        "requested_distribution": requested,
        "actual_distribution": actual,
        "eligible_distribution": {
            category: len(rows) for category, rows in eligible_by_category.items()
        },
        "filter_counts": {
            category: dict(sorted(counts.items()))
            for category, counts in filter_counts.items()
        },
        "reallocation": reallocation,
    }


def iter_shard_documents(text: str) -> Iterable[tuple[int, str]]:
    """Extract one ``date，正文：body`` record per upstream source line.

    The pinned shard uses a bracketed date only on its first line and plain
    dates on later lines, but every record has the same ``，正文：`` marker.
    Splitting by physical line therefore preserves the upstream order without
    making assumptions about the date punctuation.
    """
    for index, line in enumerate(text.splitlines()):
        body = line.split(_BODY_MARKER, 1)[1] if _BODY_MARKER in line else line
        body = normalize_text(body)
        if body:
            yield index, body


def char_ngrams(text: str, size: int = 5) -> set[str]:
    compact = "".join(text.split())
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def simhash(text: str) -> int:
    grams = char_ngrams(text)
    if not grams:
        return 0
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


def extract_distractors(
    gold_hashes: set[str],
    target_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Take deterministic non-gold distractors from fixed source shard order."""
    reject_counts: Counter[str] = Counter()
    candidates_seen = 0
    selected: list[dict[str, Any]] = []
    selected_hashes = set(gold_hashes)
    comparison_hashes = [simhash(value) for value in GOLD_TEXTS.values()]
    selected_simhashes: list[int] = []

    for shard_name in DISTRACTOR_SOURCE_SHARDS:
        path = SOURCE_DIR / shard_name
        if not path.is_file():
            raise FileNotFoundError(f"缺少 distractor source shard: {path}")
        text = path.read_text(encoding="utf-8")
        for source_index, candidate in iter_shard_documents(text):
            candidates_seen += 1
            normalized_hash = _sha256_text(candidate)
            if normalized_hash in selected_hashes:
                reject_counts["exact_duplicate"] += 1
                continue
            if len(candidate) < 80:
                reject_counts["too_short"] += 1
                continue
            candidate_hash = simhash(candidate)
            if any(
                hamming_distance(candidate_hash, other) <= 5
                for other in [*comparison_hashes, *selected_simhashes]
            ):
                reject_counts["near_duplicate_simhash_le_5"] += 1
                continue
            selected_hashes.add(normalized_hash)
            selected_simhashes.append(candidate_hash)
            selected.append(
                {
                    "source_shard": shard_name,
                    "source_index": source_index,
                    "text": candidate,
                    "sha256": normalized_hash,
                }
            )
            if len(selected) >= target_count:
                break
        if len(selected) >= target_count:
            break

    if len(selected) < target_count:
        raise ValueError(
            f"固定 source shard 只能提供 {len(selected)} 个 distractor，需求 {target_count}；"
            "请在 DISTRACTOR_SOURCE_SHARDS 中按固定顺序增加分片并下载。"
        )

    for index, item in enumerate(selected, start=1):
        name = f"distractor/distractor-{index:04d}.txt"
        write_text(DISTRACTOR_DIR / f"distractor-{index:04d}.txt", item["text"])
        item["document_name"] = name
        item.pop("text")
    return selected, {
        "target_count": target_count,
        "candidates_seen": candidates_seen,
        "accepted_count": len(selected),
        "rejected_counts": dict(sorted(reject_counts.items())),
        "near_duplicate_rule": {
            "method": "sha256 character 5-gram SimHash",
            "hamming_distance_lte": 5,
            "compared_against": "gold and previously accepted distractors",
        },
        "source_shards": list(DISTRACTOR_SOURCE_SHARDS),
    }


def _char_grams_for_score(text: str, size: int = 2) -> set[str]:
    compact = "".join(text.split())
    return {
        compact[index : index + size]
        for index in range(max(0, len(compact) - size + 1))
    }


def evidence_snippet(text: str, question: str, answer: str) -> tuple[str, bool]:
    """Select a unique source window; return ``(snippet, used_full_text_fallback)``."""
    if not text:
        raise ValueError("gold 文本不能为空")
    boundaries = [match.end() for match in re.finditer(r"[。！？!?；;]", text)]
    starts = [0, *boundaries]
    sentences: list[tuple[int, int, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        value = text[start:end].strip()
        if value:
            actual_start = text.find(value, start, end)
            sentences.append((actual_start, actual_start + len(value), value))
    if not sentences:
        return text, True

    question_grams = _char_grams_for_score(question)
    answer_grams = _char_grams_for_score(answer)
    candidates: list[tuple[int, int, int, int, str]] = []
    for start_index in range(len(sentences)):
        for end_index in range(start_index, min(len(sentences), start_index + 3)):
            start = sentences[start_index][0]
            end = sentences[end_index][1]
            value = text[start:end]
            if len(value) < 20 or len(value) > 500:
                continue
            value_grams = _char_grams_for_score(value)
            score = 2 * len(value_grams & answer_grams) + len(value_grams & question_grams)
            candidates.append((score, -abs(len(value) - 220), -start, start, value))
    for _score, _length_score, _start_score, _start, candidate in sorted(
        candidates, reverse=True
    ):
        if text.count(candidate) == 1:
            return candidate, False
    return text, True


def build_dataset() -> dict[str, Any]:
    global GOLD_TEXTS
    data, upstream_schema = load_upstream_questions()
    selected, selection_stats = select_questions(data)

    GOLD_TEXTS = {}
    question_gold_mapping: list[dict[str, Any]] = []
    for selected_item in selected:
        category = selected_item["category"]
        row = selected_item["record"]
        document_count = next(
            count for name, count, _requested in CATEGORY_SPECS if name == category
        )
        gold_names: list[str] = []
        for doc_index in range(1, document_count + 1):
            text = normalize_text(row[f"news{doc_index}"])
            digest = _sha256_text(text)
            name = f"support/{digest[:16]}.txt"
            existing = GOLD_TEXTS.get(name)
            if existing is not None and existing != text:
                raise ValueError(f"gold 文件名哈希前缀冲突: {name}")
            GOLD_TEXTS[name] = text
            gold_names.append(name)
        question_gold_mapping.append(
            {
                "category": category,
                "upstream_id": row["ID"],
                "gold_documents": gold_names,
            }
        )
    for name, text in sorted(GOLD_TEXTS.items()):
        write_text(CORPUS_DIR / name, text)

    distractors, distractor_stats = extract_distractors(
        set(_sha256_text(value) for value in GOLD_TEXTS.values()),
        1000 - len(GOLD_TEXTS),
    )
    prune_generated_text_files(
        SUPPORT_DIR,
        {Path(name).name for name in GOLD_TEXTS},
    )
    prune_generated_text_files(
        DISTRACTOR_DIR,
        {Path(item["document_name"]).name for item in distractors},
    )

    questions: list[dict[str, Any]] = []
    fallback_entries: list[str] = []
    for ordinal, selected_item in enumerate(selected, start=1):
        category = selected_item["category"]
        row = selected_item["record"]
        count = next(number for name, number, _requested in CATEGORY_SPECS if name == category)
        question_id = f"crud-{category.replace('questanswer_', 'qa-')}-{row['ID']}"
        question_group = f"crud-{category}-{row['ID']}"
        evidence: list[dict[str, str]] = []
        gold_names = question_gold_mapping[ordinal - 1]["gold_documents"]
        for doc_index, name in enumerate(gold_names, start=1):
            snippet, fallback = evidence_snippet(
                GOLD_TEXTS[name], row["questions"], row["answers"]
            )
            if fallback:
                fallback_entries.append(f"{question_id}:{doc_index}")
            evidence.append({"document_name": name, "snippet": snippet})
        questions.append(
            {
                "id": question_id,
                "question_group": question_group,
                "split": "development",
                "question": row["questions"].strip(),
                "category": category,
                "difficulty": {1: "easy", 2: "medium", 3: "hard"}[count],
                "document_types": ["txt"],
                "answerable": True,
                "unanswerable_type": None,
                "evidence": evidence,
                "keypoints": [[row["answers"].strip()]],
                "reference_answer": row["answers"].strip(),
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
        )

    # Import only the existing project helpers; no package installation is needed.
    sys.path.insert(0, str(PROJECT_ROOT))
    from eval.dataset_v3 import (  # pylint: disable=import-outside-toplevel
        build_corpus_manifest,
        parse_corpus,
        prepare_dataset,
        validate_dataset,
    )

    candidate = {
        "schema_version": 3,
        "dataset_id": DATASET_ID,
        "corpus_id": CORPUS_ID,
        "parser_version": "structured-v2",
        "rubric_version": "rag-gold-v1",
        "questions": questions,
        "dialogues": [],
    }
    parsed_documents = parse_corpus(CORPUS_DIR)
    prepared, prepare_report = prepare_dataset(
        [candidate],
        parsed_documents,
        dataset_id=DATASET_ID,
        corpus_id=CORPUS_ID,
    )
    manifest = build_corpus_manifest(
        CORPUS_DIR,
        parsed_documents,
        corpus_id=CORPUS_ID,
    )
    validation = validate_dataset(
        prepared,
        parsed_documents=parsed_documents,
        manifest=manifest,
        current_manifest=manifest,
        strict=False,
    )
    if not validation.ok:
        raise ValueError("生成的数据集无法通过 non-strict v3 校验: " + "\n".join(validation.errors))

    write_json(ROOT / "questions_v3.json", prepared)
    write_json(ROOT / "corpus_v3_manifest.json", manifest)

    source_file_names = [SPLIT_SOURCE_NAME, "tree.json", "UPSTREAM_README.md", *DISTRACTOR_SOURCE_SHARDS]
    source_files: list[dict[str, Any]] = []
    for name in source_file_names:
        path = SOURCE_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"source 文件不存在: {path}")
        url_path = (
            "data/crud_split/split_merged.json"
            if name == SPLIT_SOURCE_NAME
            else "README.md"
            if name == "UPSTREAM_README.md"
            else "data/80000_docs/" + name
            if name in DISTRACTOR_SOURCE_SHARDS
            else None
        )
        source_files.append(
            {
                "path": f"source/{name}",
                "raw_url": (
                    TREE_URL
                    if name == "tree.json"
                    else f"{RAW_BASE}/{url_path}"
                    if url_path
                    else None
                ),
                "sha256": _sha256_bytes(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
        )

    gold_document_names = sorted(GOLD_TEXTS)
    distractor_names = [item["document_name"] for item in distractors]
    selected_questions = []
    for item in prepared["questions"]:
        selected_questions.append(
            {
                "id": item["id"],
                "question_group": item["question_group"],
                "category": item["category"],
                "split": item["split"],
                "upstream_task": item["source"]["upstream_task"],
                "upstream_id": item["source"]["upstream_id"],
                "gold_documents": [value["document_name"] for value in item["evidence"]],
            }
        )
    selection_manifest = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "seed": SEED,
        "upstream": {
            "repo": UPSTREAM_REPO,
            "commit": UPSTREAM_COMMIT,
            "raw_base_url": RAW_BASE,
            "source_files": source_files,
            "schema": upstream_schema,
        },
        "upstream_counts": {
            key: len(value) if isinstance(value, list) else None
            for key, value in data.items()
        },
        "selection": {
            **selection_stats,
            "selected_count": len(prepared["questions"]),
            "actual_split_counts": dict(
                sorted(Counter(item["split"] for item in prepared["questions"]).items())
            ),
            "questions": selected_questions,
        },
        "corpus": {
            "gold_document_count": len(gold_document_names),
            "distractor_document_count": len(distractor_names),
            "total_document_count": len(gold_document_names) + len(distractor_names),
            "gold_documents": gold_document_names,
            "distractor_documents": distractor_names,
            "question_gold_mapping": question_gold_mapping,
        },
        "distractor": {
            **distractor_stats,
            "documents": distractors,
        },
        "evidence": {
            "method": "sentence or up-to-three-sentence character n-gram overlap window",
            "scoring": "2x answer bigram overlap + 1x question bigram overlap",
            "fallback": "full normalized gold text when no unique 20-500 character window exists",
            "fallback_count": len(fallback_entries),
            "fallback_entries": fallback_entries,
        },
        "review": {
            "status": "draft",
            "reviewer": None,
            "reviewed_at": None,
            "evidence_checked": False,
            "answer_checked": False,
            "whole_corpus_checked": False,
        },
        "determinism": {
            "selection_order": "SHA-256(seed|category|upstream ID|normalized question)",
            "distractor_order": "source shard tuple order, then source record order",
            "normalized_gold_hash": "SHA-256(NFKC + structured whitespace normalization)",
            "time_free": True,
        },
    }
    write_json(ROOT / "selection_manifest.json", selection_manifest)

    artifact_names = [
        "questions_v3.json",
        "corpus_v3_manifest.json",
        "selection_manifest.json",
    ]
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "upstream": {
            "repo": UPSTREAM_REPO,
            "commit": UPSTREAM_COMMIT,
            "source_files": source_files,
        },
        "local_artifacts": [
            {
                "path": name,
                "sha256": _sha256_bytes((ROOT / name).read_bytes()),
                "bytes": (ROOT / name).stat().st_size,
            }
            for name in artifact_names
        ],
        "build": {
            "dataset_id": DATASET_ID,
            "corpus_id": CORPUS_ID,
            "prepare_report": prepare_report,
            "validation": validation.model_dump(),
        },
    }
    write_json(ROOT / "provenance.json", provenance)

    return {
        "questions": len(prepared["questions"]),
        "question_distribution": dict(
            sorted(Counter(item["category"] for item in prepared["questions"]).items())
        ),
        "gold_documents": len(gold_document_names),
        "distractor_documents": len(distractor_names),
        "corpus_documents": len(gold_document_names) + len(distractor_names),
        "fallback_count": len(fallback_entries),
        "validation": validation.model_dump(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build",
        action="store_true",
        help="从 source/ 构建 questions_v3.json 和 corpus manifest（默认动作）",
    )
    args = parser.parse_args()
    del args
    summary = build_dataset()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
