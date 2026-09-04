"""Build an isolated, deterministic QA-only revision; never overwrite its parent.

This is a bulk data transformation, not a retrieval run. It uses the project's
structured-v2 parser and validator without loading neural models or databases.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT.parent
PROJECT_ROOT = FIXTURE_ROOT.parents[1]
PARENT_ROOT = FIXTURE_ROOT / "v3"
PARENT_DATASET = PARENT_ROOT / "questions_v3_120q.json"
CORPUS_ROOT = FIXTURE_ROOT / "corpus"
CORPUS_MANIFEST = FIXTURE_ROOT / "corpus_v3_manifest.json"
PARENT_AUDIT = PARENT_ROOT / "AI预审风险队列-20260823.md"
SPEC_PATH = ROOT / "correction_spec.json"
sys.path.insert(0, str(PROJECT_ROOT))

from eval.dataset_runtime import normalize_for_evaluation  # noqa: E402
from eval.dataset_v3 import (  # noqa: E402
    build_corpus_manifest,
    parse_corpus,
    resolve_evidence,
    validate_dataset,
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def protected_snapshot() -> dict[str, Any]:
    """Hash parent static artifacts; corpus content is checked by its manifest."""
    paths = [
        path for path in PARENT_ROOT.rglob("*")
        if path.is_file()
        and not {"runs", "__pycache__"}.intersection(path.relative_to(PARENT_ROOT).parts)
    ]
    paths += [
        FIXTURE_ROOT / "questions_v3.json",
        FIXTURE_ROOT / "provenance.json",
        FIXTURE_ROOT / "selection_manifest.json",
        FIXTURE_ROOT / "README.md",
        CORPUS_MANIFEST,
        PROJECT_ROOT / "docs" / "新增测试集和运行优化v3方案.md",
    ]
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): {
            "sha256": sha256(path), "bytes": path.stat().st_size,
        }
        for path in sorted(set(paths))
    }


def evidence_from_selector(selector: dict, old: dict, parsed: dict) -> dict:
    name = selector["document_name"]
    if selector.get("use_parent_snippet"):
        matches = [item for item in old["evidence"] if item["document_name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Parent evidence is not unique: {name}")
        resolved, _ = resolve_evidence(matches[0], parsed)
        return resolved
    matches = []
    for unit in parsed[name].units:
        text = unit.canonical_text
        start_anchor = selector["start_anchor"]
        end_anchor = selector["end_anchor"]
        if text.count(start_anchor) == 1 and text.count(end_anchor) == 1:
            start = text.index(start_anchor)
            end = text.index(end_anchor) + len(end_anchor)
            if end > start:
                matches.append((unit, start, end))
    if len(matches) != 1:
        raise ValueError(f"Evidence anchors must match a unique canonical unit: {name}")
    unit, start, end = matches[0]
    resolved, _ = resolve_evidence(
        {
            "document_name": name,
            "snippet": unit.canonical_text[start:end],
            "start_char": start,
            "unit_index": unit.locator.unit_index,
        },
        parsed,
    )
    return resolved


def changed_fields(before: dict, after: dict, prefix: str = "") -> list[str]:
    result = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        path = f"{prefix}.{key}" if prefix else key
        if old != new:
            if isinstance(old, dict) and isinstance(new, dict):
                result.extend(changed_fields(old, new, path))
            else:
                result.append(path)
    return result


def revise_dataset(parent: dict, spec: dict, parsed: dict) -> tuple[dict, list[dict]]:
    dataset = copy.deepcopy(parent)
    dataset["dataset_id"] = spec["dataset_id"]
    items = {item["id"]: item for item in dataset["questions"]}
    old_items = {item["id"]: item for item in parent["questions"]}
    records = []
    ids = [change["old_id"] for change in spec["corrections"]]
    if len(ids) != 10 or len(set(ids)) != 10 or not set(ids) <= old_items.keys():
        raise ValueError("The revision must target exactly 10 unique parent questions")
    for change in spec["corrections"]:
        old_id = change["old_id"]
        old = old_items[old_id]
        new = items[old_id]
        if old["answerable"] is not True:
            raise ValueError("This revision must not modify unanswerable questions")
        for key in ("question", "reference_answer"):
            if key in change:
                new[key] = change[key]
        # Keep the original whole-answer keypoint convention; no rubric change.
        new["keypoints"] = [[new["reference_answer"]]]
        if "evidence" in change:
            new["evidence"] = [
                evidence_from_selector(selector, old, parsed)
                for selector in change["evidence"]
            ]
        else:
            by_name = {item["document_name"]: item for item in old["evidence"]}
            new["evidence"] = [
                resolve_evidence(by_name[name], parsed)[0]
                for name in change["keep_documents"]
            ]
        old_names = [item["document_name"] for item in old["evidence"]]
        new_names = [item["document_name"] for item in new["evidence"]]
        count = len(set(new_names))
        if len(new_names) != count or count not in (1, 2, 3):
            raise ValueError(f"Invalid gold-document set: {old_id}")
        new["source"]["gold_document_count"] = count
        if count != old["source"]["gold_document_count"]:
            kind = {1: "1doc", 2: "2docs", 3: "3docs"}[count]
            old_kind = old["category"].removeprefix("questanswer_")
            new["category"] = f"questanswer_{kind}"
            # A 2docs version of H-A9 already exists. Include revision+lineage
            # rather than colliding with another upstream question's ID/group.
            suffix = f"{old['source']['upstream_id']}-v31-from-{old_kind}"
            new["id"] = f"crud-qa-{kind}-{suffix}"
            new["question_group"] = f"crud-questanswer_{kind}-{suffix}"
            new["difficulty"] = {1: "easy", 2: "medium", 3: "hard"}[count]
        fields = changed_fields(old, new)
        if not fields:
            raise ValueError(f"No effective correction for {old_id}")
        records.append({
            "risk_id": change["risk_id"],
            "title": change["title"],
            "reason": change["reason"],
            "old_id": old_id,
            "new_id": new["id"],
            "changed_fields": fields,
            "removed_gold_documents": sorted(set(old_names) - set(new_names)),
            "residual_notes": change["residual_notes"],
            "before": copy.deepcopy(old),
            "after": copy.deepcopy(new),
        })
    return dataset, records


def review_markdown(records: list[dict], dataset: dict, digest: str) -> str:
    lines = [
        "# v3.1：10题修正对照与证据", "",
        f"- 数据集：`{dataset['dataset_id']}`",
        f"- 文件 SHA-256：`{digest}`",
        "- 日期：2026-08-27；AI修正与静态校验，不是人工 verified。",
        "- 所有原题与原证据保存在 change_manifest.json；原版文件未改。", "",
        "## 全文复核纠错", "",
        "H-A9 正文明确写“阿尔及利亚朋友们”。先前答复依据错配的 upstream_event "
        "建议改为阿联酋，是错误的，已撤回。H-A7 的全文确有特别关注日本和领土争端的描述；"
        "之前说全文没有支持不准确，真正的问题是旧答案范围过窄、旧evidence范围不足。", "",
    ]
    for record in records:
        old, new = record["before"], record["after"]
        lines += [
            f"## {record['risk_id']} {record['title']}", "",
            f"- 旧ID：`{old['id']}`", f"- 新ID：`{new['id']}`",
            f"- 分类：`{old['category']}` → `{new['category']}`；split保留 `{new['split']}`",
            f"- 修改字段：{', '.join(record['changed_fields'])}", "",
            f"**原因：** {record['reason']}", "",
            f"**原问题：** {old['question']}", "",
            f"**新问题：** {new['question']}", "",
            f"**原答案：** {old['reference_answer']}", "",
            f"**新答案：** {new['reference_answer']}", "",
            "### 新版直接证据", "",
        ]
        for index, ev in enumerate(new["evidence"], 1):
            lines += [
                f"{index}. `{ev['document_name']}` · `{ev['unit_id']}` "
                f"· `[{ev['start_char']}, {ev['end_char']})`",
                "", "```text", ev["snippet"], "```", "",
                f"片段SHA-256：`{ev['snippet_sha256']}`", "",
            ]
        lines += ["### 保留限制", ""]
        lines += [f"- {note}" for note in record["residual_notes"]]
        lines += ["", "审核状态保持 `draft`；本页不自动更改 checked 标志。", ""]
    return "\n".join(lines).rstrip() + "\n"


def risk_markdown(records: list[dict], digest: str) -> str:
    old_text = PARENT_AUDIT.read_text(encoding="utf-8-sig")
    inherited = old_text.split("## 4. 高风险队列：不可回答", 1)[1].split(
        "## 9. 待用户确认的最小决策", 1
    )[0]
    lines = [
        "# Dataset v3.1：修正后风险队列", "",
        "> 修订日期：2026-08-27；原预审：2026-08-23。原报告保持不动。",
        "> 本轮只修正10道可回答题，不重复宣称执行了全120题AI预审或检索。", "",
        f"- 新版文件SHA-256：`{digest}`",
        "- 新版目录：`eval/crud_rag_1000/v3_1/`；120题仍全部draft。", "",
        "## 1. 当前分类（在旧预审结论上叠加本轮10题修正）", "",
        "| 分类 | 数量 | 当前说明 |", "|---|---:|---|",
        "| 原预审未发现明显内容问题 | 60 | 继承旧结论，未改题 |",
        "| 本轮已修正的原高风险可回答题 | 10 | 已补齐字段和证据校验，仍非人工verified |",
        "| 内容可用的检索/指标压力题 | 21 | 保留，不因检索失败删题 |",
        "| 尚待处理的内容/标签/说明问题 | 29 | 4道高风险不可回答 + 25道中风险 |", "",
        "合计120题。没有新增或替换语料，没有改变任何不可回答标签。", "",
        "## 2. 对先前说明的纠错", "",
        "- H-A9的原始正文明确为阿尔及利亚。先前答复提出改成阿联酋是误用错配摘要，已撤回。",
        "- H-A7全文支持特别关注日本；新版扩展证据，答案改为美国及其地区盟友、特别关注日本。",
        "- H-A10全文可区分自办第10年和第11届，现限定第10年，不混用两个场次。", "",
        "## 3. 10道修正题", "",
        "完整前后对照、题目ID、原答案和可核对证据见 [10题修正对照与证据](10题修正对照与证据.md)。", "",
    ]
    lines += [f"- {r['risk_id']}：{r['title']}；新ID `{r['new_id']}`。" for r in records]
    lines += [
        "", "### 仍需注意", "",
        "去重后的替代报道仍在corpus内。当前严格文档ID指标不自动接受等价报道，"
        "可能出现命中替代报道却不计分的情况；本轮没有调整评分指标，不能把未来的分数变化全部解释为检索改善。",
        "", "以下第4至8节继承原预审，题目内容和标签本轮未改，且未重新运行相关检索。", "",
        "## 4. 高风险队列：不可回答" + inherited.rstrip(), "",
        "## 9. 尚未完成", "",
        "- 4道高风险不可回答题和25道中风险题继续保留draft及原预审备注。",
        "- 尚未执行新版120题检索重跑；原CPU/GPU指标不是本版评测结果。",
        "- 不可回答全语料人工核验、参数校准与人工verified均未完成。",
        "- 不自动推广到eval默认题库或生产环境。", "",
    ]
    return "\n".join(lines)


def build_artifacts() -> tuple[dict[str, bytes], dict]:
    before = protected_snapshot()
    spec = read_json(SPEC_PATH)
    if sha256(PARENT_DATASET) != spec["parent_dataset_sha256"]:
        raise ValueError("Parent dataset changed; stop rather than silently rebasing")
    if sha256(CORPUS_MANIFEST) != spec["corpus_manifest_sha256"]:
        raise ValueError("Corpus manifest changed; stop before creating any artifact")
    parent = read_json(PARENT_DATASET)
    manifest = read_json(CORPUS_MANIFEST)
    parsed = parse_corpus(CORPUS_ROOT)
    current_manifest = build_corpus_manifest(
        CORPUS_ROOT, parsed, corpus_id=parent["corpus_id"],
    )
    if current_manifest != manifest or len(parsed) != 1000:
        raise ValueError("Corpus no longer matches the frozen 1000-document manifest")
    dataset, records = revise_dataset(parent, spec, parsed)
    validation = validate_dataset(
        dataset, parsed_documents=parsed, manifest=manifest,
        current_manifest=current_manifest, strict=False,
    )
    strict = validate_dataset(
        dataset, parsed_documents=parsed, manifest=manifest,
        current_manifest=current_manifest, strict=True,
    )
    if not validation.ok:
        raise ValueError("\n".join(validation.errors))
    runtime = normalize_for_evaluation(dataset)
    if len(runtime["questions"]) != 120:
        raise ValueError("Runtime must expose all 120 questions")
    after = protected_snapshot()
    if before != after:
        raise ValueError("Protected original artifacts changed during the build")
    corrections_by_old = {r["old_id"]: r for r in records}
    mapping = []
    for old, new in zip(parent["questions"], dataset["questions"], strict=True):
        correction = corrections_by_old.get(old["id"])
        if old["split"] != new["split"] or old["review"] != new["review"]:
            raise ValueError("Split and review state must not change")
        if not correction and old != new:
            raise ValueError("An out-of-scope question changed")
        mapping.append({
            "old_id": old["id"], "new_id": new["id"],
            "old_question_group": old["question_group"],
            "new_question_group": new["question_group"],
            "old_category": old["category"], "new_category": new["category"],
            "split": new["split"], "content_changed": correction is not None,
            "id_changed": old["id"] != new["id"],
            "query_changed": old["question"] != new["question"],
            "risk_id": correction["risk_id"] if correction else None,
        })
    question_ids = [q["id"] for q in dataset["questions"]]
    if len(set(question_ids)) != 120:
        raise ValueError("Question ID collision")
    category_counts = dict(Counter(q["category"] for q in dataset["questions"] if q["answerable"]))
    expected = {"questanswer_1doc": 37, "questanswer_2docs": 33, "questanswer_3docs": 30}
    if category_counts != expected:
        raise ValueError(f"Unexpected corrected category distribution: {category_counts}")
    active_gold = sorted({e["document_name"] for q in dataset["questions"] for e in q["evidence"]})
    summary = {
        "dataset_id": dataset["dataset_id"], "questions": 120,
        "answerable": 100, "unanswerable": 20, "changed_questions": len(records),
        "unchanged_questions": sum(not m["content_changed"] for m in mapping),
        "renamed_ids": sum(m["id_changed"] for m in mapping),
        "changed_queries": sum(m["query_changed"] for m in mapping),
        "answerable_category_counts": category_counts,
        "split_counts": dict(Counter(q["split"] for q in dataset["questions"])),
        "evidence_count": validation.stats["evidence"],
        "active_gold_document_count": len(active_gold), "corpus_document_count": len(parsed),
        "protected_original_file_count": len(before), "protected_original_files_unchanged": True,
        "corpus_matches_original_manifest": True, "runtime_normalization_ok": True,
        "retrieval_rerun": False, "review_status": "draft",
    }
    artifacts = {"questions_v3_120q.json": json_bytes(dataset)}
    digest = hashlib.sha256(artifacts["questions_v3_120q.json"]).hexdigest()
    artifacts["change_manifest.json"] = json_bytes({
        "schema_version": 1, "revision_date": spec["revision_date"],
        "parent_dataset_id": parent["dataset_id"], "dataset_id": dataset["dataset_id"],
        "parent_dataset_sha256": sha256(PARENT_DATASET), "dataset_sha256": digest,
        "source_policy": "upstream_task/id/event are raw lineage, not corrected facts; only gold_document_count tracks current required gold",
        "summary": summary, "corrections": records,
    })
    artifacts["id_mapping.json"] = json_bytes({
        "schema_version": 1, "parent_dataset_id": parent["dataset_id"],
        "dataset_id": dataset["dataset_id"], "mapping": mapping,
    })
    artifacts["selection_manifest.json"] = json_bytes({
        "schema_version": 1, "dataset_id": dataset["dataset_id"], "summary": summary,
        "active_gold_documents": active_gold,
        "non_gold_corpus_documents": sorted(set(parsed) - set(active_gold)),
        "physical_corpus_folders_unchanged": True,
        "question_gold_mapping": [{
            "id": q["id"], "category": q["category"], "answerable": q["answerable"],
            "gold_documents": [e["document_name"] for e in q["evidence"]],
            "source": copy.deepcopy(q["source"]),
        } for q in dataset["questions"]],
    })
    artifacts["validation_report.json"] = json_bytes({
        "summary": summary, "validation": validation.model_dump(),
        "strict_validation": strict.model_dump(),
        "strict_status_explanation": "Expected failure: draft is intentionally not promoted to human verified",
        "project_llm_api_called": False, "embedding_inference_used": False,
        "reranker_inference_used": False, "network_used": False, "database_used": False,
    })
    artifacts["source_preservation.json"] = json_bytes({
        "schema_version": 1, "unchanged": before == after,
        "scope": "parent v3 static files plus original 100q, manifests, README and design document; corpus checked file-by-file against manifest; run databases not opened",
        "files": before,
    })
    artifacts["10题修正对照与证据.md"] = review_markdown(records, dataset, digest).encode("utf-8")
    artifacts["AI预审风险队列-v3.1.md"] = risk_markdown(records, digest).encode("utf-8")
    artifacts["provenance.json"] = json_bytes({
        "schema_version": 1, "algorithm_version": "qa-only-revision-v3.1-20260827",
        "dataset_id": dataset["dataset_id"], "corpus_id": dataset["corpus_id"],
        "revision_date": spec["revision_date"], "deterministic": True,
        "ai_assisted_corrections": True, "project_llm_api_called": False,
        "network_used": False, "database_used": False, "retrieval_rerun": False,
        "parent_dataset": {"path": "../v3/questions_v3_120q.json", "sha256": sha256(PARENT_DATASET)},
        "reused_corpus_manifest": {"path": "../corpus_v3_manifest.json", "sha256": sha256(CORPUS_MANIFEST)},
        "correction_spec": {"path": "correction_spec.json", "sha256": sha256(SPEC_PATH)},
        "generator": {"path": "build_revision.py", "sha256": sha256(Path(__file__))},
        "outputs": [{
            "path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        } for name, data in artifacts.items()],
        "review_policy": "All original review flags preserved; no human verified claim",
    })
    return artifacts, summary


def publish(artifacts: dict[str, bytes], output: Path, *, check: bool) -> None:
    output = output.absolute()
    # Check links before resolve, then check the resolved boundary as well.
    if not output.is_relative_to(ROOT):
        raise ValueError("Output must stay inside the new v3_1 directory")
    for path in [output, *output.parents]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Linked output path is not allowed: {path}")
        if path == ROOT:
            break
    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Resolved output escaped the new version directory")
    # Preflight ALL outputs before writing any; existing differing files are
    # never overwritten, including on repeated builds or interrupted builds.
    for name, data in artifacts.items():
        target = output / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Invalid output target: {target}")
        if target.exists() and target.read_bytes() != data:
            raise FileExistsError(f"Refusing to overwrite differing artifact: {target}")
        if check and not target.is_file():
            raise FileNotFoundError(f"Missing generated artifact: {target}")
    if not check:
        output.mkdir(parents=True, exist_ok=True)
        for name, data in artifacts.items():
            target = output / name
            if not target.exists():
                with target.open("xb") as stream:
                    stream.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或只读校验独立v3.1修正版")
    parser.add_argument("--check", action="store_true", help="只读比对，禁止写入")
    parser.add_argument("--output", type=Path, default=ROOT, help="仅允许v3_1目录或其子目录")
    args = parser.parse_args(argv)
    artifacts, summary = build_artifacts()
    publish(artifacts, args.output, check=args.check)
    print(json.dumps({"mode": "check" if args.check else "build", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
