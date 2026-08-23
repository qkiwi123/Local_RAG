"""验证 Embedding/Reranker 的真实 GPU 推理并保存可审计报告。

本脚本不导入 LLM、Chroma 或 metadata store，也不调用任何外部 API。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
DEFAULT_OUTPUT = ROOT / "runs" / "gpu-runtime-smoke-v1" / "gpu_runtime.json"


def _configure_environment(args: argparse.Namespace) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["EMBEDDING_DEVICE"] = args.device
    os.environ["EMBEDDING_BATCH_SIZE"] = str(args.embedding_batch_size)
    os.environ["RERANKER_DEVICE"] = args.device
    os.environ["RERANKER_USE_FP16"] = "true" if args.reranker_fp16 else "false"
    os.environ["RERANKER_BATCH_SIZE"] = str(args.reranker_batch_size)


def run_smoke(args: argparse.Namespace) -> dict:
    _configure_environment(args)
    sys.path.insert(0, str(PROJECT_ROOT))

    import psutil
    import torch

    from backend.services import embedding, reranker

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用，停止 GPU 冒烟；不会回退 CPU")
    torch.cuda.reset_peak_memory_stats()

    question = "什么是检索增强生成？"
    start = time.perf_counter()
    embedding_start = time.perf_counter()
    vector = embedding.embed_query(question)
    torch.cuda.synchronize()
    embedding_seconds = time.perf_counter() - embedding_start
    embedding_model = embedding._get_model()
    embedding_device = str(embedding_model.device)
    embedding_peak_mib = torch.cuda.max_memory_allocated() / 1024 / 1024

    passages = [
        "检索增强生成结合外部知识库与语言模型。",
        "向量数据库用于语义相似度检索。",
        "重排序模型对候选内容重新评分。",
        "员工每年享有带薪年假。",
        "今天天气晴朗。",
        "RAG 可以利用外部文档补充模型知识。",
        "这是一条无关文本。",
        "文档切分后生成向量。",
        "BM25 是一种词项检索方法。",
        "混合检索融合稠密与稀疏结果。",
        "引用可以追溯答案来源。",
        "数据库事务保证一致性。",
        "召回率衡量相关结果覆盖程度。",
        "精确率衡量返回结果质量。",
        "语言模型根据检索上下文生成最终回答。",
    ]
    candidates = [
        {"chunk_id": str(index), "content": content, "metadata": {}}
        for index, content in enumerate(passages)
    ]
    reranker_start = time.perf_counter()
    ranked = reranker.rerank(question, candidates)
    torch.cuda.synchronize()
    reranker_seconds = time.perf_counter() - reranker_start
    reranker_model = reranker._get_model()
    reranker_parameter = next(reranker_model.model.parameters())

    process = psutil.Process(os.getpid())
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": {
            "llm_called": False,
            "vector_database_opened": False,
            "network_models_downloaded": False,
        },
        "environment": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device": torch.cuda.get_device_name(0),
        },
        "configuration": {
            "device": args.device,
            "embedding_batch_size": args.embedding_batch_size,
            "reranker_batch_size": args.reranker_batch_size,
            "reranker_fp16": args.reranker_fp16,
            "candidate_count": len(candidates),
        },
        "actual": {
            "embedding_device": embedding_device,
            "embedding_dimension": len(vector),
            "reranker_device": str(reranker_parameter.device),
            "reranker_dtype": str(reranker_parameter.dtype),
            "top_chunk_id": ranked[0]["chunk_id"],
        },
        "timing_seconds": {
            "embedding_cold": round(embedding_seconds, 3),
            "reranker_cold": round(reranker_seconds, 3),
            "combined": round(time.perf_counter() - start, 3),
        },
        "resources": {
            "embedding_peak_allocated_mib": round(embedding_peak_mib, 1),
            "combined_allocated_mib": round(
                torch.cuda.memory_allocated() / 1024 / 1024, 1
            ),
            "combined_reserved_mib": round(
                torch.cuda.memory_reserved() / 1024 / 1024, 1
            ),
            "combined_peak_allocated_mib": round(
                torch.cuda.max_memory_allocated() / 1024 / 1024, 1
            ),
            "process_rss_mib": round(process.memory_info().rss / 1024 / 1024, 1),
        },
        "passed": (
            embedding_device == args.device
            and str(reranker_parameter.device) == args.device
            and (not args.reranker_fp16 or reranker_parameter.dtype == torch.float16)
            and len(vector) == 1024
            and ranked[0]["chunk_id"] == "0"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--reranker-batch-size", type=int, default=8)
    parser.add_argument(
        "--reranker-fp16",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        parser.error(f"输出已存在：{output}；如需重跑请加 --overwrite")
    report = run_smoke(args)
    if not report["passed"]:
        raise RuntimeError("GPU 冒烟结果未通过，请检查输出中的实际设备和精度")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
