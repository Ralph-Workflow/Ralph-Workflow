"""Black-box tests for ralph.config.welcome.emit_first_run_welcome."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.panel import Panel

from ralph.agents.registry import AgentRegistry
from ralph.config.bootstrap import BootstrapResult
from ralph.config.models import UnifiedConfig
from ralph.config.welcome import emit_first_run_welcome
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    import pytest

    from ralph.display.context import DisplayContext

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
        self.display_name = display_name


class _FakeRegistry:
    """Fake agent registry for testing availability checks.

    Implements list_agents() -> list[str] and get(name) -> _FakeAgent | None
    to match the _HasListAgents protocol used by emit_first_run_welcome.
    """

    def __init__(self, agents: dict[str, _FakeAgent]) -> None:
        self._agents = agents

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def get(self, name: str) -> _FakeAgent | None:
        return self._agents.get(name)


def _make_console() -> tuple[StringIO, Console]:
    buf = StringIO()
    return buf, Console(file=buf, force_terminal=False, theme=RALPH_THEME)


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


def _assert_no_raw_markup(output: str) -> None:
    """Assert that no raw Rich markup tokens appear in rendered output."""
    for token in _RAW_MARKUP_TOKENS:
        assert token not in output, (
            f"Raw markup token {token!r} found in rendered output: {output!r}"
        )


def test_emit_first_run_welcome_noops_on_all_skipped() -> None:
    """Welcome should not print when all results are skipped (subsequent runs)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "skipped", None),
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = console.getvalue()
    assert output == ""


def test_emit_first_run_welcome_prints_when_any_created() -> None:
    """Welcome should print when at least one file is created."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = console.getvalue()
    assert "Ralph Workflow first-run setup" in output
    assert "ralph-workflow.toml" in output


def test_emit_first_run_welcome_prints_when_any_regenerated() -> None:
    """Welcome should print when at least one file is regenerated."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    regen_result = BootstrapResult(
        Path("/global/ralph-workflow.toml"),
        "regenerated",
        Path("/global/ralph-workflow.toml.bak"),
    )
    results = [
        regen_result,
        BootstrapResult(Path("/global/mcp.toml"), "skipped", None),
    ]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = console.getvalue()
    assert "Ralph Workflow first-run setup" in output


def test_emit_first_run_welcome_flags_missing_agent() -> None:
    """Missing agent should be flagged with a warning indicator."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry(
        {"missing-agent": _FakeAgent("definitely-not-a-real-binary-xyz", "MissingAgent")}
    )
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = console.getvalue()
    # Should contain warning about missing agent
    assert "missing" in output.lower() or "⚠" in output


def test_emit_first_run_welcome_marks_available_agent() -> None:
    """Available agent should not be flagged as missing."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    # Use 'python' as it's guaranteed to be on PATH in CI
    registry = _FakeRegistry({"python": _FakeAgent("python", "Python")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = console.getvalue()
    # Should NOT contain "missing" warning for python
    assert "missing" not in output.lower() or "⚠" not in output


def test_emit_first_run_welcome_with_local_and_global_files(
    tmp_path: Path,
) -> None:
    """Welcome should group files by scope (global vs local)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [
        BootstrapResult(Path("/home/user/.config/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path("/home/user/.config/ralph-workflow-mcp.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/mcp.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/pipeline.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/artifacts.toml"), "created", None),
    ]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = console.getvalue()
    assert "Ralph Workflow first-run setup" in output
    # All file names should appear
    assert "ralph-workflow.toml" in output
    assert "ralph-workflow-mcp.toml" in output
    assert "agents.toml" not in output
    assert "pipeline.toml" in output
    assert "artifacts.toml" in output


def test_emit_first_run_welcome_noops_when_no_registry(
    tmp_path: Path,
) -> None:
    """Welcome should work without an agent registry (generic PATH message)."""
    console = StringIO()
    rich_console = Console(file=console, force_terminal=True, theme=RALPH_THEME)
    results = [
        BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None),
    ]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=None, display_context=ctx)

    output = console.getvalue()
    assert "Ralph Workflow first-run setup" in output
    assert "PATH" in output


def test_emit_first_run_welcome_banner_printed_before_panel() -> None:
    """Banner should be printed before the 'Ralph Workflow first-run setup' panel."""
    printed: list[object] = []

    class _RecordingConsole:
        def print(self, *args: object, **kwargs: object) -> None:
            printed.extend(args)

    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = make_display_context(env={})
    emit_first_run_welcome(_RecordingConsole(), results, display_context=ctx)

    assert len(printed) >= _MIN_PRINT_CALLS, "Expected at least two print calls (banner + panel)"
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
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = buf.getvalue()
    assert "ralph --diagnose" in output
    assert "edit" in output or "PROMPT.md" in output


def test_emit_first_run_welcome_next_steps_edit_and_prompt_present() -> None:
    """Existing next-step copy (edit and PROMPT.md) must still be present."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = buf.getvalue()
    assert "PROMPT.md" in output


def test_no_raw_markup_tokens_in_output() -> None:
    """Rendered output must not contain raw Rich markup tokens."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

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
    registry = _FakeRegistry({"claude": _FakeAgent("claude", "claude")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "Detected agents:" in output
    assert "claude" in output
    _assert_no_raw_markup(output)


def test_install_hint_shown_for_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing claude agent should show the claude install URL hint."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry({"claude": _FakeAgent("claude", "claude")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "claude" in output
    assert "docs.claude.com" in output
    _assert_no_raw_markup(output)


def test_install_hint_shown_for_opencode_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing opencode agent should show the opencode install URL hint."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    registry = _FakeRegistry({"opencode": _FakeAgent("opencode", "opencode")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

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
    registry = _FakeRegistry({"foocoder": _FakeAgent("foocoder", "foocoder")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

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
    registry = _FakeRegistry({"claude": _FakeAgent("claude", "claude")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "install:" not in output
    _assert_no_raw_markup(output)


def test_no_cmd_agent_does_not_show_install_hint() -> None:
    """Agent with empty cmd must not render missing-on-PATH or install-hint paths."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    # Agent with empty cmd named "claude" — must NOT trigger install URL lookup
    agent = _FakeAgent(cmd="", display_name="claude")
    registry = _FakeRegistry({"claude-no-cmd": agent})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "claude" in output
    assert "install:" not in output
    _assert_no_raw_markup(output)


def test_real_registry_missing_claude_shows_install_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real AgentRegistry with missing claude produces install URL in welcome output."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    cfg = UnifiedConfig()
    registry = AgentRegistry.from_config(cfg)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "claude" in output
    assert "docs.claude.com" in output
    assert "opencode" in output
    assert "opencode.ai" in output
    _assert_no_raw_markup(output)


def test_real_registry_generic_fallback_only_for_unknown_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real AgentRegistry: generic fallback does NOT appear when known agents are detected."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    cfg = UnifiedConfig()
    registry = AgentRegistry.from_config(cfg)

    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    # The generic fallback message should NOT appear since known agents were detected
    assert "Ensure your AI agents are on PATH" not in output
    assert "Detected agents:" in output
    _assert_no_raw_markup(output)


def test_emit_first_run_welcome_includes_pitch_sentence() -> None:
    """Welcome panel must include the elevator-pitch sentence about the pipeline loop."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = buf.getvalue()
    assert "planning" in output, (
        f"Expected 'planning' (part of pipeline loop pitch) in output, got: {output!r}"
    )
    assert "development" in output, (
        f"Expected 'development' (part of pipeline loop pitch) in output, got: {output!r}"
    )
    assert "PROMPT.md" in output, f"Expected 'PROMPT.md' in pitch output, got: {output!r}"
    _assert_no_raw_markup(output)


def test_emit_first_run_welcome_docs_pointer_includes_pydoc_ralph() -> None:
    """Welcome panel docs pointer must include 'python -m pydoc ralph'."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = buf.getvalue()
    assert "pydoc ralph" in output, (
        f"Expected 'pydoc ralph' in docs pointer output, got: {output!r}"
    )
    _assert_no_raw_markup(output)


def test_emit_first_run_welcome_panel_includes_getting_started_pointer() -> None:
    """First-run welcome panel must point new users to getting-started.md."""
    buf, rich_console = _make_console()
    results = [BootstrapResult(Path("/global/ralph-workflow.toml"), "created", None)]
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, display_context=ctx)

    output = buf.getvalue()
    assert "getting-started" in output, (
        f"Expected 'getting-started' reference in first-run welcome panel, got: {output!r}"
    )
    _assert_no_raw_markup(output)


def test_emit_first_run_welcome_agents_section_before_config_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Detected agents section must appear before Global/Local config files sections."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    buf, rich_console = _make_console()
    results = [
        BootstrapResult(Path("/home/user/.config/ralph-workflow.toml"), "created", None),
        BootstrapResult(Path(str(tmp_path) + "/.agent/pipeline.toml"), "created", None),
    ]
    registry = _FakeRegistry({"claude": _FakeAgent("claude", "claude")})
    ctx = _make_display_context_for_console(rich_console)

    emit_first_run_welcome(rich_console, results, agent_registry=registry, display_context=ctx)

    output = buf.getvalue()
    assert "Detected agents:" in output
    assert "Global config files:" in output

    agents_pos = output.index("Detected agents:")
    global_pos = output.index("Global config files:")
    assert agents_pos < global_pos, (
        "Expected 'Detected agents:' to appear before 'Global config files:' in output, "
        f"but agents_pos={agents_pos}, global_pos={global_pos}"
    )
    _assert_no_raw_markup(output)
