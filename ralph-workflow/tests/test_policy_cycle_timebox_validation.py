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


def test_cycle_timebox_rejects_an_outcome_no_post_commit_route_declares() -> None:
    """The timebox's own finalization outcome must be routable like any other."""
    import pydantic
    import pytest as _pytest

    bundle = load_policy(_DEFAULTS_DIR)
    pipeline = bundle.pipeline
    assert pipeline.cycle_timebox is not None
    completed_only = [
        route for route in pipeline.post_commit_routes if route.when.cycle_outcome == "completed"
    ]

    with _pytest.raises(pydantic.ValidationError, match="cycle outcome 'failed'"):
        pipeline.model_copy(
            update={"post_commit_routes": completed_only}
        ).model_validate(
            {
                **pipeline.model_dump(),
                "post_commit_routes": [route.model_dump() for route in completed_only],
                "cycle_timebox": {
                    **pipeline.cycle_timebox.model_dump(),
                    "finalization_cycle_outcome": "failed",
                },
            }
        )


def test_finalization_outcome_accepted_when_the_fallthrough_is_not_terminal() -> None:
    """Declaring routes for only one outcome is legal when falling through continues.

    `resolve_post_commit_phase` deliberately lets a commit phase with no
    matching route follow its `on_success` transition. When that transition is
    another cycle rather than a terminal, an outcome no route names costs
    nothing — rejecting it would forbid a working configuration.
    """
    bundle = load_policy(_DEFAULTS_DIR)
    pipeline = bundle.pipeline
    assert pipeline.cycle_timebox is not None
    failed_only = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if route.when.cycle_outcome == "failed"
    ]
    phases = {name: phase.model_dump() for name, phase in pipeline.phases.items()}
    phases["development_final_commit"]["transitions"]["on_success"] = "planning"

    revalidated = type(pipeline).model_validate(
        {
            **pipeline.model_dump(),
            "phases": phases,
            "post_commit_routes": failed_only,
        }
    )

    assert revalidated.cycle_timebox is not None
    assert revalidated.cycle_timebox.finalization_cycle_outcome == "completed"


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
