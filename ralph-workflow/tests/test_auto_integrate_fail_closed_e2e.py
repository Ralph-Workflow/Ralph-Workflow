"""Fast fail-closed contracts for auto-integration and crash recovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_UNKNOWN
from ralph.git.operations import GitOperationError
from ralph.pipeline import auto_integrate
from ralph.pipeline import auto_integrate_recovery as recovery
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
            }
        }
    )


def _record(phase: str) -> object:
    return SimpleNamespace(
        phase=phase,
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="a" * 40,
        resolving_rebase=False,
    )


def _stub_recovery(
    monkeypatch: MonkeyPatch, *, phase: str
) -> list[Path]:
    cleared: list[Path] = []
    monkeypatch.setattr(recovery, "_read_record", lambda _root: _record(phase))
    monkeypatch.setattr(recovery, "_clear_record", cleared.append)
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(recovery, "abort_rebase", lambda **_kwargs: None)
    monkeypatch.setattr(recovery, "reset_hard", lambda _root, _sha: None)
    return cleared


def test_head_read_failure_names_the_git_operation(
    monkeypatch: MonkeyPatch,
) -> None:
    """A failed HEAD read is an operator-visible skip, not an opaque failure."""
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (
            Path("/workspace"),
            "feature",
            "main",
            "refresh-disabled",
        ),
    )
    monkeypatch.setattr(auto_integrate, "branch_sha", lambda _root, _target: "base")

    def _failing_head(_root: Path) -> str:
        raise GitOperationError("get_head_sha", "simulated HEAD read failure")

    monkeypatch.setattr(auto_integrate, "get_head_sha", _failing_head)

    outcome = auto_integrate.auto_integrate_after_commit(
        _config(),
        WorkspaceScope(Path("/workspace")),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "HEAD read failed" in (outcome.last_reason or "")
    assert "Git get_head_sha failed" in (outcome.last_reason or "")
    assert "simulated HEAD read failure" in (outcome.last_reason or "")
    assert outcome.last_target == "main"
    assert outcome.fast_forwarded is False


def test_unreadable_merge_state_retains_integrating_record(
    monkeypatch: MonkeyPatch,
) -> None:
    """Unknown merge state cannot be treated as a clean recovery."""
    cleared = _stub_recovery(monkeypatch, phase="integrating")
    states = iter((MERGE_STATE_UNKNOWN, MERGE_STATE_UNKNOWN, MERGE_STATE_UNKNOWN))
    monkeypatch.setattr(recovery, "merge_state", lambda _root: next(states))
    monkeypatch.setattr(recovery, "abort_merge", lambda _root: False)

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(Path("/workspace"))
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.recovery_record_retained is True
    assert "retained for retry" in (outcome.last_reason or "")
    assert cleared == []


def test_unreadable_merge_state_blocks_integrated_fast_forward(
    monkeypatch: MonkeyPatch,
) -> None:
    """The integrated phase also retains its record before any landing."""
    cleared = _stub_recovery(monkeypatch, phase="integrated")
    states = iter((MERGE_STATE_UNKNOWN, MERGE_STATE_UNKNOWN, MERGE_STATE_UNKNOWN))
    monkeypatch.setattr(recovery, "merge_state", lambda _root: next(states))
    monkeypatch.setattr(recovery, "abort_merge", lambda _root: False)
    fast_forward = []
    monkeypatch.setattr(
        recovery,
        "_continue_fast_forward_from_record",
        lambda *_args, **_kwargs: fast_forward.append(True),
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(Path("/workspace"))
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.recovery_record_retained is True
    assert cleared == []
    assert fast_forward == []
