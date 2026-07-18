"""
RAG 评估基准 CLI。

用法（在仓库根目录、用项目 venv 运行）：

    # 完整评估（约 17 次 LLM 调用）
    .venv/Scripts/python.exe -m eval.eval run --label baseline

    # 冒烟测试（只跑前 N 题）
    .venv/Scripts/python.exe -m eval.eval run --label smoke --limit 2

    # 对比两次运行
    .venv/Scripts/python.exe -m eval.eval compare eval/runs/baseline/report.json eval/runs/p0-1-bge/report.json

产物（eval/runs/<label>/）：
    raw_answers.jsonl  每题原始回答+检索结果（边跑边写，中断不丢数据）
    report.json        机器可读报告（compare 的输入）
    report.md          汇总报告 + 失败案例明细
    review.md          人工评审文档（问题/回答/标准答案/证据）

关键实现约束：环境变量（CHROMA_PERSIST_DIR / UPLOAD_DIR / EMBEDDING_MODEL …）
必须在任何 backend 模块 import 之前设置——backend 的 Chroma / Embedding / LLM
客户端都是模块级单例，import 即初始化。因此本文件顶层不 import backend，
adapters.py 中所有 backend import 都在函数内部。
需要 .env 中已配置 DEEPSEEK_API_KEY（config.py import 时校验）。
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent


def _setup_env(args) -> Path:
    """创建隔离运行目录并注入环境变量。必须先于任何 backend import 调用。"""
    run_dir = EVAL_DIR / "runs" / args.label
    chroma_dir = run_dir / "chroma"

    completed_outputs = (run_dir / "report.json", run_dir / "retrieval-sweep.json")
    if any(path.exists() for path in completed_outputs) and not args.overwrite:
        sys.exit(f"运行目录已存在报告：{run_dir}，如需覆盖请加 --overwrite")

    # Windows 下 Chroma 的 sqlite 文件锁：必须在 PersistentClient 创建前删除旧库
    if chroma_dir.exists():
        try:
            shutil.rmtree(chroma_dir)
        except PermissionError:
            sys.exit(
                f"无法删除旧向量库 {chroma_dir}（文件被占用）。"
                "请关闭其他正在使用该目录的进程后重试。"
            )
    run_dir.mkdir(parents=True, exist_ok=True)

    os.environ["CHROMA_PERSIST_DIR"] = str(chroma_dir)
    os.environ["UPLOAD_DIR"] = str(run_dir / "uploads")
    os.environ["METADATA_DB_PATH"] = str(run_dir / "metadata.db")
    if args.embedding_model:
        os.environ["EMBEDDING_MODEL"] = args.embedding_model
    if args.query_instruction is not None:
        os.environ["EMBEDDING_QUERY_INSTRUCTION"] = args.query_instruction
    if args.top_k:
        os.environ["RETRIEVAL_TOP_K"] = str(args.top_k)
    if getattr(args, "distance_threshold", None) is not None:
        os.environ["RETRIEVAL_DISTANCE_THRESHOLD"] = str(args.distance_threshold)
    if getattr(args, "reranker_enabled", None) is not None:
        os.environ["RERANKER_ENABLED"] = (
            "true" if args.reranker_enabled else "false"
        )
    if getattr(args, "reranker_model", None):
        os.environ["RERANKER_MODEL"] = args.reranker_model
    if getattr(args, "reranker_candidate_k", None) is not None:
        os.environ["RERANKER_CANDIDATE_K"] = str(args.reranker_candidate_k)
    if getattr(args, "chunk_strategy", None):
        os.environ["CHUNK_STRATEGY"] = args.chunk_strategy
    if getattr(args, "chunk_size", None) is not None:
        os.environ["CHUNK_SIZE"] = str(args.chunk_size)
    if getattr(args, "chunk_overlap", None) is not None:
        os.environ["CHUNK_OVERLAP"] = str(args.chunk_overlap)
    if getattr(args, "chunk_max_tokens", None) is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_max_tokens)
    return run_dir


def _load_dataset(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate_dataset(dataset: dict, corpus_texts: dict):
    """
    dataset 自检：每条 evidence snippet 必须在对应文档解析文本中至多出现一次。
    出现 0 次仅警告（说明当前 parser 未提取该证据，需结合实验阶段判断）；
    出现多次报错退出（命中判定会歧义）。
    """
    from eval.metrics import norm

    normed = {name: norm(text) for name, text in corpus_texts.items()}
    errors, warns = [], []
    for q in dataset["questions"]:
        for ev in q["evidence"]:
            doc = ev["document_name"]
            if doc not in normed:
                errors.append(f"{q['id']}: 语料中无此文档 {doc}")
                continue
            count = normed[doc].count(norm(ev["snippet"]))
            if count == 0:
                warns.append(f"{q['id']}: snippet 未出现于解析文本：{ev['snippet']}")
            elif count > 1:
                errors.append(f"{q['id']}: snippet 出现 {count} 次，须唯一：{ev['snippet']}")
    for w in warns:
        print(f"  [警告] {w}")
    if errors:
        for e in errors:
            print(f"  [错误] {e}")
        sys.exit("dataset 校验失败，请修正 questions.json")


def _format_progress_status(question: dict, row: dict) -> str:
    """Build a Windows GBK-safe progress label for one evaluated question."""
    if question["answerable"]:
        return "命中 PASS" if row.get("hit") else "命中 FAIL"
    return "拒答 PASS" if row.get("refusal_correct") else "拒答 FAIL"


def cmd_run(args):
    run_dir = _setup_env(args)

    # 环境变量已就位，现在才允许触发 backend import
    from eval import adapters
    from eval.metrics import score_question, aggregate

    try:
        adapters.assert_isolation()
    except Exception as e:
        sys.exit(str(e))

    corpus_dir = Path(args.corpus)
    dataset = _load_dataset(Path(args.dataset))
    questions = dataset["questions"]
    if args.limit:
        questions = questions[: args.limit]

    print(f"[1/4] 校验数据集（{len(questions)} 题）…")
    corpus_texts = adapters.parse_corpus(corpus_dir)
    _validate_dataset({"questions": questions}, corpus_texts)

    print(f"[2/4] 入库语料 → {os.environ['CHROMA_PERSIST_DIR']}")
    ingest_info = adapters.ingest_corpus(corpus_dir)
    for doc, n in ingest_info["documents"].items():
        print(f"  {doc}: {n} chunks")
    print(f"  共 {ingest_info['total_chunks']} chunks，耗时 {ingest_info['ingest_s']}s")

    print(f"[3/4] 逐题问答（{len(questions)} 题）…")
    answers, rows, timings = {}, [], []
    raw_path = run_dir / "raw_answers.jsonl"
    with open(raw_path, "w", encoding="utf-8") as raw_f:
        for i, q in enumerate(questions, 1):
            try:
                result = adapters.answer_question(q["question"])
            except Exception as e:
                sys.exit(f"  {q['id']} LLM 调用失败：{e}\n（已完成的原始结果在 {raw_path}）")
            answers[q["id"]] = result
            # 边跑边落盘，中断不丢已完成的题
            raw_f.write(
                json.dumps({"id": q["id"], **result}, ensure_ascii=False) + "\n"
            )
            raw_f.flush()
            row = score_question(q, result["answer"], result["retrieval"])
            rows.append(row)
            timings.append(
                {k: result[k] for k in ("retrieval_s", "llm_s", "total_s")}
            )
            status = _format_progress_status(q, row)
            print(f"  [{i}/{len(questions)}] {q['id']} {status} ({result['total_s']}s)")

    print("[4/4] 计算指标并生成报告…")
    agg = aggregate(rows, timings)
    from eval.report import (
        build_payload,
        write_report_json,
        write_report_md,
        write_review_md,
    )

    payload = build_payload(
        args.label,
        adapters.get_run_config(),
        ingest_info,
        rows,
        agg,
        dataset["version"],
    )
    write_report_json(run_dir, payload)
    write_report_md(run_dir, payload, questions, answers)
    write_review_md(run_dir, payload, questions, answers)

    print(f"\n===== {args.label} 汇总 =====")
    for key, value in agg["aggregate"].items():
        print(f"  {key}: {value}")
    print(f"\n报告：{run_dir}\\report.md")
    print(f"人工评审：{run_dir}\\review.md")


def cmd_compare(args):
    from eval.report import render_compare

    with open(args.report_a, encoding="utf-8") as f:
        a = json.load(f)
    with open(args.report_b, encoding="utf-8") as f:
        b = json.load(f)
    output = render_compare(a, b)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"对比报告已写入 {args.output}")
    else:
        print(output)


def cmd_threshold_sweep(args):
    """离线重放真实 retrieval，输出阈值候选的质量和门控对比。"""
    from eval.threshold import build_threshold_sweep, render_threshold_report

    dataset = _load_dataset(Path(args.dataset))
    answers = {}
    with open(args.raw_answers, encoding="utf-8") as raw_file:
        for line_number, line in enumerate(raw_file, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            question_id = row.get("id")
            if not question_id:
                sys.exit(f"raw_answers 第 {line_number} 行缺少 id")
            answers[question_id] = row

    missing = [
        question["id"]
        for question in dataset["questions"]
        if question["id"] not in answers
    ]
    if missing:
        sys.exit(f"raw_answers 缺少题目：{', '.join(missing)}")

    payload = build_threshold_sweep(
        label=args.label,
        questions=dataset["questions"],
        answers=answers,
        thresholds=args.thresholds,
        source=str(args.raw_answers),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "threshold-sweep.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(output_dir / "threshold-sweep.md", "w", encoding="utf-8") as f:
        f.write(render_threshold_report(payload))

    print(f"推荐阈值：{payload['recommended_threshold']:.2f}")
    print(f"报告：{output_dir / 'threshold-sweep.md'}")


def cmd_retrieval_sweep(args):
    """一次入库后比较多个 Reranker candidate_k，全程不调用 LLM。"""
    run_dir = _setup_env(args)

    from backend.config import settings
    from eval import adapters
    from eval.retrieval_sweep import (
        build_candidate_result,
        build_retrieval_sweep,
        render_retrieval_sweep,
    )

    adapters.assert_isolation()
    corpus_dir = Path(args.corpus)
    dataset = _load_dataset(Path(args.dataset))
    questions = dataset["questions"]

    print(f"[1/3] 校验数据集（{len(questions)} 题）…")
    corpus_texts = adapters.parse_corpus(corpus_dir)
    _validate_dataset(dataset, corpus_texts)

    print(f"[2/3] 入库语料 → {os.environ['CHROMA_PERSIST_DIR']}")
    ingest_info = adapters.ingest_corpus(corpus_dir)
    print(
        f"  共 {ingest_info['total_chunks']} chunks，"
        f"耗时 {ingest_info['ingest_s']}s"
    )

    print(f"[3/3] Reranker 候选数扫描：{args.candidate_ks}")
    original_enabled = settings.RERANKER_ENABLED
    original_candidate_k = settings.RERANKER_CANDIDATE_K
    try:
        settings.RERANKER_ENABLED = True
        candidate_results = []
        for candidate_k in args.candidate_ks:
            settings.RERANKER_CANDIDATE_K = candidate_k
            retrievals = {}
            timings = []
            raw_path = run_dir / f"retrieval-k{candidate_k}.jsonl"
            with open(raw_path, "w", encoding="utf-8") as raw_file:
                for index, question in enumerate(questions, 1):
                    result = adapters.retrieve_only(question["question"])
                    retrievals[question["id"]] = result["retrieval"]
                    timings.append(result["retrieval_s"])
                    raw_file.write(
                        json.dumps(
                            {"id": question["id"], **result},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    raw_file.flush()
                    print(
                        f"  [k={candidate_k}] [{index}/{len(questions)}] "
                        f"{question['id']} {result['retrieval_s']}s"
                    )
            candidate_results.append(
                build_candidate_result(
                    candidate_k=candidate_k,
                    questions=questions,
                    retrievals=retrievals,
                    timings_s=timings,
                )
            )

        config = adapters.get_run_config()
        config.pop("reranker_candidate_k", None)
        config["reranker_candidate_ks"] = list(args.candidate_ks)
        payload = build_retrieval_sweep(
            label=args.label,
            config=config,
            ingest=ingest_info,
            candidates=candidate_results,
            dataset_version=dataset["version"],
        )
        with open(run_dir / "retrieval-sweep.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with open(run_dir / "retrieval-sweep.md", "w", encoding="utf-8") as f:
            f.write(render_retrieval_sweep(payload))

        print(f"推荐候选数：{payload['recommended_candidate_k']}")
        print(f"报告：{run_dir / 'retrieval-sweep.md'}")
    finally:
        settings.RERANKER_ENABLED = original_enabled
        settings.RERANKER_CANDIDATE_K = original_candidate_k


def main():
    parser = argparse.ArgumentParser(description="RAG 评估基准")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行一次完整评估")
    p_run.add_argument("--label", required=True, help="运行标签，如 baseline / p0-1-bge")
    p_run.add_argument("--dataset", default=str(EVAL_DIR / "dataset" / "questions.json"))
    p_run.add_argument("--corpus", default=str(EVAL_DIR / "corpus"))
    p_run.add_argument("--limit", type=int, help="只跑前 N 题（冒烟测试）")
    p_run.add_argument("--overwrite", action="store_true", help="覆盖同名 label 的已有报告")
    p_run.add_argument("--embedding-model", help="覆盖 EMBEDDING_MODEL（需模型已在本地缓存）")
    p_run.add_argument(
        "--query-instruction",
        help="仅在 query 编码前添加的 instruction；传空字符串表示不添加",
    )
    p_run.add_argument("--top-k", type=int, help="覆盖 RETRIEVAL_TOP_K")
    p_run.add_argument(
        "--distance-threshold",
        type=float,
        help="覆盖 RETRIEVAL_DISTANCE_THRESHOLD（Chroma cosine distance）",
    )
    reranker_group = p_run.add_mutually_exclusive_group()
    reranker_group.add_argument(
        "--reranker",
        dest="reranker_enabled",
        action="store_true",
        default=None,
        help="启用 Cross-encoder Reranker",
    )
    reranker_group.add_argument(
        "--no-reranker",
        dest="reranker_enabled",
        action="store_false",
        help="显式关闭 Cross-encoder Reranker",
    )
    p_run.add_argument("--reranker-model", help="覆盖 RERANKER_MODEL")
    p_run.add_argument(
        "--reranker-candidate-k",
        type=int,
        help="覆盖 RERANKER_CANDIDATE_K",
    )
    p_run.add_argument(
        "--chunk-strategy",
        choices=("fixed", "recursive"),
        help="覆盖 CHUNK_STRATEGY；P1-3 候选使用 recursive",
    )
    p_run.add_argument("--chunk-size", type=int, help="覆盖目标 chunk 字符数")
    p_run.add_argument("--chunk-overlap", type=int, help="覆盖相邻 chunk 重叠字符数")
    p_run.add_argument(
        "--chunk-max-tokens",
        type=int,
        help="覆盖 embedding tokenizer 的硬 token 上限",
    )
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="对比两份 report.json")
    p_cmp.add_argument("report_a")
    p_cmp.add_argument("report_b")
    p_cmp.add_argument("-o", "--output", help="输出 md 文件路径（缺省打印到终端）")
    p_cmp.set_defaults(func=cmd_compare)

    p_threshold = sub.add_parser(
        "threshold-sweep",
        help="基于已有 raw_answers 离线扫描检索距离阈值",
    )
    p_threshold.add_argument("raw_answers", help="真实评测生成的 raw_answers.jsonl")
    p_threshold.add_argument("--label", required=True, help="阈值扫描标签")
    p_threshold.add_argument(
        "--dataset",
        default=str(EVAL_DIR / "dataset" / "questions.json"),
    )
    p_threshold.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        required=True,
        help="候选 distance 阈值列表，例如 0.60 0.61 0.62",
    )
    p_threshold.add_argument("--output-dir", required=True, help="扫描报告输出目录")
    p_threshold.set_defaults(func=cmd_threshold_sweep)

    p_retrieval = sub.add_parser(
        "retrieval-sweep",
        help="一次入库后离线比较多个 Reranker 候选数（不调用 LLM）",
    )
    p_retrieval.add_argument("--label", required=True, help="实验标签")
    p_retrieval.add_argument(
        "--dataset", default=str(EVAL_DIR / "dataset" / "questions.json")
    )
    p_retrieval.add_argument("--corpus", default=str(EVAL_DIR / "corpus"))
    p_retrieval.add_argument(
        "--candidate-ks",
        nargs="+",
        type=int,
        required=True,
        help="Reranker 候选数，例如 15 20 30",
    )
    p_retrieval.add_argument("--overwrite", action="store_true")
    p_retrieval.add_argument("--embedding-model")
    p_retrieval.add_argument("--query-instruction")
    p_retrieval.add_argument("--top-k", type=int)
    p_retrieval.add_argument("--distance-threshold", type=float)
    p_retrieval.add_argument("--reranker-model")
    p_retrieval.add_argument("--chunk-strategy", choices=("fixed", "recursive"))
    p_retrieval.add_argument("--chunk-size", type=int)
    p_retrieval.add_argument("--chunk-overlap", type=int)
    p_retrieval.add_argument("--chunk-max-tokens", type=int)
    p_retrieval.set_defaults(
        func=cmd_retrieval_sweep,
        reranker_enabled=True,
        reranker_candidate_k=None,
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
