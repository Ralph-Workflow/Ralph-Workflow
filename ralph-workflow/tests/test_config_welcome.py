"""Black-box tests for ralph.config.welcome.emit_first_run_welcome."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.config.bootstrap import BootstrapResult
from ralph.config.welcome import emit_first_run_welcome


class _FakeAgent:
    """Fake agent config for registry testing."""

    def __init__(self, cmd: str, display_name: str | None = None) -> None:
        self.cmd = cmd
        self.display_name = display_name or cmd.split(maxsplit=1)[0]


class _FakeRegistry:
    """Fake agent registry for testing availability checks."""

    def __init__(self, agents: list[_FakeAgent]) -> None:
        self._agents = agents

    def list_agents(self) -> list[_FakeAgent]:
        return self._agents


def test_emit_first_run_welcome_noops_on_all_skipped() -> None:
    """Welcome should not print when all results are skipped (subsequent runs)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "skipped", None),
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]

    emit_first_run_welcome(rich_console, results)

    output = console.getvalue()
    assert output == ""


def test_emit_first_run_welcome_prints_when_any_created() -> None:
    """Welcome should print when at least one file is created."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]

    emit_first_run_welcome(rich_console, results)

    output = console.getvalue()
    assert "Ralph first-run setup" in output
    assert "ralph-workflow.toml" in output


def test_emit_first_run_welcome_prints_when_any_regenerated() -> None:
    """Welcome should print when at least one file is regenerated."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    regen_result = BootstrapResult(
        Path("/global/ralph-workflow.toml"),
        "regenerated",
        Path("/global/ralph-workflow.toml.bak"),
    )
    results = [
        regen_result,
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]

    emit_first_run_welcome(rich_console, results)

    output = console.getvalue()
    assert "Ralph first-run setup" in output


def test_emit_first_run_welcome_flags_missing_agent() -> None:
    """Missing agent should be flagged with a warning indicator."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("definitely-not-a-real-binary-xyz", "MissingAgent")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = console.getvalue()
    # Should contain warning about missing agent
    assert "missing" in output.lower() or "⚠" in output


def test_emit_first_run_welcome_marks_available_agent() -> None:
    """Available agent should not be flagged as missing."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    # Use 'python' as it's guaranteed to be on PATH in CI
    registry = _FakeRegistry([_FakeAgent("python", "Python")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = console.getvalue()
    # Should NOT contain "missing" warning for python
    assert "missing" not in output.lower() or "⚠" not in output


def test_emit_first_run_welcome_with_local_and_global_files(
    tmp_path: Path,
) -> None:
    """Welcome should group files by scope (global vs local)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [
        BootstrapResult(Path("/home/user/.config/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path("/home/user/.config/ralph-workflow-mcp.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/mcp.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/agents.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/pipeline.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/artifacts.toml"), "created", None),
    ]

    emit_first_run_welcome(rich_console, results)

    output = console.getvalue()
    assert "Ralph first-run setup" in output
    # All file names should appear
    assert "ralph-workflow.toml" in output
    assert "ralph-workflow-mcp.toml" in output
    assert "agents.toml" in output
    assert "pipeline.toml" in output
    assert "artifacts.toml" in output


def test_emit_first_run_welcome_noops_when_no_registry(
    tmp_path: Path,
) -> None:
    """Welcome should work without an agent registry (generic PATH message)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None),
    ]

    emit_first_run_welcome(rich_console, results, agent_registry=None)

    output = console.getvalue()
    assert "Ralph first-run setup" in output
    assert "PATH" in output
