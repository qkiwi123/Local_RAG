"""Dataset v3 的确定性 prepare、manifest 生成和校验工具。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.models.document_structure import ParsedDocument, ParsedUnit
from backend.services.parsers import ADAPTERS, PARSER_VERSION, parse_document_structure


SPLITS = ("development", "validation", "frozen_test")
UNANSWERABLE_TYPES = ("out_of_scope", "insufficient_detail")
RUBRIC_VERSION = "rag-gold-v1"


class DatasetPreparationError(ValueError):
    """候选数据不能无歧义地补齐来源字段。"""


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def model_dump(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def load_json_objects(path: str | Path) -> list[dict[str, Any]]:
    """读取一个 JSON 对象，或由逗号分隔的多个顶层候选对象。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        if not isinstance(value, dict):
            raise DatasetPreparationError("Dataset v3 顶层候选必须是 JSON 对象")
        objects.append(value)
    if not objects:
        raise DatasetPreparationError("数据集文件中没有 JSON 对象")
    return objects


def _stable_split_assignments(group_ids: Iterable[str]) -> dict[str, str]:
    ordered = sorted(
        set(group_ids),
        key=lambda value: (hashlib.sha256(value.encode("utf-8")).hexdigest(), value),
    )
    count = len(ordered)
    development_count = round(count * 0.6)
    validation_count = round(count * 0.2)
    assignments: dict[str, str] = {}
    for index, group_id in enumerate(ordered):
        if index < development_count:
            split = "development"
        elif index < development_count + validation_count:
            split = "validation"
        else:
            split = "frozen_test"
        assignments[group_id] = split
    return assignments


def merge_candidate_datasets(
    candidates: list[dict[str, Any]],
    *,
    dataset_id: str,
    corpus_id: str,
) -> dict[str, Any]:
    """把多批模型输出合并成 Dataset v3 规定的唯一顶层对象。"""
    questions: list[dict[str, Any]] = []
    dialogues: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("schema_version") != 3:
            raise DatasetPreparationError("所有候选批次的 schema_version 必须为 3")
        questions.extend(copy.deepcopy(candidate.get("questions", [])))
        dialogues.extend(copy.deepcopy(candidate.get("dialogues", [])))

    group_ids = [
        item.get("question_group")
        for item in [*questions, *dialogues]
        if isinstance(item.get("question_group"), str)
    ]
    assignments = _stable_split_assignments(group_ids)
    for item in [*questions, *dialogues]:
        group_id = item.get("question_group")
        if group_id in assignments:
            item["split"] = assignments[group_id]

    # review 属于整个 dialogue；删除个别模型批次误加在 turn 上的副本。
    for dialogue in dialogues:
        for turn in dialogue.get("turns", []):
            turn.pop("review", None)

    return {
        "schema_version": 3,
        "dataset_id": dataset_id,
        "corpus_id": corpus_id,
        "parser_version": PARSER_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "questions": questions,
        "dialogues": dialogues,
    }


def parse_corpus(corpus_dir: str | Path) -> dict[str, ParsedDocument]:
    root = Path(corpus_dir)
    if not root.is_dir():
        raise DatasetPreparationError(f"corpus 目录不存在: {root}")
    parsed: dict[str, ParsedDocument] = {}
    for path in sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and item.suffix.lower() in ADAPTERS
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative_name = path.relative_to(root).as_posix()
        parsed[relative_name] = parse_document_structure(
            str(path),
            document_id=relative_name,
            document_name=relative_name,
        )
    if not parsed:
        raise DatasetPreparationError("corpus 中没有受支持的文档")
    return parsed


def _json_line(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _unit_hash_object(relative_name: str, unit: ParsedUnit) -> dict[str, Any]:
    blocks = []
    for block in sorted(unit.blocks, key=lambda item: item.sequence):
        value = block.model_dump()
        value.pop("block_id", None)
        blocks.append(value)
    return {
        "file": relative_name,
        "parser_version": unit.parser_version,
        "locator": unit.locator.model_dump(),
        "blocks": blocks,
        "canonical_text": unit.canonical_text,
    }


def build_corpus_manifest(
    corpus_dir: str | Path,
    parsed_documents: dict[str, ParsedDocument],
    *,
    corpus_id: str,
) -> dict[str, Any]:
    root = Path(corpus_dir)
    documents: list[dict[str, Any]] = []
    corpus_hash_input = bytearray()
    for relative_name in sorted(parsed_documents):
        parsed = parsed_documents[relative_name]
        units_hash = hashlib.sha256()
        for unit in sorted(parsed.units, key=lambda item: item.locator.unit_index):
            units_hash.update(_json_line(_unit_hash_object(relative_name, unit)))
        parsed_units_sha256 = units_hash.hexdigest()
        corpus_hash_input.extend(relative_name.encode("utf-8"))
        corpus_hash_input.extend(b"\0")
        corpus_hash_input.extend(parsed_units_sha256.encode("ascii"))
        corpus_hash_input.extend(b"\n")
        documents.append(
            {
                "file": relative_name,
                "sha256": hashlib.sha256((root / relative_name).read_bytes()).hexdigest(),
                "document_type": Path(relative_name).suffix.lower().lstrip("."),
                "parsed_units_sha256": parsed_units_sha256,
            }
        )
    return {
        "corpus_id": corpus_id,
        "parser_version": PARSER_VERSION,
        "parsed_corpus_sha256": hashlib.sha256(corpus_hash_input).hexdigest(),
        "documents": documents,
    }


def _normalized_text_with_positions(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        characters.append(character)
        positions.append(index)
    return "".join(characters), positions


def _metric_norm(text: str) -> str:
    """与 eval.metrics.norm 保持一致，验证 gold answer 与 keypoint 自洽。"""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(normalized.split()).lower()


def _all_occurrences(text: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        found = text.find(needle, start)
        if found < 0:
            return positions
        positions.append(found)
        start = found + 1


def _candidate_matches(
    units: list[ParsedUnit],
    snippet: str,
    *,
    unit_index: int | None,
    start_char: int | None,
) -> tuple[list[tuple[ParsedUnit, int, int]], bool]:
    selected = [
        unit
        for unit in units
        if unit_index is None or unit.locator.unit_index == unit_index
    ]
    exact: list[tuple[ParsedUnit, int, int]] = []
    for unit in selected:
        for start in _all_occurrences(unit.canonical_text, snippet):
            if start_char is None or start == start_char:
                exact.append((unit, start, start + len(snippet)))
    if exact:
        return exact, False

    normalized_snippet = "".join(
        character for character in snippet if not character.isspace()
    )
    normalized: list[tuple[ParsedUnit, int, int]] = []
    if not normalized_snippet:
        return normalized, True
    for unit in selected:
        normalized_text, positions = _normalized_text_with_positions(
            unit.canonical_text
        )
        for normalized_start in _all_occurrences(
            normalized_text,
            normalized_snippet,
        ):
            start = positions[normalized_start]
            end = positions[normalized_start + len(normalized_snippet) - 1] + 1
            if start_char is None or start == start_char:
                normalized.append((unit, start, end))
    return normalized, True


def resolve_evidence(
    evidence: dict[str, Any],
    parsed_documents: dict[str, ParsedDocument],
) -> tuple[dict[str, Any], bool]:
    document_name = str(evidence.get("document_name", "")).replace("\\", "/")
    snippet = evidence.get("snippet")
    if document_name not in parsed_documents:
        raise DatasetPreparationError(f"证据文档不存在: {document_name}")
    if not isinstance(snippet, str) or not snippet.strip():
        raise DatasetPreparationError(f"{document_name}: snippet 不能为空")

    locator_hint = evidence.get("locator")
    unit_index = evidence.get("unit_index")
    if isinstance(locator_hint, dict):
        unit_index = locator_hint.get("unit_index", unit_index)
    start_char = evidence.get("start_char")
    if unit_index is not None and not isinstance(unit_index, int):
        raise DatasetPreparationError(f"{document_name}: unit_index 必须为整数")
    if start_char is not None and not isinstance(start_char, int):
        raise DatasetPreparationError(f"{document_name}: start_char 必须为整数")

    matches, canonicalized = _candidate_matches(
        parsed_documents[document_name].units,
        snippet,
        unit_index=unit_index,
        start_char=start_char,
    )
    if len(matches) != 1:
        locations = [
            f"u{unit.locator.unit_index}:{start}"
            for unit, start, _end in matches
        ]
        detail = "、".join(locations) if locations else "无"
        raise DatasetPreparationError(
            f"{document_name}: snippet 需要唯一命中，当前候选 {len(matches)} 个（{detail}）"
        )

    unit, start, end = matches[0]
    canonical_snippet = unit.canonical_text[start:end]
    return (
        {
            "document_name": document_name,
            "snippet": canonical_snippet,
            "parser_version": unit.parser_version,
            "unit_id": f"{document_name}:u{unit.locator.unit_index}",
            "start_char": start,
            "end_char": end,
            "snippet_sha256": hashlib.sha256(
                canonical_snippet.encode("utf-8")
            ).hexdigest(),
            "locator": unit.locator.model_dump(exclude_none=True),
        },
        canonicalized or canonical_snippet != snippet,
    )


def load_overrides(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if value.get("version") != 1 or not isinstance(value.get("evidence"), dict):
        raise DatasetPreparationError("override 文件必须包含 version=1 和 evidence 对象")
    return value["evidence"]


def _iter_answer_items(dataset: dict[str, Any]):
    for question in dataset.get("questions", []):
        yield question["id"], question
    for dialogue in dataset.get("dialogues", []):
        for turn in dialogue.get("turns", []):
            yield turn["turn_id"], turn


def prepare_dataset(
    candidates: list[dict[str, Any]],
    parsed_documents: dict[str, ParsedDocument],
    *,
    dataset_id: str,
    corpus_id: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = merge_candidate_datasets(
        candidates,
        dataset_id=dataset_id,
        corpus_id=corpus_id,
    )
    override_values = overrides or {}
    used_overrides: set[str] = set()
    canonicalized_count = 0
    evidence_count = 0
    errors: list[str] = []
    for item_id, item in _iter_answer_items(dataset):
        resolved_evidence = []
        for index, original in enumerate(item.get("evidence", [])):
            evidence = copy.deepcopy(original)
            override_key = f"{item_id}:{index}"
            if override_key in override_values:
                evidence.update(copy.deepcopy(override_values[override_key]))
                used_overrides.add(override_key)
            try:
                resolved, canonicalized = resolve_evidence(
                    evidence,
                    parsed_documents,
                )
            except DatasetPreparationError as exc:
                errors.append(f"{override_key}: {exc}")
                continue
            resolved_evidence.append(resolved)
            evidence_count += 1
            canonicalized_count += int(canonicalized)
        item["evidence"] = resolved_evidence

    unused_overrides = sorted(set(override_values) - used_overrides)
    if errors:
        raise DatasetPreparationError("\n".join(errors))
    if unused_overrides:
        raise DatasetPreparationError(
            "存在未使用的 evidence override: " + "、".join(unused_overrides)
        )
    return dataset, {
        "candidate_batches": len(candidates),
        "questions": len(dataset["questions"]),
        "dialogues": len(dataset["dialogues"]),
        "dialogue_turns": sum(
            len(dialogue.get("turns", []))
            for dialogue in dataset["dialogues"]
        ),
        "evidence": evidence_count,
        "canonicalized_snippets": canonicalized_count,
        "applied_overrides": len(used_overrides),
    }


def _validate_review(
    review: Any,
    *,
    path: str,
    strict: bool,
    answerable_values: list[bool],
    result: ValidationResult,
) -> None:
    problems: list[str] = []
    if not isinstance(review, dict):
        problems.append("review 必须是对象")
    else:
        if review.get("status") != "verified":
            problems.append("review.status 必须为 verified")
        if not review.get("reviewer"):
            problems.append("reviewer 不能为空")
        if not review.get("reviewed_at"):
            problems.append("reviewed_at 不能为空")
        if review.get("rubric_version") != RUBRIC_VERSION:
            problems.append(f"rubric_version 必须为 {RUBRIC_VERSION}")
        if review.get("evidence_checked") is not True:
            problems.append("evidence_checked 必须为 true")
        if review.get("answer_checked") is not True:
            problems.append("answer_checked 必须为 true")
        if any(not answerable for answerable in answerable_values):
            if review.get("whole_corpus_checked") is not True:
                problems.append("不可回答题 whole_corpus_checked 必须为 true")
    if strict:
        result.errors.extend(f"{path}: {problem}" for problem in problems)
    elif problems:
        result.warnings.append(
            f"{path}: 人工审核尚未完成（{len(problems)} 项 strict 条件未满足）"
        )


def validate_dataset(
    dataset: dict[str, Any],
    *,
    parsed_documents: dict[str, ParsedDocument],
    manifest: dict[str, Any],
    current_manifest: dict[str, Any],
    strict: bool,
) -> ValidationResult:
    result = ValidationResult()
    if dataset.get("schema_version") != 3:
        result.errors.append("schema_version 必须为 3")
    for field_name in ("dataset_id", "corpus_id", "parser_version", "rubric_version"):
        if not isinstance(dataset.get(field_name), str) or not dataset[field_name].strip():
            result.errors.append(f"{field_name} 必须为非空字符串")
    if dataset.get("parser_version") != PARSER_VERSION:
        result.errors.append(f"parser_version 必须为 {PARSER_VERSION}")
    if dataset.get("rubric_version") != RUBRIC_VERSION:
        result.errors.append(f"rubric_version 必须为 {RUBRIC_VERSION}")
    if manifest != current_manifest:
        result.errors.append("corpus manifest 与当前确定性解析结果不一致")
    if dataset.get("corpus_id") != manifest.get("corpus_id"):
        result.errors.append("dataset 与 manifest 的 corpus_id 不一致")
    if dataset.get("parser_version") != manifest.get("parser_version"):
        result.errors.append("dataset 与 manifest 的 parser_version 不一致")

    item_ids: set[str] = set()
    group_splits: dict[str, str] = {}
    evidence_count = 0
    answerable_count = 0
    unanswerable_count = 0
    unanswerable_type_counts = {
        value: 0 for value in UNANSWERABLE_TYPES
    }
    split_counts = {split: 0 for split in SPLITS}

    def validate_answer_item(item: Any, path: str) -> bool:
        nonlocal evidence_count, answerable_count, unanswerable_count
        if not isinstance(item, dict):
            result.errors.append(f"{path}: 必须是对象")
            return True
        item_id = item.get("id", item.get("turn_id"))
        if not isinstance(item_id, str) or not item_id:
            result.errors.append(f"{path}: id/turn_id 必须为非空字符串")
        elif item_id in item_ids:
            result.errors.append(f"{path}: ID 重复: {item_id}")
        else:
            item_ids.add(item_id)
        for field_name in ("question", "reference_answer"):
            if not isinstance(item.get(field_name), str) or not item[field_name].strip():
                result.errors.append(f"{path}: {field_name} 必须为非空字符串")
        if "id" in item:
            if not isinstance(item.get("category"), str) or not item["category"].strip():
                result.errors.append(f"{path}: category 必须为非空字符串")
            if item.get("difficulty") not in {"easy", "medium", "hard"}:
                result.errors.append(f"{path}: difficulty 非法")
            document_types = item.get("document_types")
            allowed_document_types = {
                suffix.lstrip(".")
                for suffix in ADAPTERS
            }
            if (
                not isinstance(document_types, list)
                or not document_types
                or any(value not in allowed_document_types for value in document_types)
            ):
                result.errors.append(f"{path}: document_types 非法")
        if "turn_id" in item:
            standalone = item.get("standalone_question")
            if not isinstance(standalone, str) or not standalone.strip():
                result.errors.append(f"{path}: standalone_question 必须为非空字符串")

        answerable = item.get("answerable")
        if not isinstance(answerable, bool):
            result.errors.append(f"{path}: answerable 必须为布尔值")
            answerable = True
        evidence_values = item.get("evidence")
        keypoints = item.get("keypoints")
        if not isinstance(evidence_values, list):
            result.errors.append(f"{path}: evidence 必须为数组")
            evidence_values = []
        if not isinstance(keypoints, list):
            result.errors.append(f"{path}: keypoints 必须为数组")
            keypoints = []
        if answerable:
            answerable_count += 1
            if not evidence_values:
                result.errors.append(f"{path}: 可回答题必须有 evidence")
            if not keypoints:
                result.errors.append(f"{path}: 可回答题必须有 keypoints")
            if item.get("unanswerable_type") is not None:
                result.errors.append(f"{path}: 可回答题 unanswerable_type 必须为 null")
        else:
            unanswerable_count += 1
            if evidence_values:
                result.errors.append(f"{path}: 不可回答题 evidence 必须为空")
            unanswerable_type = item.get("unanswerable_type")
            if unanswerable_type not in UNANSWERABLE_TYPES:
                result.errors.append(f"{path}: unanswerable_type 非法")
            else:
                unanswerable_type_counts[unanswerable_type] += 1

        for keypoint_index, variants in enumerate(keypoints):
            if (
                not isinstance(variants, list)
                or not variants
                or any(not isinstance(value, str) or not value.strip() for value in variants)
            ):
                result.errors.append(
                    f"{path}.keypoints[{keypoint_index}]: 必须是非空字符串数组"
                )
                continue
            reference_answer = item.get("reference_answer")
            if (
                answerable
                and isinstance(reference_answer, str)
                and not any(
                    _metric_norm(variant) in _metric_norm(reference_answer)
                    for variant in variants
                )
            ):
                result.errors.append(
                    f"{path}.keypoints[{keypoint_index}]: "
                    "没有变体能命中 reference_answer"
                )

        for evidence_index, evidence in enumerate(evidence_values):
            evidence_count += 1
            evidence_path = f"{path}.evidence[{evidence_index}]"
            if not isinstance(evidence, dict):
                result.errors.append(f"{evidence_path}: 必须是对象")
                continue
            document_name = evidence.get("document_name")
            document = parsed_documents.get(document_name)
            if document is None:
                result.errors.append(f"{evidence_path}: 文档不存在")
                continue
            if evidence.get("parser_version") != dataset.get("parser_version"):
                result.errors.append(f"{evidence_path}: parser_version 不一致")
            locator = evidence.get("locator")
            if not isinstance(locator, dict) or not isinstance(
                locator.get("unit_index"), int
            ):
                result.errors.append(f"{evidence_path}: locator.unit_index 缺失")
                continue
            unit_index = locator["unit_index"]
            unit = next(
                (
                    value
                    for value in document.units
                    if value.locator.unit_index == unit_index
                ),
                None,
            )
            if unit is None:
                result.errors.append(f"{evidence_path}: locator 指向不存在的 unit")
                continue
            if locator != unit.locator.model_dump(exclude_none=True):
                result.errors.append(f"{evidence_path}: locator 与解析 unit 不一致")
            expected_unit_id = f"{document_name}:u{unit_index}"
            if evidence.get("unit_id") != expected_unit_id:
                result.errors.append(f"{evidence_path}: unit_id 不符合稳定规则")
            start = evidence.get("start_char")
            end = evidence.get("end_char")
            snippet = evidence.get("snippet")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or not isinstance(snippet, str)
                or unit.canonical_text[start:end] != snippet
            ):
                result.errors.append(f"{evidence_path}: offset 切片不等于 snippet")
                continue
            expected_hash = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
            if evidence.get("snippet_sha256") != expected_hash:
                result.errors.append(f"{evidence_path}: snippet_sha256 不一致")
        return answerable

    for question_index, question in enumerate(dataset.get("questions", [])):
        path = f"questions[{question_index}]"
        group_id = question.get("question_group")
        split = question.get("split")
        if not isinstance(group_id, str) or not group_id:
            result.errors.append(f"{path}: question_group 必须为非空字符串")
        if split not in SPLITS:
            result.errors.append(f"{path}: split 非法")
        else:
            split_counts[split] += 1
            previous = group_splits.setdefault(group_id, split)
            if previous != split:
                result.errors.append(f"{path}: 同一 group 不得跨 split")
        answerable = validate_answer_item(question, path)
        _validate_review(
            question.get("review"),
            path=path,
            strict=strict,
            answerable_values=[answerable],
            result=result,
        )

    dialogue_ids: set[str] = set()
    for dialogue_index, dialogue in enumerate(dataset.get("dialogues", [])):
        path = f"dialogues[{dialogue_index}]"
        dialogue_id = dialogue.get("dialogue_id")
        group_id = dialogue.get("question_group")
        split = dialogue.get("split")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            result.errors.append(f"{path}: dialogue_id 必须为非空字符串")
        elif dialogue_id in dialogue_ids:
            result.errors.append(f"{path}: dialogue_id 重复: {dialogue_id}")
        else:
            dialogue_ids.add(dialogue_id)
        if not isinstance(group_id, str) or not group_id:
            result.errors.append(f"{path}: question_group 必须为非空字符串")
        if dialogue.get("difficulty") not in {"easy", "medium", "hard"}:
            result.errors.append(f"{path}: difficulty 非法")
        document_types = dialogue.get("document_types")
        allowed_document_types = {
            suffix.lstrip(".")
            for suffix in ADAPTERS
        }
        if (
            not isinstance(document_types, list)
            or not document_types
            or any(value not in allowed_document_types for value in document_types)
        ):
            result.errors.append(f"{path}: document_types 非法")
        if split not in SPLITS:
            result.errors.append(f"{path}: split 非法")
        else:
            split_counts[split] += 1
            previous = group_splits.setdefault(group_id, split)
            if previous != split:
                result.errors.append(f"{path}: 同一 group 不得跨 split")
        turns = dialogue.get("turns")
        if not isinstance(turns, list) or not turns:
            result.errors.append(f"{path}: turns 必须为非空数组")
            turns = []
        answerable_values = [
            validate_answer_item(turn, f"{path}.turns[{turn_index}]")
            for turn_index, turn in enumerate(turns)
        ]
        _validate_review(
            dialogue.get("review"),
            path=path,
            strict=strict,
            answerable_values=answerable_values,
            result=result,
        )

    result.stats = {
        "questions": len(dataset.get("questions", [])),
        "dialogues": len(dataset.get("dialogues", [])),
        "dialogue_turns": sum(
            len(dialogue.get("turns", []))
            for dialogue in dataset.get("dialogues", [])
            if isinstance(dialogue, dict)
        ),
        "answerable_items": answerable_count,
        "unanswerable_items": unanswerable_count,
        "unanswerable_type_counts": unanswerable_type_counts,
        "evidence": evidence_count,
        "group_split_counts": split_counts,
        "parsed_corpus_sha256": current_manifest.get("parsed_corpus_sha256"),
    }
    return result


def _dataset_sha256(dataset: dict[str, Any]) -> str:
    serialized = json.dumps(
        dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _review_evidence_markdown(evidence_values: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if not evidence_values:
        return ["_无 evidence；不可回答题需执行 whole-corpus 核查。_", ""]
    for index, evidence in enumerate(evidence_values, start=1):
        locator = evidence.get("locator") or {}
        lines.extend(
            [
                f"#### Evidence {index}",
                "",
                f"- 文档：`{evidence.get('document_name', '')}`",
                (
                    f"- 定位：`{evidence.get('unit_id', '')}`，"
                    f"{locator.get('location_label', '')}，"
                    f"`[{evidence.get('start_char')}:{evidence.get('end_char')})`"
                ),
                "",
                "```text",
                str(evidence.get("snippet", "")),
                "```",
                "",
            ]
        )
    return lines


def _review_item_markdown(
    item: dict[str, Any],
    *,
    heading_level: int,
) -> list[str]:
    item_id = item.get("id", item.get("turn_id", "unknown"))
    prefix = "#" * heading_level
    lines = [
        f"{prefix} {item_id}",
        "",
        f"**问题：** {item.get('question', '')}",
        "",
    ]
    if "turn_id" in item:
        lines.extend(
            [
                f"**独立改写：** {item.get('standalone_question', '')}",
                "",
            ]
        )
    lines.extend(
        [
            f"**可回答：** `{str(item.get('answerable')).lower()}`",
            "",
            f"**不可回答类型：** `{item.get('unanswerable_type')}`",
            "",
            "**标准答案：**",
            "",
            str(item.get("reference_answer", "")),
            "",
            "**Keypoints：**",
            "",
        ]
    )
    for index, variants in enumerate(item.get("keypoints", []), start=1):
        lines.append(f"{index}. " + " / ".join(f"`{value}`" for value in variants))
    if not item.get("keypoints"):
        lines.append("_无 keypoint。_")
    lines.extend(["", "**Evidence：**", ""])
    lines.extend(_review_evidence_markdown(item.get("evidence", [])))
    return lines


def _review_checklist(*, has_unanswerable: bool) -> list[str]:
    lines = [
        "- [ ] 问题清晰、无歧义，难度和分类合理",
        "- [ ] 每条 evidence 均直接支持对应答案",
        "- [ ] 标准答案事实正确且没有超出 evidence",
        "- [ ] keypoint 分组与允许变体合理",
    ]
    if has_unanswerable:
        lines.append("- [ ] 已检查整个 corpus，确认不可回答类型正确")
    lines.extend(
        [
            "- [ ] split 可接受，未发现同组泄漏",
            "",
            "**决定：** `approve / revise / reject`",
            "",
            "**Reviewer：**",
            "",
            "**备注：**",
            "",
        ]
    )
    return lines


def build_review_packet(dataset: dict[str, Any]) -> str:
    """导出人工审核包；不写 verified 状态，也不代替人工结论。"""
    questions = dataset.get("questions", [])
    dialogues = dataset.get("dialogues", [])
    turns = [
        turn
        for dialogue in dialogues
        for turn in dialogue.get("turns", [])
    ]
    answer_items = [*questions, *turns]
    split_counts = {
        split: sum(
            1
            for item in [*questions, *dialogues]
            if item.get("split") == split
        )
        for split in SPLITS
    }
    unanswerable_type_counts = {
        value: sum(
            1
            for item in answer_items
            if item.get("unanswerable_type") == value
        )
        for value in UNANSWERABLE_TYPES
    }
    lines = [
        "# Dataset v3 人工审核包",
        "",
        f"- Dataset：`{dataset.get('dataset_id')}`",
        f"- Corpus：`{dataset.get('corpus_id')}`",
        f"- Parser：`{dataset.get('parser_version')}`",
        f"- Dataset SHA-256：`{_dataset_sha256(dataset)}`",
        (
            f"- 规模：{len(questions)} 道单题，{len(dialogues)} 组对话，"
            f"{len(turns)} 个 dialogue turn"
        ),
        (
            "- Group split："
            + "，".join(f"{key}={value}" for key, value in split_counts.items())
        ),
        (
            "- 不可回答类型："
            + "，".join(
                f"{key}={value}"
                for key, value in unanswerable_type_counts.items()
            )
        ),
        "",
        "> 此文件只用于人工逐项核对。勾选、Reviewer 和决定不会自动写回数据集；",
        "> 未完成真实人工审核前，不得把 review.status 改为 verified。",
        "",
        "## 单题",
        "",
    ]
    for question in questions:
        lines.extend(_review_item_markdown(question, heading_level=3))
        lines.extend(
            _review_checklist(has_unanswerable=not question.get("answerable", True))
        )

    lines.extend(["## 多轮对话", ""])
    for dialogue in dialogues:
        lines.extend(
            [
                f"### {dialogue.get('dialogue_id')}",
                "",
                (
                    f"- Split：`{dialogue.get('split')}`；"
                    f"Difficulty：`{dialogue.get('difficulty')}`"
                ),
                "",
            ]
        )
        dialogue_turns = dialogue.get("turns", [])
        for turn in dialogue_turns:
            lines.extend(_review_item_markdown(turn, heading_level=4))
        lines.extend(
            _review_checklist(
                has_unanswerable=any(
                    not turn.get("answerable", True)
                    for turn in dialogue_turns
                )
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: str | Path, value: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dataset v3 prepare/validate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="补齐确定性来源字段")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--corpus", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--report")
    prepare.add_argument("--overrides")
    prepare.add_argument("--dataset-id", default="general-courseware-v1")
    prepare.add_argument("--corpus-id", default="general-courseware-v1")

    validate = subparsers.add_parser("validate", help="校验已 prepare 的 v3 数据")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--corpus", required=True)
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--report")
    validate.add_argument("--strict", action="store_true")

    review = subparsers.add_parser("review-export", help="导出人工逐题审核包")
    review.add_argument("--dataset", required=True)
    review.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "review-export":
        dataset_objects = load_json_objects(args.dataset)
        if len(dataset_objects) != 1:
            raise DatasetPreparationError(
                "review-export 要求 Dataset v3 只有一个顶层对象"
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        packet = build_review_packet(dataset_objects[0])
        output.write_text(packet, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "dataset_sha256": _dataset_sha256(dataset_objects[0]),
                    "output": str(output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    parsed_documents = parse_corpus(args.corpus)

    if args.command == "prepare":
        candidates = load_json_objects(args.input)
        overrides = load_overrides(args.overrides)
        dataset, prepare_report = prepare_dataset(
            candidates,
            parsed_documents,
            dataset_id=args.dataset_id,
            corpus_id=args.corpus_id,
            overrides=overrides,
        )
        manifest = build_corpus_manifest(
            args.corpus,
            parsed_documents,
            corpus_id=args.corpus_id,
        )
        validation = validate_dataset(
            dataset,
            parsed_documents=parsed_documents,
            manifest=manifest,
            current_manifest=manifest,
            strict=False,
        )
        report = {
            "prepare": prepare_report,
            "validation": validation.model_dump(),
        }
        if not validation.ok:
            raise DatasetPreparationError("\n".join(validation.errors))
        _write_json(args.output, dataset)
        _write_json(args.manifest, manifest)
        if args.report:
            _write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    dataset_objects = load_json_objects(args.dataset)
    if len(dataset_objects) != 1:
        raise DatasetPreparationError("validate 要求 Dataset v3 只有一个顶层对象")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    current_manifest = build_corpus_manifest(
        args.corpus,
        parsed_documents,
        corpus_id=dataset_objects[0].get("corpus_id", ""),
    )
    result = validate_dataset(
        dataset_objects[0],
        parsed_documents=parsed_documents,
        manifest=manifest,
        current_manifest=current_manifest,
        strict=args.strict,
    )
    if args.report:
        _write_json(args.report, result.model_dump())
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
