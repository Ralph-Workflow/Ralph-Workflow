"""End-to-end tests for the runner's custom MCP startup validation."""

from __future__ import annotations

import json
import sys
from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.enums import AgentTransport
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.pipeline import runner as runner_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PACKAGE_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
FAKE_STDIO_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_stdio_mcp.py"

pytestmark = pytest.mark.timeout_seconds(20)


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def _write_fake_stdio_mcp_toml(workspace: Path) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    command = json.dumps(sys.executable)
    args = json.dumps([str(FAKE_STDIO_MCP.resolve())])
    body = (
        dedent(
            f"""
        [mcp_servers.fake_stdio]
        transport = "stdio"
        command = {command}
        args = {args}
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def _write_broken_stdio_mcp_toml(workspace: Path) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = (
        dedent(
            """
        [mcp_servers.broken]
        transport = "stdio"
        command = "/nonexistent/mcp"
        args = []
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def test_validate_with_real_stdio_fixture_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0


def test_validate_with_real_stdio_fixture_http_probe_skipped_for_stdio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.agents.transport_emit import _mcp_toml_as_upstreams  # noqa: PLC0415

    _write_fake_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    upstreams = _mcp_toml_as_upstreams(tmp_path)
    assert len(upstreams) == 1

    skip_reports = probe_agent_transports(
        upstreams,
        transports=(AgentTransport.CLAUDE, AgentTransport.OPENCODE),
        workspace_path=tmp_path,
    )
    for report in skip_reports:
        assert report.ok is True
        assert report.note is not None
        assert "skipped" in report.note

    codex_reports = probe_agent_transports(
        upstreams,
        transports=(AgentTransport.CODEX,),
        workspace_path=tmp_path,
    )
    assert len(codex_reports) == 1
    assert codex_reports[0].ok is True


def test_validate_with_broken_stdio_command_returns_one_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_broken_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    error_stream = StringIO()
    sink_id = logger.add(error_stream, level="ERROR")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 1
    assert "broken" in error_stream.getvalue()


def test_validate_with_broken_stdio_command_returns_zero_in_soft_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_broken_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")

    warning_stream = StringIO()
    sink_id = logger.add(warning_stream, level="WARNING")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 0
    assert "broken" in warning_stream.getvalue()
