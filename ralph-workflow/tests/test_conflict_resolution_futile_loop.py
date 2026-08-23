"""B7-B15: a broken tool surface must fail the attempt on evidence, not a clock."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    classify_ralph_origin_fault,
    ralph_origin_counts_as_liveness,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.status import ResolutionStatusReporter
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["a.py", "b.py", "c.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(unmerged),
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )


def test_transport_loop_detected_is_a_typed_attempt_failure() -> None:
    reason = classify_ralph_origin_fault("HTTP 503: transport_loop_detected")
    assert reason is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    assert ralph_origin_counts_as_liveness("transport_loop_detected") is False


def test_supervision_relay_error_is_a_typed_attempt_failure() -> None:
    payload = "SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: timed out"
    reason = classify_ralph_origin_fault(payload)
    assert reason is ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    assert ralph_origin_counts_as_liveness(payload) is False


def test_dead_tool_surface_fails_fast_and_hands_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    session = ResolutionSession()
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "primary":
            session.terminal_reason = classify_ralph_origin_fault("transport_loop_detected")
            session.charge_conflict_budget = False
            session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)
            return False
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=UnifiedConfig.model_validate({"general": {}}),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            session=session,
        )
        is False
    )
    assert called[0] == "primary"
    assert "fallback" in called
    assert called.count("primary") == 1
    assert session.terminal_reason is ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    assert session.charge_conflict_budget is False


def test_known_dead_surface_is_not_reentered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    session = ResolutionSession(dead_tool_surfaces=("primary",))
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=session,
    )
    assert called == ["fallback"]


def test_status_does_not_report_health_from_ralph_fault_text() -> None:
    reporter = ResolutionStatusReporter(
        display=None,
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=None,
        stop_cap=None,
        clock=lambda: 1.0,
        interval_seconds=0.0,
        started_at=0.0,
        unresolved_paths=tuple(_CONFLICTED),
        agent_name="primary",
    )

    event = type("_Event", (), {"diagnostic": {
        "last_activity_kind": "transport_loop_detected",
        "last_activity_age_seconds": 0.56,
    }})()
    reporter.observe(event)
    assert reporter._last_emitted_at is None
