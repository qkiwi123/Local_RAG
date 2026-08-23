from eval.crud_rag_1000.v3.run_retrieval_baseline import build_eval_argv


def _flag_value(arguments: list[str], flag: str) -> str:
    return arguments[arguments.index(flag) + 1]


def test_cpu_and_gpu_baselines_share_retrieval_parameters():
    cpu = build_eval_argv(label="cpu-baseline", profile="cpu", overwrite=False)
    gpu = build_eval_argv(label="gpu-baseline", profile="gpu", overwrite=False)

    fixed_value_flags = (
        "--dataset",
        "--corpus",
        "--manifest",
        "--candidate-ks",
        "--embedding-model",
        "--embedding-batch-size",
        "--query-instruction",
        "--top-k",
        "--retrieval-mode",
        "--distance-threshold",
        "--bm25-top-k",
        "--bm25-k1",
        "--bm25-b",
        "--rrf-k",
        "--reranker-model",
        "--reranker-batch-size",
        "--chunk-strategy",
        "--chunk-size",
        "--chunk-overlap",
        "--chunk-max-tokens",
    )
    for flag in fixed_value_flags:
        assert _flag_value(cpu, flag) == _flag_value(gpu, flag)

    assert _flag_value(cpu, "--embedding-device") == "cpu"
    assert _flag_value(cpu, "--reranker-device") == "cpu"
    assert "--no-reranker-fp16" in cpu
    assert _flag_value(gpu, "--embedding-device") == "cuda:0"
    assert _flag_value(gpu, "--reranker-device") == "cuda:0"
    assert "--reranker-fp16" in gpu


def test_baseline_is_offline_retrieval_only_and_isolated_to_v3_fixture():
    arguments = build_eval_argv(
        label="baseline",
        profile="gpu",
        overwrite=True,
    )

    assert arguments[0] == "retrieval-sweep"
    assert "--allow-draft-v3" in arguments
    assert "--retrieval-trace" in arguments
    assert "--overwrite" in arguments
    assert "questions_v3_120q.json" in _flag_value(arguments, "--dataset")
    assert _flag_value(arguments, "--candidate-ks") == "15"
    assert not any("llm" in argument.lower() for argument in arguments)
