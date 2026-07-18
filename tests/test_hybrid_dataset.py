"""P2-1 dataset v2 冻结与证据唯一性测试。"""

import json
from pathlib import Path

from eval import adapters
from eval.eval import _validate_dataset


DATASET_DIR = Path(__file__).parents[1] / "eval" / "dataset"


def _load(name: str) -> dict:
    return json.loads((DATASET_DIR / name).read_text(encoding="utf-8"))


def test_questions_v2_preserves_v1_and_adds_frozen_p2_cases():
    v1 = _load("questions.json")
    v2 = _load("questions_v2.json")

    assert v2["version"] == 2
    assert v2["questions"][:21] == v1["questions"]
    assert [row["id"] for row in v2["questions"][21:]] == [
        "q22",
        "q23",
        "q24",
        "q25",
        "q26",
        "q27",
    ]
    assert all(
        len(evidence["snippet"]) <= 30
        for question in v2["questions"][21:]
        for evidence in question["evidence"]
    )


def test_questions_v2_snippets_are_unique_in_parsed_corpus():
    dataset = _load("questions_v2.json")
    corpus = Path(__file__).parents[1] / "eval" / "corpus"

    _validate_dataset(dataset, adapters.parse_corpus(corpus))
