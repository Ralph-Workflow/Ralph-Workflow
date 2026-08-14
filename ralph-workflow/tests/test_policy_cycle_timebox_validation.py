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


def test_a_single_uncovered_pair_is_rejected() -> None:
    """Routing matches on (budget_state, outcome), so the table must cover pairs.

    A table can name every budget state and every outcome and still leave one
    combination uncovered — which falls through to the success transition and,
    for a final commit, ends the run with cycles unspent on a failed verdict.
    """
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    kept = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if not (route.when.budget_state == "remaining" and route.when.cycle_outcome == "failed")
    ]

    with _pytest.raises(pydantic.ValidationError, match="budget_state='remaining'"):
        type(pipeline).model_validate({**pipeline.model_dump(), "post_commit_routes": kept})


def test_a_wildcard_route_covers_every_outcome_for_its_budget_state() -> None:
    """Omitting `when.cycle_outcome` is how a policy says "any outcome".

    It must cover its own budget state only — a wildcard for one state used to
    switch the whole check off, so unrelated uncovered pairs loaded silently.
    """
    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    routes: list[dict[str, object]] = []
    for route in pipeline.post_commit_routes:
        entry = route.model_dump()
        if route.when.budget_state == "remaining":
            if route.when.cycle_outcome == "failed":
                continue
            entry["when"]["cycle_outcome"] = None
        routes.append(entry)

    revalidated = type(pipeline).model_validate(
        {**pipeline.model_dump(), "post_commit_routes": routes}
    )

    assert any(route.when.cycle_outcome is None for route in revalidated.post_commit_routes)


def test_spent_budget_routes_that_re_enter_the_cycle_are_rejected() -> None:
    """A run whose budget is spent has to be able to finish.

    Covering every budget state says nothing about where those routes lead.
    Routes that send a spent budget back into the cycle keep incrementing a
    counter that is already exhausted, so the run never terminates.
    """
    import pytest as _pytest

    from ralph.policy.validation import PolicyValidationError, validate_policy_completeness

    bundle = load_policy(_DEFAULTS_DIR)
    routes: list[dict[str, object]] = []
    for route in bundle.pipeline.post_commit_routes:
        entry = route.model_dump()
        if route.when.phase == "development_final_commit":
            entry["target"] = "planning"
        routes.append(entry)
    looping = type(bundle.pipeline).model_validate(
        {**bundle.pipeline.model_dump(), "post_commit_routes": routes}
    )

    with _pytest.raises(PolicyValidationError, match="re-enter the cycle forever"):
        validate_policy_completeness(bundle.model_copy(update={"pipeline": looping}))


def test_the_bundled_policy_can_finish_once_its_budget_is_spent() -> None:
    """The guard must not reject the workflow it ships with."""
    from ralph.policy.validation import validate_policy_completeness

    validate_policy_completeness(load_policy(_DEFAULTS_DIR))


def test_a_finalization_target_inside_the_cycle_is_rejected() -> None:
    """The typo this catches is one word apart from the correct phase name.

    `development_commit_cleanup` and `development_final_commit_cleanup` both
    exist in the bundled graph. Naming the intermediate one stops the clock on
    the first ordinary re-entry, and it can never re-arm — the cycle then runs
    with no deadline at all.
    """
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    assert pipeline.cycle_timebox is not None

    with _pytest.raises(pydantic.ValidationError, match="finalization_target"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "cycle_timebox": {
                    **pipeline.cycle_timebox.model_dump(),
                    "finalization_target": "development_commit_cleanup",
                },
            }
        )


def test_an_end_entry_inside_the_cycle_is_rejected() -> None:
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    assert pipeline.cycle_timebox is not None

    with _pytest.raises(pydantic.ValidationError, match="end_entry"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "cycle_timebox": {
                    **pipeline.cycle_timebox.model_dump(),
                    "end_entry": "development_commit_cleanup",
                },
            }
        )


def test_a_misspelled_timebox_field_is_rejected_rather_than_ignored() -> None:
    """A typo must fail loudly instead of silently keeping the default.

    Every field here changes how a cycle ends, so a silently dropped key is
    the worst possible failure mode: an operator who writes
    `finalisation_cycle_outcome = "failed"` to make a timed-out out-of-budget
    run end in the failure terminal gets `completed` instead, and never learns
    the setting did nothing.
    """
    import pydantic
    import pytest as _pytest

    from ralph.policy.models import CycleTimeboxPolicy

    with _pytest.raises(pydantic.ValidationError, match="finalisation_cycle_outcome"):
        CycleTimeboxPolicy(
            start_source="planning_analysis",
            start_entry="development",
            guarded_entry="development",
            end_entry="development_final_commit_cleanup",
            finalization_target="development_final_commit_cleanup",
            finalisation_cycle_outcome="failed",
        )


def test_an_unrecorded_outcome_that_loops_forever_is_rejected() -> None:
    """A cycle that reaches its final commit with no verdict must still be able to end.

    Post-commit routing matches an unrecorded outcome against the wildcard
    routes first and only then retries it as the forward outcome, so a `failed`
    route can never be the way an unrecorded cycle terminates. The validator
    pooled every route for the phase instead, and accepted a table whose
    unrecorded path routes back into the cycle on a spent budget — a run that
    never ends — because a sibling `failed` route happened to reach a terminal.
    """
    import pytest as _pytest

    from ralph.policy.models import PipelinePolicy
    from ralph.policy.validation._api import validate_policy_completeness
    from ralph.policy.validation._policy_validation_error import PolicyValidationError

    bundle = load_policy(_DEFAULTS_DIR)
    raw = bundle.pipeline.model_dump()
    raw["post_commit_routes"] = [
        {**route, "when": {**route["when"], "cycle_outcome": None}, "target": "planning"}
        if route["when"]["phase"] == "development_final_commit"
        and route["when"]["budget_state"] in ("exhausted", "no_review")
        and route["when"]["cycle_outcome"] == "completed"
        else route
        for route in raw["post_commit_routes"]
    ]
    looping = bundle.model_copy(update={"pipeline": PipelinePolicy.model_validate(raw)})

    with _pytest.raises(PolicyValidationError, match="terminal"):
        validate_policy_completeness(looping)


def test_disabling_an_inherited_timebox_is_announced() -> None:
    """A custom graph that drops the deadline must not do so silently.

    The inherited default cannot describe a graph it was not written for, so it
    is disabled — and every surface that would show a deadline goes quiet with
    it, leaving an operator no way to notice their run is unbounded.
    """
    from loguru import logger

    from ralph.policy.loader import disable_incompatible_inherited_cycle_timebox

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    normalized: dict[str, object] = {
        "cycle_timebox": pipeline.cycle_timebox.model_dump(),
        # The graph renames the guarded phase, as a custom workflow would.
        "phases": {
            name: phase
            for name, phase in pipeline.phases.items()
            if name != "development"
        },
    }

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        result = disable_incompatible_inherited_cycle_timebox(normalized)
    finally:
        logger.remove(sink_id)

    assert "cycle_timebox" not in result
    assert any("no cycle deadline" in record for record in records)


def test_the_model_defaults_are_the_documented_ones() -> None:
    """A policy that omits these fields must get the documented values.

    Every existing assertion reads them back from the bundled TOML, which
    supplies both explicitly — so the model's own defaults could drift to any
    value and nothing would notice. They decide how long a cycle runs and
    whether a timed-out out-of-budget run ends in success or failure.
    """
    from ralph.policy.models import CycleTimeboxPolicy

    minimal = CycleTimeboxPolicy(
        start_source="planning_analysis",
        start_entry="development",
        guarded_entry="development",
        end_entry="development_final_commit_cleanup",
        finalization_target="development_final_commit_cleanup",
    )

    assert minimal.duration_seconds == 7200.0
    assert minimal.finalization_cycle_outcome == "completed"


def test_an_inherited_timebox_whose_start_edge_is_gone_is_disabled() -> None:
    """The edge branch had no test: both existing cases removed a phase instead.

    A graph can keep every referenced phase and still drop the transition the
    timer starts on, which leaves the deadline unable to ever arm.
    """
    from ralph.policy.loader import disable_incompatible_inherited_cycle_timebox

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    phases = dict(pipeline.phases)
    # Keep every phase; sever only the declared start edge.
    planning_analysis = phases["planning_analysis"]
    phases["planning_analysis"] = planning_analysis.model_copy(
        update={
            "transitions": planning_analysis.transitions.model_copy(
                update={"on_success": "development_final_commit_cleanup"}
            ),
            "decisions": {},
        }
    )

    result = disable_incompatible_inherited_cycle_timebox(
        {"cycle_timebox": pipeline.cycle_timebox.model_dump(), "phases": phases}
    )

    assert "cycle_timebox" not in result
