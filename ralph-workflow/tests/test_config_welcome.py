"""Black-box tests for ralph.config.welcome.emit_first_run_welcome."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.panel import Panel

from ralph.config.bootstrap import BootstrapResult
from ralph.config.welcome import emit_first_run_welcome

if TYPE_CHECKING:
    import pytest

_MIN_PRINT_CALLS = 2

# Raw markup tokens that must never appear in rendered console output.
_RAW_MARKUP_TOKENS = (
    "[bold cyan]",
    "[/bold cyan]",
    "[yellow]",
    "[/yellow]",
    "[dim]",
    "[/dim]",
    "[green]",
    "[/green]",
    "[cyan]",
    "[/cyan]",
)


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


def _make_console() -> tuple[StringIO, Console]:
    buf = StringIO()
    return buf, Console(file=buf, force_terminal=False)


def _assert_no_raw_markup(output: str) -> None:
    """Assert that no raw Rich markup tokens appear in rendered output."""
    for token in _RAW_MARKUP_TOKENS:
        assert token not in output, (
            f"Raw markup token {token!r} found in rendered output: {output!r}"
        )


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


def test_emit_first_run_welcome_banner_printed_before_panel() -> None:
    """Banner should be printed before the 'Ralph first-run setup' panel."""
    printed: list[object] = []

    class _RecordingConsole:
        def print(self, *args: object, **kwargs: object) -> None:
            printed.extend(args)

    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    emit_first_run_welcome(_RecordingConsole(), results)

    assert len(printed) >= _MIN_PRINT_CALLS, (
        "Expected at least two print calls (banner + panel)"
    )
    # First call is the banner renderable — show_banner emits a rich Group
    assert isinstance(printed[0], Group), (
        f"First printed object should be a Rich Group (banner), got: {type(printed[0])}"
    )
    # Second call contains the panel
    assert isinstance(printed[1], Panel), (
        f"Second printed object should be a Rich Panel (welcome), got: {type(printed[1])}"
    )


def test_emit_first_run_welcome_next_steps_mentions_diagnose() -> None:
    """Next steps should mention ralph --diagnose."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]

    emit_first_run_welcome(rich_console, results)

    output = buf.getvalue()
    assert "ralph --diagnose" in output
    assert "Edit" in output or "PROMPT.md" in output


def test_emit_first_run_welcome_next_steps_edit_and_prompt_present() -> None:
    """Existing next-step copy (Edit and PROMPT.md) must still be present."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]

    emit_first_run_welcome(rich_console, results)

    output = buf.getvalue()
    assert "PROMPT.md" in output


def test_no_raw_markup_tokens_in_output() -> None:
    """Rendered output must not contain raw Rich markup tokens."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]

    emit_first_run_welcome(rich_console, results)

    output = buf.getvalue()
    # Human-facing content must be present
    assert "Next steps:" in output
    assert "PROMPT.md" in output
    assert "ralph --diagnose" in output
    # No raw markup tokens should appear
    _assert_no_raw_markup(output)


def test_no_raw_markup_tokens_with_detected_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent availability lines must not contain raw markup tokens."""
    # Make claude appear missing
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("claude", "claude")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "Detected agents:" in output
    assert "claude" in output
    _assert_no_raw_markup(output)


def test_install_hint_shown_for_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing claude agent should show the claude install URL hint."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("claude", "claude")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "claude" in output
    assert "docs.claude.com" in output
    _assert_no_raw_markup(output)


def test_install_hint_shown_for_opencode_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing opencode agent should show the opencode install URL hint."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("opencode", "opencode")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "opencode" in output
    assert "opencode.ai" in output
    _assert_no_raw_markup(output)


def test_install_hint_not_shown_for_unknown_agent_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown missing agent should NOT show any install URL."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("foocoder", "foocoder")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "foocoder" in output
    assert "missing" in output.lower() or "⚠" in output
    assert "install:" not in output
    _assert_no_raw_markup(output)


def test_install_hint_not_shown_when_agent_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When claude is on PATH, no install hint should appear."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry([_FakeAgent("claude", "claude")])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "install:" not in output
    _assert_no_raw_markup(output)


def test_no_cmd_agent_does_not_show_install_hint() -> None:
    """Agent with empty cmd must not render missing-on-PATH or install-hint paths."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    # Agent with empty cmd named "claude" — must NOT trigger install URL lookup
    agent = _FakeAgent.__new__(_FakeAgent)
    agent.cmd = ""
    agent.display_name = "claude"
    registry = _FakeRegistry([agent])

    emit_first_run_welcome(rich_console, results, agent_registry=registry)

    output = buf.getvalue()
    assert "claude" in output
    assert "install:" not in output
    _assert_no_raw_markup(output)
