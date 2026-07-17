from types import SimpleNamespace

from frontend.http_utils import (
    pending_uploads,
    response_error_detail,
    upload_attempts,
    upload_selection_complete,
)


class FakeResponse:
    def __init__(self, status_code, text, payload_error=None, payload=None):
        self.status_code = status_code
        self.text = text
        self._payload_error = payload_error
        self._payload = payload

    def json(self):
        if self._payload_error:
            raise self._payload_error
        return self._payload


def _file(name: str, content: bytes):
    return SimpleNamespace(name=name, getvalue=lambda: content)


def test_response_error_detail_accepts_json_detail():
    response = FakeResponse(500, "", payload={"detail": "文档解析失败"})

    assert response_error_detail(response) == "文档解析失败"


def test_response_error_detail_handles_empty_non_json_response():
    response = FakeResponse(502, "", payload_error=ValueError("empty body"))

    assert response_error_detail(response) == "HTTP 502（后端未返回错误详情）"


def test_failed_upload_remains_pending_until_marked_successful():
    completed = set()
    uploaded_file = _file("same.docx", b"content")

    first_attempt = pending_uploads([uploaded_file], completed)
    retry_attempt = pending_uploads([uploaded_file], completed)

    assert len(first_attempt) == 1
    assert len(retry_attempt) == 1

    completed.add(first_attempt[0][0])
    assert pending_uploads([uploaded_file], completed) == []


def test_removing_file_allows_same_content_to_be_selected_again():
    uploaded_file = _file("same.docx", b"content")
    completed = {pending_uploads([uploaded_file], set())[0][0]}

    assert pending_uploads([], completed) == []
    assert completed == set()
    assert len(pending_uploads([uploaded_file], completed)) == 1


def test_failed_upload_is_only_attempted_after_explicit_retry():
    first = ("failed-key", _file("failed.docx", b"failed"))
    second = ("new-key", _file("new.docx", b"new"))

    assert upload_attempts([first, second], {"failed-key"}, retry_failed=False) == [
        second
    ]
    assert upload_attempts([first, second], {"failed-key"}, retry_failed=True) == [
        second,
        first,
    ]


def test_upload_selection_is_complete_only_when_every_file_succeeded():
    first = _file("first.docx", b"first")
    second = _file("second.pdf", b"second")
    first_key = pending_uploads([first], set())[0][0]
    second_key = pending_uploads([second], set())[0][0]

    assert not upload_selection_complete([], {first_key, second_key})
    assert not upload_selection_complete([first, second], {first_key})
    assert upload_selection_complete([first, second], {first_key, second_key})
