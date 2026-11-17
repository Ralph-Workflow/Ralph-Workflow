from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_args

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_resolve import (
    ConflictResolver,
    handle_unsuccessful_resolution,
)
from ralph.pipeline.conflict_resolution import operator_cap
from ralph.pipeline.conflict_resolution.driver import (
    run_rebase_conflict_resolution_outcome,
)
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.resolution_outcome import (
    STOP_RESOLUTION_OUTCOMES,
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)


def _sealed_variant_samples() -> tuple[StopResolutionOutcome, ...]:
    return (
        ResolvedOutcome(),
        WaitingOutcome("gate", "key changes"),
        RetryLaterOutcome(on_change="gate changed"),
        NextStrategyOutcome("completion_in_place"),
        ConfigFailureOutcome(Path("broken.toml"), "invalid value"),
        UnknownOutcome(RuntimeError("boom")),
    )


def test_fast_forward_producer_returns_sealed_outcome() -> None:
    assert inspect.signature(fast_forward_target).return_annotation == "StopResolutionOutcome"
    assert inspect.signature(run_rebase_conflict_resolution_outcome).return_annotation == (
        "StopResolutionOutcome"
    )


def test_conflict_resolver_accepts_only_sealed_outcome() -> None:
    resolver_args = get_args(ConflictResolver)

    assert resolver_args[-1] is StopResolutionOutcome
    assert bool not in resolver_args


def test_rebase_result_exposes_no_legacy_boolean_decision() -> None:
    result = RebaseResolutionResult(
        ResolvedOutcome(),
        None,
        ResolutionFailureCause.NONE,
    )

    assert not hasattr(result, "succeeded")
    assert not hasattr(result, "__len__")
    assert not hasattr(result, "__iter__")
    assert not hasattr(result, "__getitem__")


@pytest.mark.parametrize("outcome", _sealed_variant_samples())
def test_sealed_variants_reject_tuple_unpack_index_and_len(
    outcome: StopResolutionOutcome,
) -> None:
    with pytest.raises(TypeError):
        _first, _second = outcome
    with pytest.raises(TypeError):
        _ = outcome[0]
    with pytest.raises(TypeError):
        len(outcome)
    assert outcome != (True, "")
    assert outcome != (False, "")


def test_sealed_outcome_catalog_matches_samples() -> None:
    samples = _sealed_variant_samples()
    assert len(samples) == len(STOP_RESOLUTION_OUTCOMES)
    assert {type(sample) for sample in samples} == set(STOP_RESOLUTION_OUTCOMES)


def test_run_loop_has_no_tuple_outcome_transport() -> None:
    assert not hasattr(run_loop, "_typed_outcome_transport")
    assert not hasattr(run_loop, "_retry_later_cause")
    source = inspect.getsource(run_loop.resolve_paused_rebase)
    assert "_typed_outcome_transport" not in source
    assert "resolved, reason, failure_cause =" not in source


@pytest.mark.parametrize(
    ("outcome", "expected_outcome", "expected_reason", "expected_cause"),
    [
        (ResolvedOutcome(), "success", None, ResolutionFailureCause.NONE),
        (
            WaitingOutcome("gate", "key changes"),
            "merge_commit_pending",
            "waiting for gate; resumes when key changes",
            ResolutionFailureCause.CONTEXT_FAILURE,
        ),
        (
            RetryLaterOutcome(on_change="gate changed"),
            "merge_commit_pending",
            "gate changed",
            ResolutionFailureCause.AGENT_FAILURE,
        ),
        (
            NextStrategyOutcome("completion_in_place"),
            "merge_commit_pending",
            "continuing with autonomous strategy: completion_in_place",
            ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ),
        (
            ConfigFailureOutcome(Path("broken.toml"), "invalid value"),
            "merge_commit_pending",
            "fix broken.toml then run ralph --check-config",
            ResolutionFailureCause.CONFIG_FAILURE,
        ),
        (
            UnknownOutcome(RuntimeError("boom")),
            "merge_commit_pending",
            "unclassified resolution failure; advancing strategy: boom",
            ResolutionFailureCause.CONTEXT_FAILURE,
        ),
    ],
)
def test_merge_consumer_maps_every_sealed_variant(
    outcome: StopResolutionOutcome,
    expected_outcome: str,
    expected_reason: str | None,
    expected_cause: ResolutionFailureCause,
) -> None:
    result = handle_unsuccessful_resolution(outcome, None)

    assert result.outcome == expected_outcome
    assert result.reason == expected_reason
    assert result.failure_cause == expected_cause


def test_resolver_operator_cap_surface_is_removed() -> None:
    assert not hasattr(operator_cap, "operator_cap_expired")
    assert not hasattr(operator_cap, "remaining_operator_cap")
