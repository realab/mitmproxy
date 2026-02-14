import json

from mitmproxy.contentviews._api import Contentview
from mitmproxy.contentviews._api import Metadata
from mitmproxy.http import HTTPFlow
from mitmproxy.http import Request


def _format_request_messages(data: bytes) -> str:
    """Format a chat completions request body showing the conversation."""
    body = json.loads(data)
    messages = body.get("messages", [])
    if not messages:
        raise ValueError("No messages in request body.")
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _parse_streaming_choices(data: bytes) -> dict[int, str]:
    """Parse SSE data and reassemble delta content by choice index."""
    choices: dict[int, str] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            index = choice.get("index", 0)
            content = choice.get("delta", {}).get("content")
            if content is not None:
                choices.setdefault(index, "")
                choices[index] += content
    return choices


def _parse_json_choices(data: bytes) -> dict[int, str]:
    """Parse a JSON response and extract message content by choice index."""
    body = json.loads(data)
    choices: dict[int, str] = {}
    for choice in body.get("choices", []):
        index = choice.get("index", 0)
        content = choice.get("message", {}).get("content")
        if content is not None:
            choices[index] = content
    return choices


def _format_choices(choices: dict[int, str]) -> str:
    if not choices:
        return ""
    if len(choices) == 1:
        return next(iter(choices.values()))
    parts = []
    for index in sorted(choices):
        parts.append(f"--- Choice {index} ---\n{choices[index]}")
    return "\n\n".join(parts)


def _is_request(metadata: Metadata) -> bool:
    return isinstance(metadata.http_message, Request)


class ChatCompletionsContentview(Contentview):
    name = "Chat Completions"

    @property
    def syntax_highlight(self):
        return "yaml"

    def prettify(
        self,
        data: bytes,
        metadata: Metadata,
    ) -> str:
        if _is_request(metadata):
            return _format_request_messages(data)
        if metadata.content_type == "text/event-stream":
            choices = _parse_streaming_choices(data)
        else:
            choices = _parse_json_choices(data)
        return _format_choices(choices)

    def render_priority(
        self,
        data: bytes,
        metadata: Metadata,
    ) -> float:
        if not data:
            return 0
        if not isinstance(metadata.flow, HTTPFlow):
            return 0
        if not metadata.flow.request.path.endswith("/chat/completions"):
            return 0
        if _is_request(metadata):
            return 2 if metadata.content_type == "application/json" else 0
        if metadata.content_type not in ("application/json", "text/event-stream"):
            return 0
        return 2


chat_completions = ChatCompletionsContentview()
