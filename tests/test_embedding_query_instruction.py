"""查询侧 instruction 与评测配置测试。"""

import os
from types import SimpleNamespace

from backend.config import settings
from backend.services import embedding
from eval import adapters
from eval import eval as eval_cli


class _Encoded:
    def __init__(self, rows):
        self._rows = rows

    def tolist(self):
        return self._rows


class _RecordingModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        return _Encoded([[0.1, 0.2] for _ in texts])


def test_p1_selected_embedding_defaults():
    assert settings.EMBEDDING_MODEL == "BAAI/bge-large-zh-v1.5"
    assert settings.EMBEDDING_QUERY_INSTRUCTION == ""


def test_query_instruction_is_applied_only_to_queries(monkeypatch):
    model = _RecordingModel()
    instruction = "为这个句子生成表示以用于检索相关文章："
    monkeypatch.setattr(embedding, "_get_model", lambda: model)
    monkeypatch.setattr(settings, "EMBEDDING_QUERY_INSTRUCTION", instruction, raising=False)

    embedding.embed_texts(["文档正文"])
    embedding.embed_query("什么是 RAG？")

    assert model.calls == [["文档正文"], [f"{instruction}什么是 RAG？"]]


def test_eval_cli_sets_and_records_query_instruction(monkeypatch, tmp_path):
    instruction = "为这个句子生成表示以用于检索相关文章："
    monkeypatch.setattr(eval_cli, "EVAL_DIR", tmp_path)
    for name in (
        "CHROMA_PERSIST_DIR",
        "UPLOAD_DIR",
        "METADATA_DB_PATH",
        "EMBEDDING_QUERY_INSTRUCTION",
    ):
        monkeypatch.setenv(name, f"before-{name.lower()}")
    args = SimpleNamespace(
        label="instruction-test",
        overwrite=False,
        embedding_model=None,
        query_instruction=instruction,
        top_k=None,
    )

    eval_cli._setup_env(args)
    assert os.environ["EMBEDDING_QUERY_INSTRUCTION"] == instruction

    monkeypatch.setattr(settings, "EMBEDDING_QUERY_INSTRUCTION", instruction, raising=False)
    assert adapters.get_run_config()["embedding_query_instruction"] == instruction
