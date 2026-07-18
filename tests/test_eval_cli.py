import pytest


@pytest.mark.parametrize(
    ("question", "row", "expected"),
    [
        ({"answerable": True}, {"hit": True}, "命中 PASS"),
        ({"answerable": True}, {"hit": False}, "命中 FAIL"),
        ({"answerable": False}, {"refusal_correct": True}, "拒答 PASS"),
        ({"answerable": False}, {"refusal_correct": False}, "拒答 FAIL"),
    ],
)
def test_progress_status_is_safe_for_windows_gbk_console(question, row, expected):
    from eval.eval import _format_progress_status

    status = _format_progress_status(question, row)

    assert status == expected
    status.encode("gbk")
