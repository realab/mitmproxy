import json

import pytest

from mitmproxy.contentviews import Metadata
from mitmproxy.contentviews._view_chat_completions import chat_completions
from mitmproxy.test import tflow


def _make_metadata(
    content_type="application/json",
    path="/v1/chat/completions",
    is_request=False,
):
    flow = tflow.tflow(resp=True)
    flow.request.path = path
    http_message = flow.request if is_request else flow.response
    return Metadata(flow=flow, content_type=content_type, http_message=http_message)


def test_syntax_highlight():
    assert chat_completions.syntax_highlight == "yaml"


class TestRenderPriority:
    def test_matching_json_response(self):
        metadata = _make_metadata(content_type="application/json")
        assert chat_completions.render_priority(b'{"choices":[]}', metadata) == 2

    def test_matching_json_request(self):
        metadata = _make_metadata(content_type="application/json", is_request=True)
        assert chat_completions.render_priority(b'{"messages":[]}', metadata) == 2

    def test_matching_sse(self):
        metadata = _make_metadata(content_type="text/event-stream")
        assert chat_completions.render_priority(b"data: {}\n", metadata) == 2

    def test_request_wrong_content_type(self):
        metadata = _make_metadata(content_type="text/event-stream", is_request=True)
        assert chat_completions.render_priority(b"data", metadata) == 0

    def test_empty_data(self):
        metadata = _make_metadata()
        assert chat_completions.render_priority(b"", metadata) == 0

    def test_wrong_path(self):
        metadata = _make_metadata(path="/v1/models")
        assert chat_completions.render_priority(b'{"choices":[]}', metadata) == 0

    def test_wrong_content_type(self):
        metadata = _make_metadata(content_type="text/html")
        assert chat_completions.render_priority(b"<html>", metadata) == 0

    def test_no_flow(self):
        metadata = Metadata(content_type="application/json")
        assert chat_completions.render_priority(b'{"choices":[]}', metadata) == 0

    def test_path_suffix(self):
        metadata = _make_metadata(path="/api/openai/chat/completions")
        assert chat_completions.render_priority(b'{"choices":[]}', metadata) == 2


class TestPrettifyRequest:
    def test_single_message(self):
        body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello!"}],
        }
        metadata = _make_metadata(is_request=True)
        result = chat_completions.prettify(json.dumps(body).encode(), metadata)
        assert "[user]" in result
        assert "Hello!" in result

    def test_conversation(self):
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "Bye"},
            ]
        }
        metadata = _make_metadata(is_request=True)
        result = chat_completions.prettify(json.dumps(body).encode(), metadata)
        assert "[system]\nYou are helpful." in result
        assert "[user]\nHi" in result
        assert "[assistant]\nHello!" in result
        assert "[user]\nBye" in result

    def test_no_messages(self):
        body = {"model": "gpt-4"}
        metadata = _make_metadata(is_request=True)
        with pytest.raises(ValueError):
            chat_completions.prettify(json.dumps(body).encode(), metadata)

    def test_empty_messages(self):
        body = {"messages": []}
        metadata = _make_metadata(is_request=True)
        with pytest.raises(ValueError):
            chat_completions.prettify(json.dumps(body).encode(), metadata)


class TestPrettifyJSON:
    def test_single_choice(self):
        body = {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Hello!"}}
            ]
        }
        result = chat_completions.prettify(json.dumps(body).encode(), _make_metadata())
        assert result == "Hello!"

    def test_multiple_choices(self):
        body = {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Answer A"}},
                {"index": 1, "message": {"role": "assistant", "content": "Answer B"}},
            ]
        }
        result = chat_completions.prettify(json.dumps(body).encode(), _make_metadata())
        assert "--- Choice 0 ---" in result
        assert "Answer A" in result
        assert "--- Choice 1 ---" in result
        assert "Answer B" in result

    def test_empty_choices(self):
        body = {"choices": []}
        result = chat_completions.prettify(json.dumps(body).encode(), _make_metadata())
        assert result == ""

    def test_no_content(self):
        body = {"choices": [{"index": 0, "message": {"role": "assistant"}}]}
        result = chat_completions.prettify(json.dumps(body).encode(), _make_metadata())
        assert result == ""

    def test_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            chat_completions.prettify(b"not json", _make_metadata())


class TestPrettifySSE:
    def test_streaming(self):
        lines = [
            'data: {"choices":[{"index":0,"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"!"}}]}',
            "data: [DONE]",
        ]
        data = "\n".join(lines).encode()
        metadata = _make_metadata(content_type="text/event-stream")
        result = chat_completions.prettify(data, metadata)
        assert result == "Hello!"

    def test_streaming_multiple_choices(self):
        lines = [
            'data: {"choices":[{"index":0,"delta":{"content":"A"}},{"index":1,"delta":{"content":"X"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"B"}},{"index":1,"delta":{"content":"Y"}}]}',
            "data: [DONE]",
        ]
        data = "\n".join(lines).encode()
        metadata = _make_metadata(content_type="text/event-stream")
        result = chat_completions.prettify(data, metadata)
        assert "--- Choice 0 ---" in result
        assert "AB" in result
        assert "--- Choice 1 ---" in result
        assert "XY" in result

    def test_empty_and_done_lines(self):
        lines = [
            "",
            'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}',
            "data: [DONE]",
            "",
        ]
        data = "\n".join(lines).encode()
        metadata = _make_metadata(content_type="text/event-stream")
        result = chat_completions.prettify(data, metadata)
        assert result == "Hi"

    def test_malformed_json_in_sse(self):
        lines = [
            "data: {bad json}",
            'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]
        data = "\n".join(lines).encode()
        metadata = _make_metadata(content_type="text/event-stream")
        result = chat_completions.prettify(data, metadata)
        assert result == "ok"

    def test_empty_sse(self):
        metadata = _make_metadata(content_type="text/event-stream")
        result = chat_completions.prettify(b"data: [DONE]\n", metadata)
        assert result == ""
