"""CLI behavior tests for the process-scoped skills view fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    import pytest

_PROCESS_VIEW_ENV = "RALPH_SKILLS_PROCESS_DIR"


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    expected_process_dir: Path | None,
) -> tuple[int, list[str | None]]:
    """Run the public CLI command with only its public collaborators replaced."""
    observed_process_dirs: list[str | None] = []

    def _fake_load_config(*_args: object, **_kwargs: object) -> UnifiedConfig:
        return UnifiedConfig()

    def _fake_runner(_config: object, _initial_state: object) -> int:
        observed_process_dirs.append(os.environ.get(_PROCESS_VIEW_ENV))
        return 0

    class _ProcessView:
        def __enter__(self) -> Path:
            if expected_process_dir is None:
                raise AssertionError("the CLI entered a process view unexpectedly")
            monkeypatch.setenv(_PROCESS_VIEW_ENV, str(expected_process_dir))
            return expected_process_dir

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            monkeypatch.delenv(_PROCESS_VIEW_ENV, raising=False)
            return False

    monkeypatch.setattr(
        run_module,
        "load_config",
        _fake_load_config,
    )
    monkeypatch.setattr(run_module, "protect_broker_secret", lambda: None)
    monkeypatch.setattr(run_module, "SkillsProcessView", _ProcessView)
    monkeypatch.setattr(run_module.state, "run_func", _fake_runner)
    result = run_module.run_pipeline(
        request=run_module.RunPipelineRequest(
            config_path=tmp_path / "ralph.toml",
        ),
    )
    return result, observed_process_dirs


def test_cli_skips_process_view_when_machine_global_skills_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A complete machine-global skill install needs no process-scoped view."""
    monkeypatch.setattr(run_module, "has_machine_global_skills", lambda: True)

    result, observed_process_dirs = _run_cli(
        monkeypatch,
        tmp_path,
        expected_process_dir=None,
    )

    assert result == 0
    assert observed_process_dirs == [None]


def test_cli_enters_process_view_when_machine_global_skills_are_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without machine-global skills, the pipeline sees the view's target."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    target = tmp_path / "process-view"

    result, observed_process_dirs = _run_cli(
        monkeypatch,
        tmp_path,
        expected_process_dir=target,
    )

    assert result == 0
    assert observed_process_dirs == [str(target)]


def test_cli_enters_process_view_when_machine_global_skills_are_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A partial machine-global install falls back to the process-scoped view."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    skills_dir = tmp_path / ".claude" / "skills" / "using-superpowers"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# using-superpowers\n", encoding="utf-8")
    target = tmp_path / "process-view"

    result, observed_process_dirs = _run_cli(
        monkeypatch,
        tmp_path,
        expected_process_dir=target,
    )

    assert result == 0
    assert observed_process_dirs == [str(target)]
