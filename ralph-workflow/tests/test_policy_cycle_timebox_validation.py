"""Validation rules that keep a cycle timebox routable.

The deadline redirect stamps `finalization_cycle_outcome` on the cycle it
finalizes, and a stamped outcome suppresses the unrecorded-outcome fallback in
post-commit routing. An outcome no route declares would therefore fall through
the route table onto the commit phase's success transition — ending the run
while its cycle budget still had room, the exact failure the timebox exists to
avoid. Both rules below reject that configuration at load time.
"""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def test_cycle_timebox_rejects_an_outcome_outside_the_vocabulary() -> None:
    """The finalization outcome is closed to the vocabulary routes match on."""
    import pydantic
    import pytest as _pytest

    from ralph.policy.models import CycleTimeboxPolicy

    with _pytest.raises(pydantic.ValidationError):
        CycleTimeboxPolicy(
            start_source="planning_analysis",
            start_entry="development",
            guarded_entry="development",
            end_entry="development_final_commit_cleanup",
            finalization_target="development_final_commit_cleanup",
            finalization_cycle_outcome="timeboxed",
        )


def test_timebox_outcome_is_checked_even_when_no_decision_declares_it() -> None:
    """The timebox's own outcome is a source of recordable outcomes in its own right.

    Isolating it matters: with the analysis decisions stripped of `failed`,
    the ONLY thing that can put `failed` on a cycle is the deadline redirect.
    If the validator stopped consulting the timebox, this policy would load
    and a timed-out cycle would fall through the route table to the terminal.
    """
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    assert pipeline.cycle_timebox is not None
    phases = {name: phase.model_dump() for name, phase in pipeline.phases.items()}
    for phase in phases.values():
        decisions = phase.get("decisions") or {}
        for route in decisions.values():
            if route.get("cycle_outcome") == "failed":
                route["cycle_outcome"] = "completed"
    completed_routes = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome != "failed"
    ]

    with _pytest.raises(pydantic.ValidationError, match="cycle outcome 'failed'"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "phases": phases,
                "post_commit_routes": completed_routes,
                "cycle_timebox": {
                    **pipeline.cycle_timebox.model_dump(),
                    "finalization_cycle_outcome": "failed",
                },
            }
        )


def test_commit_phase_with_no_declared_routes_is_not_checked() -> None:
    """A phase the route table never names keeps its plain success transition.

    The check is scoped to phases that actually declare routes; a workflow
    that routes nothing post-commit is not forced to declare outcomes.
    """
    pipeline = load_policy(_DEFAULTS_DIR).pipeline

    revalidated = type(pipeline).model_validate(
        {**pipeline.model_dump(), "post_commit_routes": []}
    )

    assert revalidated.post_commit_routes == []


def test_incomplete_routes_are_rejected_even_when_the_fallthrough_loops() -> None:
    """A non-terminal fallthrough is not a licence to leave the table incomplete.

    Falling back into the cycle re-enters it regardless of remaining budget,
    so such a run never terminates at all — the mirror image of ending early,
    and just as much a defect.
    """
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    phases = {name: phase.model_dump() for name, phase in pipeline.phases.items()}
    phases["development_final_commit"]["transitions"]["on_success"] = "planning"
    completed_routes = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome != "failed"
    ]

    with _pytest.raises(pydantic.ValidationError, match="cycle outcome 'failed'"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "phases": phases,
                "post_commit_routes": completed_routes,
            }
        )


def test_route_table_must_cover_every_outcome_a_decision_can_record() -> None:
    """An outcome a phase can record but no route matches ends the run early.

    Post-commit routing falls through to the commit phase's success
    transition when nothing matches, and for a final commit that is the
    terminal — so a cycle that recorded `failed` would report success with
    dev cycles still unspent. The recorded-outcome case has no runtime
    fallback (a verdict must never be re-routed as a different verdict), so
    the incomplete table is rejected at load instead.
    """
    import pydantic
    import pytest as _pytest

    bundle = load_policy(_DEFAULTS_DIR)
    pipeline = bundle.pipeline
    completed_only = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome != "failed"
    ]

    with _pytest.raises(pydantic.ValidationError, match="failed"):
        type(pipeline).model_validate(
            {**pipeline.model_dump(), "post_commit_routes": completed_only}
        )


def test_decision_outcomes_are_checked_without_a_cycle_timebox() -> None:
    """The route-coverage rule is about recorded verdicts, not about the timebox.

    A workflow that declares no `[cycle_timebox]` still records `failed` on
    its analysis decisions, and an uncovered route table would end its runs in
    the success terminal with budget left.
    """
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    completed_routes = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome != "failed"
    ]

    with _pytest.raises(pydantic.ValidationError, match="cycle outcome 'failed'"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "cycle_timebox": None,
                "post_commit_routes": completed_routes,
            }
        )


def test_decision_routed_straight_to_a_terminal_needs_no_post_commit_route() -> None:
    """An outcome that never reaches a commit phase cannot fall through one."""
    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    phases = {name: phase.model_dump() for name, phase in pipeline.phases.items()}
    for phase in phases.values():
        for route in (phase.get("decisions") or {}).values():
            if route.get("cycle_outcome") == "failed":
                route["target"] = "failed_terminal"
    completed_routes = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome != "failed"
    ]

    revalidated = type(pipeline).model_validate(
        {
            **pipeline.model_dump(),
            "phases": phases,
            "cycle_timebox": None,
            "post_commit_routes": completed_routes,
        }
    )

    assert revalidated.cycle_timebox is None
