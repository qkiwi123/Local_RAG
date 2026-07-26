"""把不同版本的黄金集转换为评测运行时的统一单题视图。"""

from __future__ import annotations

import copy
from typing import Any


def is_dataset_v3(dataset: dict[str, Any]) -> bool:
    return dataset.get("schema_version") == 3


def dataset_version_label(dataset: dict[str, Any]) -> int | str:
    """返回可写入报告、用于跨 run 识别数据集的稳定标签。"""
    if is_dataset_v3(dataset):
        return str(dataset["dataset_id"])
    return dataset["version"]


def normalize_for_evaluation(dataset: dict[str, Any]) -> dict[str, Any]:
    """将 v3 dialogue turn 展平成无状态检索/回答可消费的题目。

    v3 的多轮正式评测还需要独立验证 contextualizer；当前通用评测入口使用
    gold ``standalone_question`` 作为检索问题，并保留原始追问供报告展示。
    """
    if not is_dataset_v3(dataset):
        return dataset

    questions = [copy.deepcopy(question) for question in dataset["questions"]]
    for dialogue in dataset["dialogues"]:
        for turn in dialogue["turns"]:
            question = copy.deepcopy(turn)
            question["id"] = question.pop("turn_id")
            question["original_question"] = question["question"]
            question["question"] = question["standalone_question"]
            question["category"] = "dialogue"
            question["difficulty"] = dialogue["difficulty"]
            question["document_types"] = copy.deepcopy(
                dialogue["document_types"]
            )
            question["dialogue_id"] = dialogue["dialogue_id"]
            question["question_group"] = dialogue["question_group"]
            question["split"] = dialogue["split"]
            questions.append(question)

    return {
        **{
            key: copy.deepcopy(value)
            for key, value in dataset.items()
            if key not in {"questions", "dialogues"}
        },
        "version": dataset_version_label(dataset),
        "questions": questions,
        "dialogues": copy.deepcopy(dataset["dialogues"]),
        "runtime_notes": {
            "dialogue_query_mode": "gold_standalone_question",
        },
    }
