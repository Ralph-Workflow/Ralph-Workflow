"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


class _CaptureConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        self.lines.append(" ".join(str(arg) for arg in args))


def _fake_config(developer_iters: int = 1, reviewer_reviews: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        general=SimpleNamespace(developer_iters=developer_iters, reviewer_reviews=reviewer_reviews)
    )


def test_run_pipeline_load_config_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[str] = []

    def raise_config(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - raises instantly
        raise RuntimeError("boom")

    def capture_error(message: str, *args: object, **kwargs: object) -> None:
        errors.append(message)

    monkeypatch.setattr(run_module, "load_config", raise_config)
    monkeypatch.setattr(run_module.logger, "error", capture_error)
    assert run_module.run_pipeline() == 1
    assert errors == ["Failed to load configuration: {}"]


def test_run_pipeline_resume_without_checkpoint_prints_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()
    monkeypatch.setattr(run_module, "console", console)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: None)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline(resume=True) == 0
    assert any("No checkpoint found to resume from" in line for line in console.lines)


def test_run_pipeline_dry_run_reports_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _fake_config(developer_iters=4, reviewer_reviews=2)
    state = PipelineState(phase="review")

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: state)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    console = _CaptureConsole()
    monkeypatch.setattr(run_module, "console", console)

    assert run_module.run_pipeline(dry_run=True, resume=True) == 0
    assert console.lines[0] == "[cyan]Dry run mode[/cyan]"
    assert "Phase: review" in console.lines[1]
    assert "Iterations: 4" in console.lines[2]
    assert "Review passes: 2" in console.lines[3]


def test_run_pipeline_runner_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()
    logged: list[str] = []

    monkeypatch.setattr(run_module, "console", console)
    monkeypatch.setattr(
        run_module.logger, "error", lambda message, *args, **kwargs: logged.append(message)
    )
    monkeypatch.setattr(run_module, "_run_func", None)

    assert run_module.run_pipeline() == 1
    assert any("Pipeline runner is unavailable" in line for line in console.lines)
    assert logged and logged[-1] == "Pipeline runner is unavailable"


def test_run_pipeline_runner_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the runner raises an unexpected exception, run_pipeline returns 1 and
    shows an error on the console. This validates the observable behavior of the
    outer exception handler; it does not assert which specific logger method is called,
    since that is an implementation detail."""
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()

    def raising_runner(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - raises intentionally
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "_run_func", raising_runner)
    # Suppress any logging to avoid noise in test output
    monkeypatch.setattr(run_module.logger, "critical", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_module, "console", console)

    assert run_module.run_pipeline() == 1
    assert any("Pipeline failed" in line for line in console.lines)


def test_run_pipeline_injects_workspace_scope_when_config_path_is_implicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    scope = WorkspaceScope("/tmp/worktree")

    def fake_load_config(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["kwargs"] = kwargs
        return _fake_config()

    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(run_module, "load_config", fake_load_config)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline() == 0
    assert captured["kwargs"] == {"workspace_scope": scope}
