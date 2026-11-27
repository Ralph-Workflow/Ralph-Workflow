from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import handle_exec_command
from ralph.pipeline.publication_gate_slots import GATE_SLOT_DIR_ENV, GATE_SLOTS_ENV
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


def _declare_gate(root: Path, command: str) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True)
    policy.write_text(f"RALPH-FACT: authoritative_verify_command: `{command}`\n", encoding="utf-8")


def _run(root: Path, shell_command: str) -> str:
    result = handle_exec_command(
        MockSession({"ProcessExecBounded"}),
        MockWorkspaceRoot(root),
        {"command": "sh", "args": ["-c", shell_command], "timeout_ms": 10000},
    )
    content = result.content[0]
    assert isinstance(content, ToolContent)
    return content.text


def test_an_agent_running_the_declared_gate_takes_a_host_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = tmp_path / "slots"
    monkeypatch.setenv(GATE_SLOT_DIR_ENV, str(slots))
    monkeypatch.setenv(GATE_SLOTS_ENV, "1")
    _declare_gate(tmp_path, "printf gate-ran")

    output = _run(tmp_path, "printf gate-ran")

    assert "gate-ran" in output
    assert (slots / "slot-0.lock").exists()


def test_a_narrower_command_runs_without_a_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = tmp_path / "slots"
    monkeypatch.setenv(GATE_SLOT_DIR_ENV, str(slots))
    _declare_gate(tmp_path, "printf gate-ran")

    output = _run(tmp_path, "printf narrow")

    assert "narrow" in output
    assert not slots.exists()
