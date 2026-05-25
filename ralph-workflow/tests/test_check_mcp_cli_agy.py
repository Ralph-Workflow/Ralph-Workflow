"""Tests for the public --check-mcp CLI path with AGY-compatible workspaces."""

from __future__ import annotations

import json
import sys
from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli import main as cli_main
from ralph.cli.main import handle_check_mcp
from ralph.display.theme import RALPH_THEME
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
FAKE_STDIO_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_stdio_mcp.py"

pytestmark = pytest.mark.timeout_seconds(20)


def _make_test_console() -> tuple[Console, StringIO]:
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=False, color_system=None, width=200, theme=RALPH_THEME
    )
    return console, stream


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


def test_handle_check_mcp_validates_agy_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_stdio_mcp_toml(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setattr(cli_main, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    console, stream = _make_test_console()

    rc = handle_check_mcp(True, console=console)

    assert rc == 0
    assert "MCP servers validated successfully" in stream.getvalue()


def test_handle_check_mcp_is_noop_when_flag_false() -> None:
    console, _stream = _make_test_console()

    rc = handle_check_mcp(False, console=console)

    assert rc is None
