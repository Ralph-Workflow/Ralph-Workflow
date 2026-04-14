"""Focused CLI command tests for commit, diagnose, init, and option helpers."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import rich_click as click
from rich.console import Console

from ralph.cli import options as options_module
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands import diagnose as diagnose_module
from ralph.cli.commands import init as init_module
from ralph.config.enums import ReviewDepth, Verbosity
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    import pytest


def _attach_console(monkeypatch: pytest.MonkeyPatch, module: object) -> StringIO:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    monkeypatch.setattr(module, "console", console)
    return stream


def _simple_config() -> SimpleNamespace:
    return SimpleNamespace(
        general=SimpleNamespace(
            git_user_name="user",
            git_user_email="user@example.com",
        )
    )


def test_commit_plumbing_reports_missing_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)

    def raise_repo() -> Path:
        raise RuntimeError("no repo")

    monkeypatch.setattr(commit_module, "find_repo_root", raise_repo)
    commit_module.commit_plumbing()
    assert "Not in a git repository" in stream.getvalue()


def test_commit_plumbing_reports_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: Path("/tmp"))

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(commit_module, "load_config", raise_config)
    commit_module.commit_plumbing()
    assert "Error loading config" in stream.getvalue()


def test_commit_plumbing_prints_no_staged_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: Path("/tmp"))
    monkeypatch.setattr(commit_module, "load_config", lambda *_: _simple_config())
    monkeypatch.setattr(commit_module, "has_staged_changes", lambda root: False)

    commit_module.commit_plumbing()
    assert "No staged changes to commit" in stream.getvalue()


def test_handle_show_or_generate_displays_staged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    files = [f"file_{i}" for i in range(commit_module._MAX_DISPLAY_FILES + 2)]
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: files)
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda staged, root: "auto msg")

    def fail_commit(*args: object, **kwargs: object) -> Path:
        raise AssertionError("Should not commit when apply=False")

    monkeypatch.setattr(commit_module, "create_commit", fail_commit)

    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=False,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    output = stream.getvalue()
    assert "Staged files" in output
    assert "... and 2 more" in output
    assert "auto msg" in output
    assert "Generated commit message" in output


def test_handle_show_or_generate_applies_commit_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: ["src/app.py"])
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda staged, root: "auto msg")
    recorded: list[str] = []

    def fake_create(
        repo_root: Path, message: str, author_name: str | None, author_email: str | None
    ) -> str:
        recorded.append(repo_root.as_posix())
        return "deadbeef1234"

    monkeypatch.setattr(commit_module, "create_commit", fake_create)
    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=True,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    assert recorded
    output = stream.getvalue()
    assert "Created commit" in output
    assert "deadbeef" in output


def test_handle_show_or_generate_applies_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: ["src/app.py"])
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda staged, root: "auto msg")

    def raise_commit(*args: object, **kwargs: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(commit_module, "create_commit", raise_commit)
    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=True,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    assert "Commit failed" in stream.getvalue()


def test_generate_commit_message_synthesizes_sections() -> None:
    assert commit_module._generate_commit_message([], Path("/tmp")) == "Update files"
    message = commit_module._generate_commit_message(
        ["src/one.py", "tests/two.py", "docs/three.md"], Path("/tmp")
    )
    assert "Update 2 files" in message
    assert "Modify 1 file" in message


def test_check_git_repo_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_repo() -> Path:
        raise RuntimeError("missing")

    monkeypatch.setattr(diagnose_module, "find_repo_root", raise_repo)
    diagnose_module._check_git_repo()
    assert "Git Repository" in stream.getvalue()
    assert "Error" in stream.getvalue()


def test_check_git_repo_clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.setattr(diagnose_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(diagnose_module, "is_repo_clean", lambda root: True)

    diagnose_module._check_git_repo()
    output = stream.getvalue()
    assert "Working tree" in output
    assert "Clean" in output


def test_check_configuration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    config = SimpleNamespace(
        general=SimpleNamespace(
            developer_iters=4,
            reviewer_reviews=2,
            review_depth=ReviewDepth.SECURITY,
            workflow=SimpleNamespace(checkpoint_enabled=False),
        )
    )
    monkeypatch.setattr(diagnose_module, "load_config", lambda *_: config)
    diagnose_module._check_configuration(None, {})
    output = stream.getvalue()
    assert "Config loaded" in output
    assert "Developer iters" in output


def test_check_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module._check_configuration(None, {})
    assert "Error" in stream.getvalue()


def test_check_agents_no_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.setattr(diagnose_module, "load_config", lambda *_: SimpleNamespace(agents={}))
    diagnose_module._check_agents({})
    assert "No agents configured" in stream.getvalue()


def test_check_agents_with_configured_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    agent = AgentConfig(cmd="agent", can_commit=True)
    monkeypatch.setattr(
        diagnose_module, "load_config", lambda *_: SimpleNamespace(agents={"alpha": agent})
    )
    diagnose_module._check_agents({})
    output = stream.getvalue()
    assert "Configured" in output
    assert "alpha" in output


def test_check_agents_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module._check_agents({})
    assert "Error" in stream.getvalue()


def test_check_workspace_files_reports_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("prompt")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text("config")

    diagnose_module._check_workspace_files()
    output = stream.getvalue()
    assert "PROMPT.md" in output
    assert "Exists" in output
    assert "checkpoint" in output.lower()
    assert "Not found" in output


def test_init_command_creates_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    init_module.init_command(path=str(tmp_path))
    assert (tmp_path / "PROMPT.md").exists()
    assert (tmp_path / ".agent" / "ralph-workflow.toml").exists()
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


def test_init_command_keeps_existing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("existing")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    config = agent_dir / "ralph-workflow.toml"
    config.write_text("existing config")

    init_module.init_command(path=str(tmp_path))
    assert prompt.read_text() == "existing"
    assert config.read_text() == "existing config"
    assert "Created" not in stream.getvalue()


def test_init_command_custom_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    custom = tmp_path / "custom" / "custom.toml"
    custom.parent.mkdir()
    init_module.init_command(path=str(tmp_path), config_path=custom)
    assert custom.exists()
    assert "Created" in stream.getvalue()


def test_verbosity_option_processes_values() -> None:
    ctx = click.Context(click.Command("test"))
    option = options_module.VerbosityOption(param_decls=["--verbosity"])
    assert option.process_value(ctx, None) == Verbosity.NORMAL
    assert option.process_value(ctx, Verbosity.FULL) == Verbosity.FULL
    assert option.process_value(ctx, "debug") == Verbosity.DEBUG
    assert option.process_value(ctx, "3") == Verbosity.FULL
    assert option.process_value(ctx, "20") == Verbosity.DEBUG
    assert option.process_value(ctx, "nonsense") == Verbosity.NORMAL


def test_display_tables_render() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    agent = AgentConfig(cmd="agent", can_commit=False)
    options_module.display_agents_table({"alpha": agent}, console=console)
    rendered = buffer.getvalue()
    assert "Configured" in rendered
    assert "Agents" in rendered
    assert "alpha" in rendered
    assert "no" in rendered

    buffer.truncate(0)
    buffer.seek(0)
    options_module.display_providers_table(["opencode"], console=console)
    rendered = buffer.getvalue()
    assert "Available" in rendered
    assert "Providers" in rendered
    assert "opencode" in rendered
