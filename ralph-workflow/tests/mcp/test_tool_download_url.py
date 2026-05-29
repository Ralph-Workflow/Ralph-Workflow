from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.config.mcp_models import WebVisitConfig
from ralph.mcp.tools import webvisit as tool_webvisit
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.webvisit.fetcher import FetchOutcome
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    import pytest

_HTTP_200 = 200


class _AllowedSession:
    session_id = "test-session-dl"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _DeniedSession:
    session_id = "denied-session-dl"

    def check_capability(self, capability: str) -> object:
        return "denied"


_GOOD_OUTCOME = FetchOutcome(
    status="ok",
    effective_url="https://example.com/data.json",
    http_status=_HTTP_200,
    content_type="application/json; charset=utf-8",
    body=b'{"key": "value"}',
)


_TEXT_OUTCOME = FetchOutcome(
    status="ok",
    effective_url="https://example.com/page.html",
    http_status=_HTTP_200,
    content_type="text/html; charset=utf-8",
    body=b"<html><body><p>Hello world</p></body></html>",
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


def test_download_url_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)

    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"url": "https://example.com/data.json", "output_path": "downloads/data.json"},
        web_visit_config=_make_config(),
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["status"] == "ok"
    assert data["output_path"] == "downloads/data.json"
    assert data["bytes_written"] == len(b'{"key": "value"}')
    assert workspace.read("downloads/data.json") == '{"key": "value"}'


def test_download_url_capability_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)

    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _DeniedSession(),
        workspace,
        {"url": "https://example.com/data.json", "output_path": "downloads/data.json"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True


def test_download_url_missing_url_param() -> None:
    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"output_path": "downloads/data.json"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True


def test_download_url_missing_output_path_param() -> None:
    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"url": "https://example.com/data.json"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True


def test_download_url_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error_outcome = FetchOutcome(status="timeout", error="request timed out")
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: error_outcome)

    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"url": "https://example.com/data.json", "output_path": "downloads/data.json"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "timeout"


def test_download_url_creates_parent_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)

    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"url": "https://example.com/data.json", "output_path": "a/b/c/data.json"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    assert workspace.read("a/b/c/data.json") == '{"key": "value"}'


def test_download_url_content_type_in_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _TEXT_OUTCOME)

    workspace = MemoryWorkspace()
    result = tool_webvisit.handle_download_url(
        _AllowedSession(),
        workspace,
        {"url": "https://example.com/page.html", "output_path": "downloads/page.html"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["content_type"] == "text/html; charset=utf-8"
    assert workspace.read("downloads/page.html") == "<html><body><p>Hello world</p></body></html>"


def test_download_url_url_not_in_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    from loguru import logger

    secret_url = "https://internal-secret-dl-98765.example.com/private"
    timeout_outcome = FetchOutcome(status="timeout", error="timed out")
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: timeout_outcome)

    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="WARNING")
    try:
        tool_webvisit.handle_download_url(
            _AllowedSession(),
            MemoryWorkspace(),
            {"url": secret_url, "output_path": "downloads/out"},
            web_visit_config=_make_config(),
        )
    finally:
        logger.remove(sink_id)

    for record in records:
        assert secret_url not in record, f"Secret URL found in log record: {record!r}"
