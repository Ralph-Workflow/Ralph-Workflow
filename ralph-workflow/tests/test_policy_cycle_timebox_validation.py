from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _phases_recording_failed(pipeline: PipelinePolicy) -> dict[str, Any]:
    phases = {name: phase.model_dump() for name, phase in pipeline.phases.items()}
    failed_route = phases["development_analysis"]["decisions"]["failed"]
    failed_route["target"] = "development_final_commit_review"
    failed_route["reset_loop"] = True
    failed_route["cycle_outcome"] = "failed"
    return phases


def test_cycle_timebox_rejects_an_outcome_outside_the_vocabulary() -> None:
    import pydantic
    import pytest as _pytest

    from ralph.policy.models import CycleTimeboxPolicy

    with _pytest.raises(pydantic.ValidationError):
        CycleTimeboxPolicy(
            start_source="planning_analysis",
            start_entry="development",
            guarded_entry="development",
            end_entry="development_final_commit_review",
            finalization_target="development_final_commit_review",
            finalization_cycle_outcome="timeboxed",
        )


@pytest.mark.timeout_seconds(5)
def test_timebox_outcome_is_checked_even_when_no_decision_declares_it() -> None:
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
    pipeline = load_policy(_DEFAULTS_DIR).pipeline

    revalidated = type(pipeline).model_validate({**pipeline.model_dump(), "post_commit_routes": []})

    assert revalidated.post_commit_routes == []


def test_incomplete_routes_are_rejected_even_when_the_fallthrough_loops() -> None:
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    phases = _phases_recording_failed(pipeline)
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
            {
                **pipeline.model_dump(),
                "phases": _phases_recording_failed(pipeline),
                "post_commit_routes": completed_only,
            }
        )


def test_decision_outcomes_are_checked_without_a_cycle_timebox() -> None:
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
                "phases": _phases_recording_failed(pipeline),
                "cycle_timebox": None,
                "post_commit_routes": completed_routes,
            }
        )


def test_decision_routed_straight_to_a_terminal_needs_no_post_commit_route() -> None:
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
    import pydantic
    import pytest as _pytest

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    kept = [
        route.model_dump()
        for route in pipeline.post_commit_routes
        if not (route.when.budget_state == "remaining" and route.when.cycle_outcome == "failed")
    ]

    with _pytest.raises(pydantic.ValidationError, match="budget_state='remaining'"):
        type(pipeline).model_validate(
            {
                **pipeline.model_dump(),
                "phases": _phases_recording_failed(pipeline),
                "post_commit_routes": kept,
            }
        )


def test_a_wildcard_route_covers_every_outcome_for_its_budget_state() -> None:
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
    from ralph.policy.validation import validate_policy_completeness

    validate_policy_completeness(load_policy(_DEFAULTS_DIR))


def test_a_finalization_target_inside_the_cycle_is_rejected() -> None:
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
                    "finalization_target": "development_commit_review",
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
                    "end_entry": "development_commit_review",
                },
            }
        )


def test_a_misspelled_timebox_field_is_rejected_rather_than_ignored() -> None:
    import pydantic
    import pytest as _pytest

    from ralph.policy.models import CycleTimeboxPolicy

    with _pytest.raises(pydantic.ValidationError, match="finalisation_cycle_outcome"):
        CycleTimeboxPolicy(
            start_source="planning_analysis",
            start_entry="development",
            guarded_entry="development",
            end_entry="development_final_commit_review",
            finalization_target="development_final_commit_review",
            finalisation_cycle_outcome="failed",
        )


def test_an_unrecorded_outcome_that_loops_forever_is_rejected() -> None:
    import pytest as _pytest

    from ralph.policy.models import PipelinePolicy
    from ralph.policy.validation import validate_policy_completeness
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
    from loguru import logger

    from ralph.policy.loader import disable_incompatible_inherited_cycle_timebox

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    normalized: dict[str, object] = {
        "cycle_timebox": pipeline.cycle_timebox.model_dump(),
        "phases": {name: phase for name, phase in pipeline.phases.items() if name != "development"},
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
    from ralph.policy.models import CycleTimeboxPolicy

    minimal = CycleTimeboxPolicy(
        start_source="planning_analysis",
        start_entry="development",
        guarded_entry="development",
        end_entry="development_final_commit_review",
        finalization_target="development_final_commit_review",
    )

    assert minimal.duration_seconds == 36000.0
    assert minimal.finalization_cycle_outcome == "completed"


@pytest.mark.timeout_seconds(5)
def test_an_inherited_timebox_whose_start_edge_is_gone_is_disabled() -> None:
    from ralph.policy.loader import disable_incompatible_inherited_cycle_timebox

    pipeline = load_policy(_DEFAULTS_DIR).pipeline
    phases = dict(pipeline.phases)
    planning_analysis = phases["planning_analysis"]
    phases["planning_analysis"] = planning_analysis.model_copy(
        update={
            "transitions": planning_analysis.transitions.model_copy(
                update={"on_success": "development_final_commit_review"}
            ),
            "decisions": {},
        }
    )

    result = disable_incompatible_inherited_cycle_timebox(
        {"cycle_timebox": pipeline.cycle_timebox.model_dump(), "phases": phases}
    )

    assert "cycle_timebox" not in result
