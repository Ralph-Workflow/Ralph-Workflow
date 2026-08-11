"""Contract tests for the explicitly on-demand visual judgement tier."""

from __future__ import annotations

import inspect

from ralph.testing import audit_repo_structure
from ralph.visual import judgement_tier
from ralph.visual.judgement_tier import (
    JudgementTier,
    OnDemandJudgementDeps,
    OnDemandJudgementResult,
    resolve_judgement_tier,
    run_on_demand_judgement,
)


def test_on_demand_contracts_pass_the_structure_audit() -> None:
    source = inspect.getsource(judgement_tier)
    top_level_classes, nested_classes, _ = audit_repo_structure._scan_structure(
        source, tuple(source.splitlines())
    )

    assert top_level_classes == ("JudgementTier",)
    assert nested_classes == ()


def test_deterministic_tier_is_the_only_blocking_tier() -> None:
    assert JudgementTier.DETERMINISTIC.is_blocking is True
    assert JudgementTier.ON_DEMAND.is_blocking is False


def test_on_demand_tier_must_be_selected_explicitly() -> None:
    assert resolve_judgement_tier(None) is JudgementTier.DETERMINISTIC
    assert resolve_judgement_tier("on-demand") is JudgementTier.ON_DEMAND


def test_visual_judgement_cli_dispatches_fixture_capture_sets_to_vision_agent_and_submits_on_demand_verdict() -> None:
    """S-5: retained/fresh evidence is delegated then checked after submission."""
    dispatched: list[object] = []
    validated: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = []
    deps = OnDemandJudgementDeps(
        load_retained_capture=lambda target: (
            ("ralph://media/before",) if target == "fixture-ui" else ()
        ),
        load_fresh_capture=lambda target: (
            ("ralph://media/after",) if target == "fixture-ui" else ()
        ),
        delegated_agent_id=lambda: "vision-verdict-1",
        invoke_vision=lambda request: dispatched.append(request) or "verdict-1",
        validate_submission=lambda verdict_id, evidence, agent_id: validated.append(
            (verdict_id, evidence.before_handles, evidence.after_handles, agent_id)
        )
        or True,
    )

    result = run_on_demand_judgement("fixture-ui", "Improve hierarchy", deps=deps)

    assert result == OnDemandJudgementResult(verdict_id="verdict-1", status="submitted")
    assert len(dispatched) == 1
    assert validated == [
        (
            "verdict-1",
            ("ralph://media/before",),
            ("ralph://media/after",),
            "vision-verdict-1",
        )
    ]


def test_visual_judgement_cli_reports_a_blocker_when_capture_or_delegation_is_unavailable() -> None:
    deps = OnDemandJudgementDeps(
        load_retained_capture=lambda _target: (),
        load_fresh_capture=lambda _target: ("ralph://media/after",),
        delegated_agent_id=lambda: "vision-verdict-1",
        invoke_vision=lambda _request: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        validate_submission=lambda _verdict_id, _evidence, _agent_id: False,
    )

    result = run_on_demand_judgement("fixture-ui", "Improve hierarchy", deps=deps)

    assert result.blocker == "retained capture evidence is unavailable"
