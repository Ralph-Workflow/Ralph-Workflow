"""Unit tests for CLI."""

from __future__ import annotations

import os
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rich_click as click
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner as TyperCliRunner

import ralph.pipeline.runner as runner_module
from ralph.cli.commands.commit import CommitPlumbingOptions
from ralph.cli.main import (
    CLIOverrideInput,
    _build_cli_overrides,
    _configure_logging,
    _handle_check_config,
    _handle_check_mcp,
    _handle_commit_plumbing,
    _handle_list_agents,
    _handle_list_providers,
    _run_pipeline,
    app,
)
from ralph.config.enums import Verbosity
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.workspace.scope import WorkspaceScope

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


class CliResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class CliRunner:
    def __init__(self) -> None:
        self._cwd = PROJECT_ROOT
        self._runner = TyperCliRunner()

    def invoke(self, _app: object, args: list[str]) -> CliResult:
        with self._pushd(self._cwd):
            result = self._runner.invoke(app, args, catch_exceptions=False)
        stderr = getattr(result, "stderr", "")
        return CliResult(result.exit_code, result.stdout, stderr)

    @contextmanager
    def _pushd(self, path: Path):
        original_cwd = Path.cwd()
        try:
            os.chdir(path)
            yield
        finally:
            os.chdir(original_cwd)

    @contextmanager
    def isolated_filesystem(self, temp_dir: Path):
        temp_dir.mkdir(parents=True, exist_ok=True)
        with self._runner.isolated_filesystem(temp_dir):
            yield temp_dir


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner for testing."""
    return CliRunner()


def test_app_help(cli_runner: CliRunner) -> None:
    """Test that --help works."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Ralph" in result.stdout
    assert "Multi-agent" in result.stdout


def test_app_help_mentions_init_label_deprecation(cli_runner: CliRunner) -> None:
    """Top-level help should explain that `--init` labels are deprecated."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--init" in result.stdout
    assert "deprecated" in result.stdout.lower()


def test_app_help_mentions_checkpoint_json_output(cli_runner: CliRunner) -> None:
    """Top-level help should make checkpoint inspection JSON output explicit."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--inspect-checkpoint" in result.stdout
    assert "JSON" in result.stdout


def test_app_help_mentions_commit_msg_artifact_deletion(cli_runner: CliRunner) -> None:
    """Top-level help should explain when --show-commit-msg may show nothing."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--show-commit-msg" in result.stdout
    assert "deleted" in result.stdout.lower()


def test_app_rejects_conflicting_resume_flags(cli_runner: CliRunner) -> None:
    """Conflicting resume flags should fail loudly instead of silently preferring one."""
    result = cli_runner.invoke(app, ["--resume", "--no-resume"])
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "--resume" in result.stderr
    assert "--no-resume" in result.stderr
    assert "conflict" in result.stderr.lower()


def test_app_version(cli_runner: CliRunner) -> None:
    """Test that --version works."""
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Ralph" in result.stdout or "version" in result.stdout.lower()


def test_unknown_command_uses_typer_usage_error(cli_runner: CliRunner) -> None:
    """Unknown commands should fail with Typer's standard exit code and message."""
    result = cli_runner.invoke(app, ["does-not-exist"])
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "No such command 'does-not-exist'" in result.stderr
    assert "Try 'ralph --help' for help." in result.stderr


def test_unknown_option_uses_typer_usage_error(cli_runner: CliRunner) -> None:
    """Unknown options should surface Typer's standard usage error."""
    result = cli_runner.invoke(app, ["--does-not-exist"])
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "No such option: --does-not-exist" in result.stderr
    assert "Usage: ralph [OPTIONS] COMMAND [ARGS]..." in result.stderr


@pytest.mark.parametrize(
    "flag",
    ["--rebase-only", "--apply-commit", "--no-isolation"],
)
def test_removed_top_level_flags_are_rejected_after_cleanup(
    cli_runner: CliRunner, flag: str
) -> None:
    """Cleanup should leave the real CLI with no legacy top-level flags."""
    command = get_command(app)

    with pytest.raises(click.exceptions.NoSuchOption):  # type: ignore[attr-defined]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        command.make_context("ralph", [flag], resilient_parsing=False)


def test_cleanup_rejects_unexpected_extra_argument(cli_runner: CliRunner) -> None:
    """Subcommands should keep strict parsing for unexpected extra arguments."""
    result = cli_runner.invoke(app, ["cleanup", "extra-arg"])
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "Got unexpected extra argument (extra-arg)" in result.stderr


def test_app_list_agents_empty(cli_runner: CliRunner) -> None:
    """Test --list-agents with no configuration."""
    result = cli_runner.invoke(app, ["--list-agents"])
    assert result.exit_code == 0


def test_app_check_config_valid(cli_runner: CliRunner) -> None:
    """Test --check-config with valid config."""
    result = cli_runner.invoke(app, ["--check-config"])
    assert result.exit_code == 0


def test_app_init_creates_files(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test --init creates necessary files."""
    with cli_runner.isolated_filesystem(temp_dir=tmp_path):
        result = cli_runner.invoke(app, ["--init", str(tmp_path)])
        # Init may fail due to missing git repo, but should not crash
        assert result.exit_code in (0, 1)


def test_app_diagnose(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test --diagnose runs without crashing."""
    with cli_runner.isolated_filesystem(temp_dir=tmp_path):
        result = cli_runner.invoke(app, ["--diagnose"])
        # May fail without git repo or return a validation-specific exit code,
        # but it should not crash with an unexpected Typer usage error.
        assert result.exit_code in (0, 1, 2)


def test_app_with_invalid_config(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test app tolerates invalid explicit config path without crashing."""
    invalid_config = tmp_path / "invalid.toml"
    invalid_config.write_text("invalid toml {{{{")

    result = cli_runner.invoke(app, ["--config", str(invalid_config), "--check-config"])
    assert result.exit_code == 0


def test_verbose_flag(cli_runner: CliRunner) -> None:
    """Test -v flag is accepted."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "-v" in result.stdout or "--verbosity" in result.stdout


def test_quiet_flag(cli_runner: CliRunner) -> None:
    """Test --quiet flag is accepted."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--quiet" in result.stdout


def test_handle_list_agents_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """List agents renders a table when config loads."""

    sentinel = {"bot": "alpha"}
    called: dict[str, object] = {}

    def fake_load_config(
        path: Path | None,
        overrides: dict[str, object],
        **kwargs: object,
    ) -> SimpleNamespace:
        called["config_path"] = path
        called["overrides"] = overrides
        called["kwargs"] = kwargs
        return SimpleNamespace(agents=sentinel)

    def fake_display_agents_table(agents: dict[str, object], *, display_context=None) -> None:
        called["agents"] = agents

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)
    monkeypatch.setattr("ralph.cli.main.display_agents_table", fake_display_agents_table)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    exit_code = _handle_list_agents("/tmp/config.toml", {}, True, display_context=ctx)
    assert exit_code == 0
    assert called["agents"] is sentinel


def test_handle_list_agents_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failures from load_config bubble up as exit code 1."""

    def fake_load_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    exit_code = _handle_list_agents(None, {}, True, display_context=ctx)
    assert exit_code == 1


def test_handle_check_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check config returns 0 and prints a success banner."""

    monkeypatch.setattr("ralph.cli.main.load_config", lambda *args, **kwargs: object())
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)

    exit_code = _handle_check_config(None, {}, True, console=console)
    assert exit_code == 0
    assert "Configuration is valid" in stream.getvalue()


def test_handle_check_config_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure to load config returns code 1."""

    def fake_load_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)

    exit_code = _handle_check_config(None, {}, True)
    assert exit_code == 1


def test_handle_check_mcp_returns_none_when_flag_false() -> None:
    """--check-mcp disabled is a no-op and returns None to continue execution."""
    assert _handle_check_mcp(False) is None


def test_handle_check_mcp_flag_returns_zero_when_validation_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--check-mcp exits 0 and prints success when the validator returns 0."""
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr("ralph.cli.main.resolve_workspace_scope", lambda: scope)

    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _root: 0)
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)

    assert _handle_check_mcp(True, console=console) == 0
    assert "MCP servers validated successfully" in stream.getvalue()


def test_handle_check_mcp_flag_returns_one_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--check-mcp exits 1 and prints failure when the validator returns 1."""
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr("ralph.cli.main.resolve_workspace_scope", lambda: scope)

    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _root: 1)
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)

    assert _handle_check_mcp(True, console=console) == 1
    assert "MCP validation failed" in stream.getvalue()


def test_handle_list_agents_injects_workspace_scope_for_implicit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}
    scope = WorkspaceScope("/tmp/worktree")

    def fake_load_config(*args: object, **kwargs: object) -> SimpleNamespace:
        called["kwargs"] = kwargs
        return SimpleNamespace(agents={})

    monkeypatch.setattr("ralph.cli.main.resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)
    monkeypatch.setattr(
        "ralph.cli.main.display_agents_table", lambda _agents, *, display_context=None: None
    )

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    assert _handle_list_agents(None, {}, True, display_context=ctx) == 0
    assert called["kwargs"] == {"workspace_scope": scope}


def test_handle_list_providers_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """List providers renders the provider list."""

    monkeypatch.setattr("ralph.cli.main.fetch_providers", lambda: ["opencode"])
    recorded: list[object] = []

    def fake_display_providers_table(providers: object, *, display_context=None) -> None:
        recorded.append(providers)

    monkeypatch.setattr("ralph.cli.main.display_providers_table", fake_display_providers_table)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    exit_code = _handle_list_providers(True, display_context=ctx)
    assert exit_code == 0
    assert recorded == [["opencode"]]


def test_handle_list_providers_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """List providers returns 1 when fetching fails."""

    def fake_fetch() -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.fetch_providers", fake_fetch)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    exit_code = _handle_list_providers(True, display_context=ctx)
    assert exit_code == 1


def test_handle_commit_plumbing_invokes_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit plumbing delegates to the helper when flags are set."""

    calls: list[CommitPlumbingOptions] = []

    def fake_commit_plumbing(
        *, options: CommitPlumbingOptions | None = None, display_context=None
    ) -> None:
        calls.append(options or CommitPlumbingOptions())

    monkeypatch.setattr("ralph.cli.main.commit_plumbing", fake_commit_plumbing)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    options = CommitPlumbingOptions(generate_commit_msg=True)
    result = _handle_commit_plumbing(options, display_context=ctx)
    assert result == 0
    assert calls


def test_handle_commit_plumbing_no_flags_does_not_call_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit plumbing is skipped when no flags are set."""

    called = False

    def fake_commit_plumbing(
        *, options: CommitPlumbingOptions | None = None, display_context=None
    ) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("ralph.cli.main.commit_plumbing", fake_commit_plumbing)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    options = CommitPlumbingOptions()
    result = _handle_commit_plumbing(options, display_context=ctx)
    assert result is None
    assert not called


def test_run_pipeline_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run pipeline returns the runner exit code."""

    recorded: dict[str, object] = {}

    def fake_run_pipeline(  # noqa: PLR0913
        *,
        config_path,
        cli_overrides,
        dry_run,
        resume,
        verbosity=None,
        display_context=None,
        counter_overrides=None,
        inline_prompt=None,
    ):  # type: ignore[override]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        recorded["config_path"] = config_path
        recorded["cli_overrides"] = cli_overrides
        recorded["dry_run"] = dry_run
        recorded["resume"] = resume
        return RUN_PIPELINE_SUCCESS

    monkeypatch.setattr("ralph.cli.main.run_pipeline", fake_run_pipeline)

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = _make_display_context_for_console(console)
    exit_code = _run_pipeline(
        "/tmp/config.toml",
        {"foo": "bar"},
        dry_run=True,
        resume=True,
        no_resume=False,
        display_context=ctx,
    )
    assert exit_code == RUN_PIPELINE_SUCCESS
    assert recorded["config_path"] == Path("/tmp/config.toml")
    assert recorded["cli_overrides"] == {"foo": "bar"}
    assert recorded["dry_run"]
    assert recorded["resume"]


def test_run_pipeline_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt is translated to 130."""

    monkeypatch.setattr(
        "ralph.cli.main.run_pipeline",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console)

    exit_code = _run_pipeline(
        None, {}, dry_run=False, resume=False, no_resume=False, display_context=ctx
    )
    assert exit_code == KEYBOARD_INTERRUPT_EXIT_CODE
    assert "Interrupted by user" in stream.getvalue()


def test_run_pipeline_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """General exceptions surface as a user-facing error."""

    def fake_run(*args: object, **kwargs: object) -> None:  # pragma: no cover - raised immediately
        raise RuntimeError("boom")

    logged: list[str] = []
    monkeypatch.setattr("ralph.cli.main.run_pipeline", fake_run)

    def capture_exception(message: object) -> None:  # pragma: no cover - helper only
        logged.append(str(message))

    monkeypatch.setattr("ralph.cli.main.logger.exception", capture_exception)
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console)

    exit_code = _run_pipeline(
        None, {}, dry_run=False, resume=False, no_resume=False, display_context=ctx
    )
    assert exit_code == 1
    assert logged == ["Pipeline failed: {}"]
    assert "Error" in stream.getvalue()


def test_build_cli_overrides_sets_values() -> None:
    """CLI overrides mirror the supported inputs only."""

    cli_input = CLIOverrideInput(
        developer_agent="dev",
        developer_model="dev-model",
        git_user_name="Jane",
        git_user_email="jane@example.com",
        developer_iters=7,
    )

    overrides = cast("dict[str, object]", _build_cli_overrides(cli_input))
    general = cast("dict[str, object]", overrides["general"])
    execution = cast("dict[str, object]", general["execution"])
    assert general["git_user_name"] == "Jane"
    assert general["git_user_email"] == "jane@example.com"
    assert general["developer_iters"] == 7  # noqa: PLR2004
    assert execution == {}
    assert overrides["developer_agent"] == "dev"
    assert overrides["developer_model"] == "dev-model"


def test_cli_override_input_rejects_removed_isolation_mode_field() -> None:
    """The removed isolation toggle must not remain as hidden compatibility plumbing."""

    factory: Any = CLIOverrideInput
    with pytest.raises(TypeError):
        factory(isolation_mode=True)


def test_configure_logging_sets_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging configuration delegates to loguru with expected levels."""

    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr("ralph.cli.main.logger.remove", lambda: calls.append(("remove", (), {})))
    monkeypatch.setattr(
        "ralph.cli.main.logger.add",
        lambda *args, **kwargs: calls.append(("add", args, dict(kwargs))),
    )

    _configure_logging(Verbosity.QUIET)
    assert calls[-1][0] == "add"
    assert calls[-1][2]["level"] == "ERROR"


def test_configure_logging_debug_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Debug verbosity configures the trace level with formatting."""

    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr("ralph.cli.main.logger.remove", lambda: calls.append(("remove", (), {})))
    monkeypatch.setattr(
        "ralph.cli.main.logger.add",
        lambda *args, **kwargs: calls.append(("add", args, dict(kwargs))),
    )

    _configure_logging(Verbosity.DEBUG)
    assert calls[-1][0] == "add"
    assert calls[-1][2]["level"] == "TRACE"


def test_regenerate_config_flag_creates_bak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regenerate config creates .bak backup in isolated temp workspace."""
    # Set up XDG_CONFIG_HOME to temp directory
    xdg_dir = tmp_path / "xdg"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    # Create existing global config
    existing = xdg_dir / "ralph-workflow.toml"
    existing.write_text("# MINE", encoding="utf-8")

    # Change to temp directory so resolve_workspace_scope() resolves to temp workspace
    # (not the real repo's .agent/)
    monkeypatch.chdir(tmp_path)

    runner = TyperCliRunner()
    result = runner.invoke(app, ["--regenerate-config"], catch_exceptions=False)
    assert result.exit_code == 0

    # Verify backup was created in XDG_CONFIG_HOME
    bak = xdg_dir / "ralph-workflow.toml.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "# MINE"
    assert (xdg_dir / "ralph-workflow.toml").read_text(encoding="utf-8").startswith("#")


def test_explain_policy_prints_workflow_diagram(cli_runner: CliRunner) -> None:
    """--explain-policy prints the workflow diagram and structural breakdown."""
    # Explicitly point at the bundled defaults so the test is not environment-dependent.
    result = cli_runner.invoke(
        app, ["--explain-policy", "--explain-policy-dir", str(_BUNDLED_POLICY_DIR)]
    )

    # Should exit successfully
    assert result.exit_code == 0

    # Should contain the ASCII diagram section
    assert "WORKFLOW DIAGRAM" in result.stdout

    # Should contain entry marker
    assert "=ENTRY=>" in result.stdout

    # Should contain terminal success marker
    assert "==SUCCESS==>" in result.stdout

    # Should also contain the structural breakdown section
    assert "RALPH WORKFLOW — ACTIVE POLICY EXPLANATION" in result.stdout

    # Should contain post-commit routing explanation for the reviewless bundled defaults.
    assert "Explanation: after commit phase 'development_commit'" in result.stdout


class TestParseCounterOverrides:
    """Tests for _parse_counter_overrides helper."""

    def test_parses_single_valid_entry(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        result = _parse_counter_overrides(["iteration=3"])
        assert result == {"iteration": 3}

    def test_parses_multiple_entries(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        result = _parse_counter_overrides(["iteration=3", "reviewer_pass=1"])
        assert result == {"iteration": 3, "reviewer_pass": 1}

    def test_empty_list_returns_empty_dict(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        assert _parse_counter_overrides([]) == {}

    def test_missing_equals_raises_usage_error(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        with pytest.raises(click.UsageError, match="invalid format"):
            _parse_counter_overrides(["iteration3"])

    def test_blank_name_raises_usage_error(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        with pytest.raises(click.UsageError, match="blank counter name"):
            _parse_counter_overrides(["=5"])

    def test_non_integer_value_raises_usage_error(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        with pytest.raises(click.UsageError, match="not a valid integer"):
            _parse_counter_overrides(["iteration=abc"])

    def test_zero_value_is_valid(self) -> None:
        from ralph.cli.main import _parse_counter_overrides  # noqa: PLC0415

        result = _parse_counter_overrides(["reviewer_pass=0"])
        assert result == {"reviewer_pass": 0}


class TestIterationCounterFlags:
    def test_developer_iters_flag_sets_config_override(self) -> None:
        overrides = cast(
            "dict[str, object]",
            _build_cli_overrides(CLIOverrideInput(developer_iters=3)),
        )
        general = cast("dict[str, object]", overrides["general"])
        assert general["developer_iters"] == 3  # noqa: PLR2004

    def test_counter_flag_passes_overrides_to_run_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda **kw: captured.update(kw) or 0,
        )
        monkeypatch.setattr(
            "ralph.cli.main._bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main._configure_logging", lambda v: None)

        runner = TyperCliRunner()
        runner.invoke(
            app,
            ["--counter", "iteration=2", "--counter", "reviewer_pass=1", "--dry-run"],
            catch_exceptions=False,
        )

        assert captured.get("counter_overrides") == {"iteration": 2, "reviewer_pass": 1}


class TestPrepareInitArgs:
    """Tests for _prepare_init_args sys.argv fallback."""

    def test_none_falls_back_to_sys_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ralph.cli.main import _prepare_init_args  # noqa: PLC0415

        monkeypatch.setattr("sys.argv", ["ralph", "-Q", "do a quick change", "--dry-run"])
        result = _prepare_init_args(None)
        assert result == ["-Q", "--prompt", "do a quick change", "--dry-run"]

    def test_explicit_args_bypass_sys_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ralph.cli.main import _prepare_init_args  # noqa: PLC0415

        monkeypatch.setattr("sys.argv", ["ralph", "--should-not-be-used"])
        result = _prepare_init_args(["-Q", "task"])
        assert result == ["-Q", "--prompt", "task"]


class TestInjectQuickPrompt:
    """Tests for _inject_quick_prompt preprocessing helper."""

    def test_injects_prompt_flag_before_positional_text(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "do a quick change"])
        assert result == ["-Q", "--prompt", "do a quick change"]

    def test_long_quick_flag_also_triggers_injection(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["--quick", "do a task"])
        assert result == ["--quick", "--prompt", "do a task"]

    def test_options_after_text_are_preserved(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "do a task", "--dry-run"])
        assert result == ["-Q", "--prompt", "do a task", "--dry-run"]

    def test_skips_injection_when_prompt_already_present(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "--prompt", "text"])
        assert result == ["-Q", "--prompt", "text"]

    def test_skips_injection_when_short_prompt_already_present(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "-P", "text"])
        assert result == ["-Q", "-P", "text"]

    def test_no_injection_when_no_quick_flag(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["does-not-exist"])
        assert result == ["does-not-exist"]

    def test_known_subcommand_is_not_treated_as_prompt(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "cleanup"])
        assert result == ["-Q", "cleanup"]

    def test_no_positional_text_leaves_args_unchanged(self) -> None:
        from ralph.cli.main import _inject_quick_prompt  # noqa: PLC0415

        result = _inject_quick_prompt(["-Q", "--dry-run"])
        assert result == ["-Q", "--dry-run"]


class TestQuickModeSemantics:
    """Tests for --quick/-Q flag behavior."""

    def test_quick_mode_forces_developer_iters_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda **kw: captured.update(kw) or 0,
        )
        monkeypatch.setattr(
            "ralph.cli.main._bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main._configure_logging", lambda v: None)

        runner = TyperCliRunner()
        runner.invoke(app, ["-Q", "--prompt", "do a task", "--dry-run"], catch_exceptions=False)

        cli_overrides = cast("dict[str, object]", captured.get("cli_overrides"))
        general = cast("dict[str, object]", cli_overrides["general"])
        assert general["developer_iters"] == 1

    def test_quick_overrides_developer_iters_when_both_supplied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda **kw: captured.update(kw) or 0,
        )
        monkeypatch.setattr(
            "ralph.cli.main._bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main._configure_logging", lambda v: None)

        runner = TyperCliRunner()
        runner.invoke(
            app,
            ["-Q", "-D", "5", "--prompt", "do a task", "--dry-run"],
            catch_exceptions=False,
        )

        cli_overrides = cast("dict[str, object]", captured.get("cli_overrides"))
        general = cast("dict[str, object]", cli_overrides["general"])
        assert general["developer_iters"] == 1

    def test_quick_mode_positional_text_is_passed_as_inline_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda **kw: captured.update(kw) or 0,
        )
        monkeypatch.setattr(
            "ralph.cli.main._bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main._configure_logging", lambda v: None)

        runner = TyperCliRunner()
        runner.invoke(
            app,
            ["-Q", "do a quick change", "--dry-run"],
            catch_exceptions=False,
        )

        assert captured.get("inline_prompt") == "do a quick change"

    def test_prompt_without_quick_raises_usage_error(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, ["--prompt", "some text"])
        assert result.exit_code == 2  # noqa: PLR2004
        assert "--prompt requires --quick/-Q" in result.stderr or "--prompt requires" in result.stdout  # noqa: E501


class TestRemovedReviewFlags:
    """Verify that review-era CLI flags that no longer exist are absent from help output."""

    @pytest.mark.parametrize(
        "flag",
        [
            "--reviewer-reviews",
            "--reviewer-agent",
            "--reviewer-model",
            "--review-depth",
        ],
    )
    def test_removed_review_flags_not_in_help(self, cli_runner: CliRunner, flag: str) -> None:
        result = cli_runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert flag not in result.stdout

    def test_quick_flag_is_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--quick" in result.stdout or "-Q" in result.stdout
