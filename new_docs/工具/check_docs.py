"""Static checks for new_docs; no backend import, network, model, or business-data writes.

Run from any directory with an installed Python:
    python new_docs/工具/check_docs.py
    python new_docs/工具/check_docs.py --check-preserved --self-test
Only --report writes a result, and only inside this script's directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, deque
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

DOC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DOC_ROOT.parent
TOOL_ROOT = Path(__file__).resolve().parent
CATEGORIES = ("需求", "设计", "开发", "优化", "测试", "运维")
PREFIX_CATEGORY = {
    "REQ": "需求", "DES": "设计", "DEV": "开发", "OPT": "优化",
    "TEST": "测试", "OPS": "运维", "BUG": "测试",
}
STATUSES = {"规划中", "设计中", "开发中", "测试中", "已完成", "已废弃", "已替代"}
CORE_NAME = re.compile(r"^((REQ|DES|DEV|OPT|TEST|OPS|BUG)-\d{3})-(.+)\.md$")
CORE_ID = re.compile(r"^(REQ|DES|DEV|OPT|TEST|OPS|BUG)-\d{3}$")
LINK = re.compile(r'!?\[([^\]\n]+)\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+["\'][^\n]*?["\'])?\s*\)')
EXPLICIT_ANCHOR = re.compile(r'<a\s+(?:id|name)=["\']([^"\']+)["\']\s*>\s*</a>', re.I)
EXEMPT_NAMES = {"README.md", "AGENTS.md", "新旧文档对照.md"}
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|file:|vscode:)", re.I)
REQUIRED = {
    "REQ": {"DES"}, "DES": {"REQ"}, "DEV": {"DES"}, "TEST": {"DEV"},
    "OPT": {"DEV"}, "OPS": {"DEV", "DES"}, "BUG": {"DEV"},
}


def unfenced(text: str) -> str:
    """Blank fenced code while preserving line numbers."""
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s*(\x60{3,}|~{3,})", line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append("" if fence else line)
    return "\n".join(lines)


def anchors(text: str) -> set[str]:
    clean = unfenced(text)
    result = set(EXPLICIT_ANCHOR.findall(clean))
    counts: Counter[str] = Counter()
    for line in clean.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", "", match.group(1))
        title = LINK.sub(lambda m: m.group(1), title)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        number = counts[slug]
        counts[slug] += 1
        result.add(slug if not number else f"{slug}-{number}")
    return result


def field(text: str, name: str) -> str | None:
    match = re.search(r"^- " + re.escape(name) + r"[：:]\s*(.*?)\s*$", unfenced(text), re.M)
    return match.group(1) if match else None


def association(text: str) -> str:
    match = re.search(r"^## 关联文档\s*$\n(.*?)(?=^## |\Z)", unfenced(text), re.M | re.S)
    return match.group(1) if match else ""


def local_target(source: Path, destination: str) -> tuple[Path | None, str, str | None]:
    destination = destination.strip("<>")
    if ABSOLUTE.match(destination):
        return None, "", "absolute_link"
    parsed = urlsplit(destination)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https", "mailto"}:
            return None, "", "unsupported_link"
        return None, "", None
    target = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
    if not target.is_relative_to(REPO_ROOT):
        return None, "", "outside_repository"
    return target, unquote(parsed.fragment), None


def read_texts() -> dict[str, str]:
    return {
        p.relative_to(DOC_ROOT).as_posix(): p.read_text(encoding="utf-8-sig")
        for p in DOC_ROOT.rglob("*.md")
    }


def validate(texts: dict[str, str], preserve: bool = False) -> dict:
    errors: list[dict] = []
    core: dict[str, tuple[str, str]] = {}
    paths_to_ids: dict[Path, str] = {}
    id_paths: dict[str, list[str]] = {}
    link_count = 0
    checked_sources = 0

    def error(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "file": path, "message": message})

    def target_text(target: Path) -> str | None:
        if target.is_relative_to(DOC_ROOT):
            return texts.get(target.relative_to(DOC_ROOT).as_posix())
        if target.is_file() and target.suffix.lower() == ".md":
            return target.read_text(encoding="utf-8-sig")
        return None

    for key, content in texts.items():
        path = DOC_ROOT / key
        match = CORE_NAME.fullmatch(path.name)
        if not match:
            if path.name not in EXEMPT_NAMES:
                error("filename", key, "Non-navigation Markdown must have a stable core ID.")
            continue
        expected_id, prefix = match.group(1), match.group(2)
        if path.parent.name != PREFIX_CATEGORY[prefix]:
            error("category", key, "ID prefix and category disagree.")
        doc_id = field(content, "文档ID")
        if not doc_id or not CORE_ID.fullmatch(doc_id):
            error("metadata", key, "Missing or invalid document ID.")
            doc_id = expected_id
        id_paths.setdefault(doc_id, []).append(key)
        if doc_id != expected_id or not content.startswith(f"# {expected_id} "):
            error("id_filename", key, "Heading, metadata ID and filename must agree.")
        for name in ("文档类型", "状态", "创建日期", "最后更新"):
            if not field(content, name):
                error("metadata", key, "Missing metadata: " + name)
        expected_type = "Bug" if prefix == "BUG" else PREFIX_CATEGORY[prefix]
        if field(content, "文档类型") != expected_type:
            error("metadata_type", key, "Incorrect document type.")
        if field(content, "状态") not in STATUSES:
            error("status", key, "Unknown lifecycle status.")
        try:
            created = date.fromisoformat(field(content, "创建日期") or "")
            updated = date.fromisoformat(field(content, "最后更新") or "")
            if updated < created:
                error("date", key, "Update precedes creation.")
        except ValueError:
            error("date", key, "Invalid ISO date.")
        if not association(content).strip():
            error("association", key, "Missing explicit related-documents section.")
        if "## 变更记录" not in content:
            error("changes", key, "Missing change history.")
        if field(content, "状态") == "已替代" and "替代文档" not in content:
            error("replacement", key, "Replaced documents need a replacement link.")
        if prefix == "BUG" and not all(word in content for word in ("修复", "回归")):
            error("bug_chain", key, "Bug lacks fix/regression explanation.")
        core[doc_id] = (key, content)
        paths_to_ids[path.resolve()] = doc_id

    for doc_id, paths in id_paths.items():
        if len(paths) > 1:
            error("duplicate_id", ", ".join(paths), doc_id)

    for key, content in texts.items():
        source = (DOC_ROOT / key).resolve()
        for match in LINK.finditer(unfenced(content)):
            link_count += 1
            destination = match.group(2)
            target, fragment, issue = local_target(source, destination)
            if issue:
                error(issue, key, destination)
                continue
            if target is None:
                continue
            new_markdown = target.is_relative_to(DOC_ROOT) and target.suffix.lower() == ".md"
            exists = (
                target.relative_to(DOC_ROOT).as_posix() in texts
                if new_markdown else target.exists()
            )
            if not exists:
                error("broken_link", key, destination)
                continue
            if fragment and target.suffix.lower() == ".md":
                body = target_text(target)
                if body is None or fragment not in anchors(body):
                    error("broken_anchor", key, destination)

    needed_readmes = {"README.md"} | {f"{category}/README.md" for category in CATEGORIES}
    needed_readmes |= {str(Path(key).parent / "README.md").replace("\\", "/") for key in texts}
    for key in sorted(needed_readmes):
        if key not in texts:
            error("navigation", key, "Directory README is missing.")
    root_links = {
        m.group(2).strip("<>") for m in LINK.finditer(unfenced(texts.get("README.md", "")))
    }
    for category in CATEGORIES:
        if f"./{category}/README.md" not in root_links:
            error("navigation", "README.md", "Missing category " + category)
    for doc_id, (key, content) in core.items():
        nav_key = str(Path(key).parent / "README.md").replace("\\", "/")
        nav = texts.get(nav_key, "")
        rows = [
            line for line in nav.splitlines()
            if line.startswith("| ") and f"| {doc_id} |" in line
        ]
        wanted = "./" + Path(key).name
        if len(rows) != 1 or wanted not in rows[0]:
            error("navigation", nav_key, "Missing/duplicate navigation row for " + doc_id)
        elif field(content, "状态") not in rows[0]:
            error("navigation_status", nav_key, "Stale status for " + doc_id)

    graph: dict[str, set[str]] = {doc_id: set() for doc_id in core}
    for doc_id, (key, content) in core.items():
        source = (DOC_ROOT / key).resolve()
        for line in association(content).splitlines():
            matches = list(LINK.finditer(line))
            if matches and "关系：" not in line:
                error("relation_label", key, "Link lacks a relationship label.")
            for match in matches:
                target, _, issue = local_target(source, match.group(2))
                if not issue and target in paths_to_ids:
                    other = paths_to_ids[target]
                    if other != doc_id:
                        graph[doc_id].add(other)
        prefix = doc_id.split("-")[0]
        related_prefixes = {other.split("-")[0] for other in graph[doc_id]}
        if not related_prefixes.intersection(REQUIRED[prefix]):
            error("upstream", key, "Missing required upstream/object relationship.")
        if prefix == "BUG" and "TEST" not in related_prefixes:
            error("bug_chain", key, "Bug needs discovery/regression test relationship.")
        if prefix == "OPT" and "问题来源" in content and "TEST" not in related_prefixes:
            error("upstream", key, "Test-derived optimization lacks test link.")

    for doc_id, others in graph.items():
        if not others:
            error("orphan", core[doc_id][0], "No related core documents.")
        for other in others:
            if doc_id not in graph.get(other, set()):
                error("backlink", core[doc_id][0], f"{doc_id} -> {other} has no reverse link.")
    if graph:
        seen = set()
        queue = deque([next(iter(graph))])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(graph.get(node, set()) - seen)
        if len(seen) != len(graph):
            error("disconnected", "README.md", "Core graph is not connected.")

    snapshot_path = TOOL_ROOT / "source_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mapping = unfenced(texts.get("新旧文档对照.md", ""))
    for old_path in snapshot["old_docs"]:
        if f"](../{old_path})" not in mapping:
            error("mapping", "新旧文档对照.md", "Unmapped old document: " + old_path)
    if preserve:
        for key, expected_hash in snapshot["hashes"].items():
            target = (REPO_ROOT / key).resolve()
            if not target.is_relative_to(REPO_ROOT):
                error("source_scope", key, "Snapshot path escapes repository.")
                continue
            checked_sources += 1
            if not target.is_file():
                error("source_missing", key, "Original file was removed.")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
                error("source_changed", key, "Original file differs from pre-migration snapshot.")

    return {
        "status": "passed" if not errors else "failed",
        "markdown_files": len(texts),
        "core_documents": len(core),
        "core_by_prefix": dict(sorted(Counter(k.split("-")[0] for k in core).items())),
        "links_checked": link_count,
        "directed_core_relations": sum(map(len, graph.values())),
        "old_docs_mapped": len(snapshot["old_docs"]),
        "source_files_checked": checked_sources,
        "errors": errors,
    }


def self_test(texts: dict[str, str]) -> list[dict]:
    """Mutation checks in memory; never writes fixtures or edits actual documents."""
    results = []

    def run(name: str, modified: dict[str, str], expected: str) -> None:
        actual = validate(modified)
        found = {item["code"] for item in actual["errors"]}
        results.append({"case": name, "passed": expected in found, "expected_error": expected})

    sample = next(key for key in texts if Path(key).name.startswith("REQ-001-"))
    second = next(key for key in texts if Path(key).name.startswith("REQ-002-"))
    dev = next(key for key in texts if Path(key).name.startswith("DEV-001-"))
    run("missing_target", {**texts, sample: texts[sample] + "\n[bad](./missing.md)\n"}, "broken_link")
    run("missing_anchor", {**texts, sample: texts[sample] + "\n[bad](#no-such-anchor)\n"}, "broken_anchor")
    run("duplicate_id", {**texts, sample: texts[sample].replace("文档ID：REQ-001", "文档ID：REQ-002")}, "duplicate_id")
    run("missing_metadata", {**texts, sample: texts[sample].replace("- 创建日期：2026-08-27\n", "")}, "metadata")
    run("invalid_status", {**texts, sample: re.sub(r"^- 状态：.*$", "- 状态：任意状态", texts[sample], flags=re.M)}, "status")
    run("absolute_path", {**texts, sample: texts[sample] + "\n[bad](C:/private/readme.md)\n"}, "absolute_link")
    nav = "需求/README.md"
    rows = "\n".join(line for line in texts[nav].splitlines() if "| REQ-001 |" not in line)
    run("navigation_gap", {**texts, nav: rows}, "navigation")
    pattern = r"^## 关联文档\s*$\n.*?(?=^## |\Z)"
    no_links = re.sub(pattern, "## 关联文档\n\n暂无关联。\n\n", texts[dev], flags=re.M | re.S)
    run("missing_upstream", {**texts, dev: no_links}, "upstream")
    run("missing_reverse", {**texts, dev: no_links}, "backlink")
    run("missing_mapping", {**texts, "新旧文档对照.md": "# Empty mapping\n"}, "mapping")
    fence = chr(96) * 3
    example = fence + "markdown\n## 关联文档\n\n[example](./missing-example.md)\n" + fence
    with_example = texts[sample].replace("## 关联文档", example + "\n\n## 关联文档", 1)
    checked = validate({**texts, sample: with_example})
    results.append({"case": "fenced_example_ignored", "passed": checked["status"] == "passed", "expected_error": None})
    assert second in texts  # A second real ID is necessary for the duplicate-ID case.
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-preserved", action="store_true", help="Compare original files with migration snapshot")
    parser.add_argument("--self-test", action="store_true", help="Test failure detection in memory")
    parser.add_argument("--report", type=Path, help="Write JSON only inside new_docs/工具")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texts = read_texts()
    result = validate(texts, preserve=args.check_preserved)
    result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["scope"] = "Documentation static checks only; no product test or model run."
    if args.self_test:
        result["self_tests"] = self_test(texts)
        if not all(case["passed"] for case in result["self_tests"]):
            result["status"] = "failed"
            result["errors"].append({"code": "self_test", "file": "工具/check_docs.py", "message": "Failure detection self-test failed."})
    if args.report:
        report = args.report.resolve()
        if report.parent != TOOL_ROOT:
            parser.error("--report must be a file directly inside new_docs/工具")
        if report.name in {"source_snapshot.json", "check_docs.py", "README.md"} or report.suffix != ".json":
            parser.error("--report cannot overwrite source files or the protection snapshot")
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
