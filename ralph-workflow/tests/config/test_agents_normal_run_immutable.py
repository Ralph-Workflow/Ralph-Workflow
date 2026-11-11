"""Normal Ralph commands never materialize the user-global agents file."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import ralph.cli.main as cli_main
from ralph.cli.commands import init as init_module
from ralph.cli.main import app
from ralph.config.bootstrap import ensure_global_agents_config
from ralph.config.loader import load_config
from ralph.display.line_sanitizer import strip_terminal_control

if TYPE_CHECKING:
    import pytest

AGENTS_FILENAME = "ralph-workflow-agents.toml"


def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_home = tmp_path / "config"
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
    monkeypatch.chdir(workspace)
    return config_home / AGENTS_FILENAME


def _assert_untouched(target: Path) -> None:
    assert not target.exists()
    assert not target.with_name(f"{target.name}.bak").exists()
    assert not (target.parent / ".git").exists()


def test_plain_load_config_leaves_missing_agents_file_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _isolate_config(monkeypatch, tmp_path)
    local_config = tmp_path / "local.toml"
    local_config.write_text("", encoding="utf-8")

    load_config(local_config, {})

    _assert_untouched(target)


def test_diagnose_leaves_missing_agents_file_absent_and_emits_one_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_main, "diagnose_command", lambda *_args, **_kwargs: 0)

    result = CliRunner().invoke(app, ["--diagnose"])

    assert result.exit_code == 0, result.output
    _assert_untouched(target)
    plain_output = strip_terminal_control(result.output)
    compact_output = "".join(plain_output.split())
    assert compact_output.count(str(target)) == 1
    expected_hint = f"Agentsconfigurationismissing:{target}.Run`ralph--init`tocreateit."
    assert compact_output.count(expected_hint) == 1


def test_ordinary_pipeline_run_leaves_missing_agents_file_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_main, "run_pipeline", lambda *_args, **_kwargs: 0)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0, result.output
    _assert_untouched(target)


def test_init_explicitly_creates_missing_agents_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _isolate_config(monkeypatch, tmp_path)

    # The explicit init route asks the shared bootstrap for the creation;
    # the default call keeps the file absent.
    assert ensure_global_agents_config().action == "skipped"
    assert not target.exists()

    result = ensure_global_agents_config(create_if_missing=True)

    assert result.action == "created"
    assert target.is_file()
    assert not target.with_name(f"{target.name}.bak").exists()


def test_init_command_wires_the_explicit_creation_flag() -> None:
    """The explicit init route requests the creation; a normal route does not."""
    init_source = inspect.getsource(init_module.init_command)
    assert "ensure_global_agents_config(create_if_missing=True)" in init_source
