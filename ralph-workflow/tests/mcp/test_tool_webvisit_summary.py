"""Phase 4: tests for ``format='metadata'`` in ``handle_visit_url`` and
``format='summary'`` in ``handle_download_url``.

Tests are colocated under ``tests/mcp/`` to match the existing web
test layout (``test_tool_webvisit.py``). They stub the fetch layer
and the readability extractor so the handlers run end-to-end without
any real network call.
"""

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

from tests.mcp.test_tool_webvisit_helper__stubworkspace import _StubWorkspace


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _Workspace:
    """Minimal Workspace double for download tests.

    Implements only the ``absolute_path`` and ``write`` methods the
    download handler actually touches. ``explore_index`` is set to
    ``None`` so the structural Workspace protocol stays satisfied.
    """

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


# =============================================================================
# visit_url format=metadata
# =============================================================================


def test_format_metadata_drops_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(
        tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE
    )

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
    # Full text body must NOT be echoed in metadata mode.
    assert "text" not in payload
    assert payload["byte_count"] == len(_GOOD_PAGE.text)
    assert len(payload["head_preview"]) <= tool_webvisit._VISIT_HEAD_PREVIEW_CHARS
    assert payload["bytes_in"] == len(_GOOD_OUTCOME.body or b"")
    assert payload["bytes_out"] > 0


def test_format_metadata_includes_links_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(
        tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE
    )

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
    """``format='raw'`` (default) preserves the legacy full text body."""
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(
        tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE
    )

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


# =============================================================================
# download_url format=summary
# =============================================================================


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
    # The body itself must NOT be echoed inline; only the head preview.
    assert "head_preview" in payload
    assert payload["truncated"] is True
    assert payload["bytes_in"] == len(body)
    # The file must still be persisted to ``output_path``.
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
    """``format='raw'`` (default) preserves the legacy metadata-only envelope."""
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
    # sha256 + head_preview must NOT be present in raw mode.
    assert "sha256" not in payload
    assert "head_preview" not in payload
