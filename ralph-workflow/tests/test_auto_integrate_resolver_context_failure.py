from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import auto_integrate_agent as agent_module
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, RebaseStopResolver
from ralph.pipeline.resolution_outcome import RetryLaterOutcome

if TYPE_CHECKING:
    import pytest


class _UnreadableConfigError(RuntimeError):
    pass


def _build_resolver(display: MagicMock) -> RebaseStopResolver:
    return agent_module.build_agent_rebase_stop_resolver(
        policy_bundle=MagicMock(name="policy_bundle"),
        registry=MagicMock(name="registry"),
        display=display,
        config=MagicMock(name="config"),
        pipeline_deps=MagicMock(name="pipeline_deps"),
        workspace_scope=MagicMock(name="workspace_scope"),
    )


def _stop() -> RebaseStop:
    return RebaseStop(
        sha="deadbeef",
        subject="replay the feature commit",
        conflicted_files=("src/omega.py",),
        stop_index=1,
        stop_cap=5,
    )


def test_unreadable_target_config_declines_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(_root: Path) -> object:
        raise _UnreadableConfigError("Unclosed array (at line 75, column 1)")

    monkeypatch.setattr(agent_module, "workspace_context", _raise)
    resolver = _build_resolver(MagicMock(name="display"))

    outcome = resolver(tmp_path, "main", _stop())
    assert isinstance(outcome, RetryLaterOutcome)
    assert outcome.succeeded is False


def test_unreadable_target_config_is_reported_to_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    emitted: list[str] = []

    def _raise(_root: Path) -> object:
        raise _UnreadableConfigError("Unclosed array (at line 75, column 1)")

    monkeypatch.setattr(agent_module, "workspace_context", _raise)
    monkeypatch.setattr(
        agent_module,
        "emit_integration_warn_line",
        lambda _display, message: emitted.append(message),
    )
    resolver = _build_resolver(MagicMock(name="display"))

    outcome = resolver(tmp_path, "main", _stop())
    assert isinstance(outcome, RetryLaterOutcome)
    assert outcome.succeeded is False
    assert len(emitted) == 1
    assert "Unclosed array (at line 75, column 1)" in emitted[0]
