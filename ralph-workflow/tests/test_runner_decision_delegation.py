"""Black-box delegation tests for runner decision consumption.

``_apply_step_integration_outcome`` and ``_handle_blocked_dispatch_recovery``
must *consume* the typed ``IntegrationAction`` / ``PhaseDecision`` returned by
the authorities, not discard them. These tests observe only the returned
``PipelineState`` — never internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from ralph.pipeline import runner as runner_module
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._pipeline_state_machine import PhaseDecision
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_dispatch_blocked_error import (
    IntegrationDispatchBlockedError,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

DEFAULT_POLICY_DIR = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"


def _state(**rebase_updates: Any) -> PipelineState:
    rebase = RebaseState(**rebase_updates)
    return PipelineState(phase="development", rebase=rebase)


def _scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path)


@pytest.fixture()
def policy_bundle() -> object:
    return load_policy(DEFAULT_POLICY_DIR)


_BASE_RECORD: dict[str, object] = {
    "phase": "integrating",
    "target": "main",
    "pre_feature_sha": "a" * 40,
    "pre_target_sha": "b" * 40,
}


def _write_record(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    **record_fields: Any,
) -> None:
    """Make read_record return a record with the given fields (in-memory seam)."""
    record = IntegrationRecord(**{**_BASE_RECORD, **record_fields})
    monkeypatch.setattr(runner_module, "read_record", lambda _root: record)
    # _reconcile_rebase_if_live_resolved reads the record from disk too; stub
    # it to a no-op so the in-memory record is the only input.
    monkeypatch.setattr(
        runner_module,
        "_reconcile_rebase_if_live_resolved",
        lambda _scope, rebase: rebase,
    )


def test_intervention_action_surfaces_intervention_display_state(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    # No record + retained recovery => REQUIRE_INTERVENTION (fail-closed).
    monkeypatch.setattr(runner_module, "read_record", lambda _root: None)
    state = _state(recovery_record_retained=True)
    out = runner_module._apply_step_integration_outcome(
        state, _scope(tmp_path), policy_bundle, state.rebase
    )
    assert out.rebase.operation_display_state == "intervention_required"


def test_diagnostic_intervention_required_surfaces_intervention(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    _write_record(tmp_path, monkeypatch, diagnostic_intervention_required=True)
    state = _state()
    out = runner_module._apply_step_integration_outcome(
        state, _scope(tmp_path), policy_bundle, state.rebase
    )
    assert out.rebase.operation_display_state == "intervention_required"


def test_recoverable_action_blocks_display_without_intervention(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    # Active operation => RESOLVE_ACTIVE_OPERATION => BLOCK_FOR_RECOVERY,
    # NOT intervention.
    _write_record(
        tmp_path,
        monkeypatch,
        phase="integrating",
        target="main",
        pre_feature_sha="a",
        pre_target_sha="b",
        resolving_rebase=True,
    )
    state = _state()
    out = runner_module._apply_step_integration_outcome(
        state, _scope(tmp_path), policy_bundle, state.rebase
    )
    assert out.rebase.operation_display_state == "waiting"
    assert out.rebase.operation_display_state != "intervention_required"


def test_no_outcome_returns_state_unchanged(tmp_path: Path) -> None:
    state = _state()
    out = runner_module._apply_step_integration_outcome(
        state, _scope(tmp_path), {"pipeline": None}, None
    )
    assert out is state


def _fake_verdict() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(reasons=["blocked"], status="EXHAUSTED")


def test_blocked_dispatch_unknown_action_fails_closed_to_retention(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    # No record at all => next_integration_action => REQUIRE_INTERVENTION,
    # an unknown-ish/fail-closed vocabulary value; recovery must retain.
    monkeypatch.setattr(runner_module, "read_record", lambda _root: None)
    timed = _state()
    recovered = _state()
    out = runner_module._handle_blocked_dispatch_recovery(
        recovered,
        timed,
        _scope(tmp_path),
        policy_bundle.pipeline,
        IntegrationDispatchBlockedError("development", _fake_verdict()),
    )
    assert out.rebase.recovery_record_retained is True


def test_blocked_dispatch_non_blocked_exc_is_noop(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    monkeypatch.setattr(runner_module, "read_record", lambda _root: None)
    recovered = _state()
    out = runner_module._handle_blocked_dispatch_recovery(
        recovered,
        _state(),
        _scope(tmp_path),
        policy_bundle.pipeline,
        RuntimeError("not a dispatch block"),
    )
    assert out is recovered
    assert out.rebase.recovery_record_retained is False


def test_apply_step_does_not_write_last_decision(
    tmp_path: Path, monkeypatch: MonkeyPatch, policy_bundle: object
) -> None:
    # The reducer is the single last_decision writer; the runner consumes the
    # decision by acting on display state, not by persisting the decision.
    _write_record(tmp_path, monkeypatch, diagnostic_intervention_required=True)
    state = _state()
    out = runner_module._apply_step_integration_outcome(
        state, _scope(tmp_path), policy_bundle, state.rebase
    )
    assert out.rebase.last_decision is None


def test_closed_vocabulary_constants_are_unchanged() -> None:
    assert IntegrationAction.REQUIRE_INTERVENTION.value == "require_intervention"
    assert PhaseDecision.BLOCK_FOR_RECOVERY.value == "block_for_recovery"
    assert PhaseDecision.REPORT_INTERVENTION_REQUIRED.value == ("report_intervention_required")
