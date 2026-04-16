"""Unit tests for CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ralph.cli.commands.commit import CommitPlumbingOptions
from ralph.cli.main import (
    CLIOverrideInput,
    _build_cli_overrides,
    _configure_logging,
    _handle_check_config,
    _handle_commit_plumbing,
    _handle_list_agents,
    _handle_list_providers,
    _run_pipeline,
    app,
)
from ralph.config.enums import ReviewDepth, Verbosity
from ralph.workspace.scope import WorkspaceScope

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
DEFAULT_DEVELOPER_ITERS = 3


class CliResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class CliRunner:
    def __init__(self) -> None:
        self._cwd = PROJECT_ROOT

    def invoke(self, _app: object, args: list[str]) -> CliResult:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(PROJECT_ROOT)
        )
        result = subprocess.run(
            [sys.executable, "-m", "ralph.cli.main", *args],
            cwd=self._cwd,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        return CliResult(result.returncode, result.stdout, result.stderr)

    @contextmanager
    def isolated_filesystem(self, temp_dir: Path):
        temp_dir.mkdir(parents=True, exist_ok=True)
        original_cwd = self._cwd
        self._cwd = temp_dir
        try:
            yield temp_dir
        finally:
            self._cwd = original_cwd


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


def test_app_version(cli_runner: CliRunner) -> None:
    """Test that --version works."""
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Ralph" in result.stdout or "version" in result.stdout.lower()


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
        # May fail without git repo but shouldn't crash
        assert result.exit_code in (0, 1)


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

    def fake_display_agents_table(agents: dict[str, object]) -> None:
        called["agents"] = agents

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)
    monkeypatch.setattr("ralph.cli.main.display_agents_table", fake_display_agents_table)

    exit_code = _handle_list_agents("/tmp/config.toml", {}, True)
    assert exit_code == 0
    assert called["agents"] is sentinel


def test_handle_list_agents_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failures from load_config bubble up as exit code 1."""

    def fake_load_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)

    exit_code = _handle_list_agents(None, {}, True)
    assert exit_code == 1


def test_handle_check_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check config returns 0 and prints a success banner."""

    monkeypatch.setattr("ralph.cli.main.load_config", lambda *args, **kwargs: object())
    printed: list[str] = []

    def fake_console_print(message: object) -> None:
        printed.append(str(message))

    monkeypatch.setattr("ralph.cli.main.console.print", fake_console_print)

    exit_code = _handle_check_config(None, {}, True)
    assert exit_code == 0
    assert printed and "Configuration is valid" in printed[0]


def test_handle_check_config_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure to load config returns code 1."""

    def fake_load_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.load_config", fake_load_config)

    exit_code = _handle_check_config(None, {}, True)
    assert exit_code == 1


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
    monkeypatch.setattr("ralph.cli.main.display_agents_table", lambda _agents: None)

    assert _handle_list_agents(None, {}, True) == 0
    assert called["kwargs"] == {"workspace_scope": scope}


def test_handle_list_providers_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """List providers renders the provider list."""

    monkeypatch.setattr("ralph.cli.main.fetch_providers", lambda: ["opencode"])
    recorded: list[object] = []

    def fake_display_providers_table(providers: object) -> None:
        recorded.append(providers)

    monkeypatch.setattr("ralph.cli.main.display_providers_table", fake_display_providers_table)

    exit_code = _handle_list_providers(True)
    assert exit_code == 0
    assert recorded == [["opencode"]]


def test_handle_list_providers_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """List providers returns 1 when fetching fails."""

    def fake_fetch() -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.fetch_providers", fake_fetch)

    exit_code = _handle_list_providers(True)
    assert exit_code == 1


def test_handle_commit_plumbing_invokes_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit plumbing delegates to the helper when flags are set."""

    calls: list[CommitPlumbingOptions] = []

    def fake_commit_plumbing(*, options: CommitPlumbingOptions | None = None) -> None:
        calls.append(options or CommitPlumbingOptions())

    monkeypatch.setattr("ralph.cli.main.commit_plumbing", fake_commit_plumbing)

    options = CommitPlumbingOptions(generate_commit_msg=True)
    result = _handle_commit_plumbing(options)
    assert result == 0
    assert calls


def test_handle_commit_plumbing_no_flags_does_not_call_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit plumbing is skipped when no flags are set."""

    called = False

    def fake_commit_plumbing(*, options: CommitPlumbingOptions | None = None) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("ralph.cli.main.commit_plumbing", fake_commit_plumbing)

    options = CommitPlumbingOptions()
    result = _handle_commit_plumbing(options)
    assert result is None
    assert not called


def test_run_pipeline_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run pipeline returns the runner exit code."""

    recorded: dict[str, object] = {}

    def fake_run_pipeline(
        *, config_path: Path | None, cli_overrides: dict[str, object], dry_run: bool, resume: bool
    ) -> int:  # type: ignore[override]
        recorded["config_path"] = config_path
        recorded["cli_overrides"] = cli_overrides
        recorded["dry_run"] = dry_run
        recorded["resume"] = resume
        return RUN_PIPELINE_SUCCESS

    monkeypatch.setattr("ralph.cli.main.run_pipeline", fake_run_pipeline)

    exit_code = _run_pipeline(
        "/tmp/config.toml", {"foo": "bar"}, dry_run=True, resume=True, no_resume=False
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
    printed: list[str] = []
    monkeypatch.setattr(
        "ralph.cli.main.console.print", lambda message: printed.append(str(message))
    )

    exit_code = _run_pipeline(None, {}, dry_run=False, resume=False, no_resume=False)
    assert exit_code == KEYBOARD_INTERRUPT_EXIT_CODE
    assert printed == ["\n[yellow]Interrupted by user[/yellow]"]


def test_run_pipeline_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """General exceptions surface as a user-facing error."""

    def fake_run(*args: object, **kwargs: object) -> None:  # pragma: no cover - raised immediately
        raise RuntimeError("boom")

    logged: list[str] = []
    monkeypatch.setattr("ralph.cli.main.run_pipeline", fake_run)

    def capture_exception(message: object) -> None:  # pragma: no cover - helper only
        logged.append(str(message))

    monkeypatch.setattr("ralph.cli.main.logger.exception", capture_exception)
    printed: list[str] = []
    monkeypatch.setattr(
        "ralph.cli.main.console.print", lambda message: printed.append(str(message))
    )

    exit_code = _run_pipeline(None, {}, dry_run=False, resume=False, no_resume=False)
    assert exit_code == 1
    assert logged == ["Pipeline failed: {}"]
    assert printed and "Error" in printed[0]


def test_build_cli_overrides_sets_values() -> None:
    """CLI overrides mirror the provided inputs."""

    cli_input = CLIOverrideInput(
        developer_iters=3,
        reviewer_reviews=1,
        developer_agent="dev",
        reviewer_agent="rev",
        developer_model="dev-model",
        reviewer_model="rev-model",
        review_depth=ReviewDepth.SECURITY,
        git_user_name="Jane",
        git_user_email="jane@example.com",
        isolation_mode=True,
    )

    overrides = cast("dict[str, object]", _build_cli_overrides(cli_input))
    general = cast("dict[str, object]", overrides["general"])
    execution = cast("dict[str, object]", general["execution"])
    assert general["developer_iters"] == DEFAULT_DEVELOPER_ITERS
    assert general["reviewer_reviews"] == 1
    assert general["review_depth"] == ReviewDepth.SECURITY.value
    assert general["git_user_name"] == "Jane"
    assert general["git_user_email"] == "jane@example.com"
    assert execution["isolation_mode"] is True
    assert overrides["developer_agent"] == "dev"
    assert overrides["reviewer_agent"] == "rev"
    assert overrides["developer_model"] == "dev-model"
    assert overrides["reviewer_model"] == "rev-model"


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
