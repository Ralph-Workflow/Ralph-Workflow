"""All-mock contracts for the post-commit auto-integrate runner seam."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import pytest

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PhaseDefinition
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope


@dataclass
class _IntegrationSpy:
    """Typed replacement for the integration boundary."""

    result: RebaseState | None = None
    error: Exception | None = None
    calls: int = 0

    def __call__(
        self,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
        _rebase_state: RebaseState,
    ) -> RebaseState | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _commit_phase() -> PhaseDefinition:
    """Provide the one phase attribute the narrow seam consumes."""
    return cast("PhaseDefinition", SimpleNamespace(role="commit"))


def _state() -> PipelineState:
    """Provide the one state attribute the narrow seam consumes."""
    return cast("PipelineState", SimpleNamespace(rebase=RebaseState()))


def _config() -> UnifiedConfig:
    return cast("UnifiedConfig", object())


def _display() -> ParallelDisplay:
    return cast("ParallelDisplay", object())


def _run_commit_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spy: _IntegrationSpy,
    event: PipelineEvent,
) -> RebaseState | None:
    def clear_baseline(_root: Path) -> None:
        return None

    monkeypatch.setattr(
        "ralph.pipeline.runner.clear_cycle_baseline",
        cast("Callable[[Path], None]", clear_baseline),
    )
    monkeypatch.setattr("ralph.pipeline.runner.auto_integrate_after_commit", spy)
    def log_outcome(_display: ParallelDisplay, _outcome: RebaseState) -> None:
        return None

    monkeypatch.setattr(
        "ralph.pipeline.runner._log_auto_integrate_outcome",
        cast("Callable[[ParallelDisplay, RebaseState], None]", log_outcome),
    )
    return runner_module._maybe_auto_integrate(
        effect=CommitEffect(message_file="/dev/null"),
        event=event,
        commit_phase_def=_commit_phase(),
        config=_config(),
        workspace_scope=WorkspaceScope(tmp_path),
        state=_state(),
        display=_display(),
    )


def test_commit_success_threads_auto_integrate_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan step 5: COMMIT_SUCCESS invokes the all-mock integration boundary."""
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    spy = _IntegrationSpy(result=outcome)

    assert _run_commit_seam(monkeypatch, tmp_path, spy, PipelineEvent.COMMIT_SUCCESS) is outcome
    assert spy.calls == 1


def test_commit_skipped_does_not_invoke_auto_integrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan step 5: COMMIT_SKIPPED clears the baseline but does not integrate."""
    spy = _IntegrationSpy(result=RebaseState(last_action="rebased"))

    assert _run_commit_seam(monkeypatch, tmp_path, spy, PipelineEvent.COMMIT_SKIPPED) is None
    assert spy.calls == 0


def test_commit_conflict_outcome_does_not_halt_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prompt AC-07, plan step 6: a conflict outcome crosses the seam unchanged."""
    conflict = RebaseState(
        last_action="conflict",
        last_reason="rebase and endpoint merge both conflicted",
        last_target="main",
        fast_forwarded=False,
    )
    spy = _IntegrationSpy(result=conflict)

    assert _run_commit_seam(monkeypatch, tmp_path, spy, PipelineEvent.COMMIT_SUCCESS) is conflict
    assert spy.calls == 1


def test_auto_integrate_exception_does_not_halt_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prompt AC-07, plan step 6: the defensive exception path returns None."""
    spy = _IntegrationSpy(error=RuntimeError("integration blew up"))

    assert _run_commit_seam(monkeypatch, tmp_path, spy, PipelineEvent.COMMIT_SUCCESS) is None
    assert spy.calls == 1
