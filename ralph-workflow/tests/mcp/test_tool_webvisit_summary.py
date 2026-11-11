from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.mcp_models import WebVisitConfig
from ralph.mcp.tools import webvisit as tool_webvisit
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.mcp.webvisit.fetcher import FetchOutcome

if TYPE_CHECKING:
    import pytest

from tests.mcp._tool_webvisit_helper__stubworkspace import _StubWorkspace


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _Workspace:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.explore_index: object | None = None

    def absolute_path(self, path: str) -> str:
        return path

    def write(self, path: str, content: str) -> None:
        self.writes.append((path, content))


_GOOD_OUTCOME = FetchOutcome(
    status="ok",
    effective_url="https://example.com/page",
    http_status=200,
    content_type="text/html; charset=utf-8",
    body=b"<html><body>" + b"<p>x</p>" * 200 + b"</body></html>",
)

_GOOD_PAGE = ExtractedPage(
    title="Hello",
    text="Hello world\n" + ("A" * 1000),
    links=("https://example.com/link",),
)


def _make_config(**overrides: object) -> WebVisitConfig:
    data: dict[str, object] = {
        "enabled": True,
        "timeout_ms": 15000,
        "max_bytes": 2_097_152,
        "user_agent": "RalphWorkflow/1.0",
        "allow_private_networks": False,
        "extract_links": False,
    }
    data.update(overrides)
    return WebVisitConfig.model_validate(data)


def test_format_metadata_drops_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page", "format": "metadata"},
        web_visit_config=_make_config(),
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["format"] == "metadata"
    assert payload["status"] == "ok"
    assert payload["title"] == "Hello"
    assert payload["effective_url"] == "https://example.com/page"
    assert payload["content_type"] == "text/html; charset=utf-8"
    assert "text" not in payload
    assert payload["byte_count"] == len(_GOOD_PAGE.text)
    assert len(payload["head_preview"]) <= tool_webvisit._VISIT_HEAD_PREVIEW_CHARS
    assert payload["bytes_in"] == len(_GOOD_OUTCOME.body or b"")
    assert payload["bytes_out"] > 0


def test_format_metadata_includes_links_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {
            "url": "https://example.com/page",
            "format": "metadata",
            "with_links": True,
        },
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["links"] == list(_GOOD_PAGE.links)[:10]


def test_format_raw_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "ok"
    assert "text" in payload
    assert "Hello world" in payload["text"]


def test_format_invalid_value_returns_error() -> None:
    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page", "format": "bogus"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    assert "Invalid visit_url format" in result.content[0].text


def test_format_summary_download_returns_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (b"alpha " * 200) + b"final"
    outcome = FetchOutcome(
        status="ok",
        effective_url="https://example.com/data.bin",
        http_status=200,
        content_type="application/octet-stream",
        body=body,
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: outcome)

    ws = _Workspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        ws,
        {
            "url": "https://example.com/data.bin",
            "output_path": "tmp/data.bin",
            "format": "summary",
        },
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["format"] == "summary"
    assert payload["status"] == "ok"
    assert payload["effective_url"] == "https://example.com/data.bin"
    assert payload["content_type"] == "application/octet-stream"
    assert payload["output_path"] == "tmp/data.bin"
    assert payload["bytes_written"] == len(body)
    assert payload["sha256"] != ""
    assert len(payload["sha256"]) == 16
    assert "head_preview" in payload
    assert payload["truncated"] is True
    assert payload["bytes_in"] == len(body)
    assert ws.writes == [("tmp/data.bin", body.decode("utf-8", errors="replace"))]


def test_format_summary_invalid_value_returns_error() -> None:
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        MagicMock(),
        {
            "url": "https://example.com/data.bin",
            "output_path": "tmp/data.bin",
            "format": "bogus",
        },
        web_visit_config=_make_config(),
    )
    assert result.is_error is True
    assert "Invalid download_url format" in result.content[0].text


def test_format_summary_raw_default_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"some content"
    outcome = FetchOutcome(
        status="ok",
        effective_url="https://example.com/data.bin",
        http_status=200,
        content_type="application/octet-stream",
        body=body,
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: outcome)

    ws = _Workspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        ws,
        {
            "url": "https://example.com/data.bin",
            "output_path": "tmp/data.bin",
        },
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert "format" not in payload
    assert payload["status"] == "ok"
    assert payload["bytes_written"] == len(body)
    assert "sha256" not in payload
    assert "head_preview" not in payload
