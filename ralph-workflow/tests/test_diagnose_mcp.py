"""Tests for the diagnose command's custom MCP server probe rendering."""

from __future__ import annotations

import json
import sys
from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli.commands import diagnose as diagnose_module
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
FAKE_STDIO_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_stdio_mcp.py"

pytestmark = pytest.mark.timeout_seconds(20)


def _attach_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=200)
    monkeypatch.setattr(diagnose_module, "console", console)
    return stream


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


def test_diagnose_renders_custom_mcp_tables_with_real_stdio_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    stream = _attach_console(monkeypatch)
    workspace_scope = WorkspaceScope(tmp_path)

    diagnose_module._check_mcp_servers(workspace_scope)

    output = stream.getvalue()
    assert "Custom MCP Servers" in output
    assert "fake_stdio" in output
    assert "ok" in output
    assert "Agent Transport Compatibility" in output
    assert "Claude" in output
    assert "Codex" in output
    assert "OpenCode" in output


def test_diagnose_handles_workspace_with_no_custom_mcp_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch)
    workspace_scope = WorkspaceScope(tmp_path)

    diagnose_module._check_mcp_servers(workspace_scope)

    output = stream.getvalue()
    assert "Custom MCP Servers" in output
    assert "No custom MCP servers configured" in output
    assert "Agent Transport Compatibility" not in output
