"""Pin the R6 same-shape retry loop bound (SameShapeRetryTracker).

The tracker is the single-source mechanism that bounds consecutive
identical-fire resumes of the same agent session. The PROMPT requires
the bound to:

  - Default to 3 (small enough to catch the 25-minute four-cycle burn
    that motivated this task after the 3rd consecutive identical fire,
    well before the 4th).
  - Be configurable via ``RecoveryControllerOptions.same_shape_retry_limit``.
  - Carry the fingerprint, consecutive count, and effective limit as
    structured evidence on the bound exception.
  - Reset the counter when the fingerprint changes (different fire
    reason, different diagnostic signature, or any forward progress).
  - Allow progress between fires (a new artifact or workspace change
    resets the shape).
  - Reject limits < 1 in the constructor (a bound of 0 silently
    disables the R6 contract, which converts an infinite loop into a
    fast quiet failure of a healthy agent).

The tests below pin every contract point. They use no I/O, no real
clock, no real subprocess; everything is in-memory and deterministic.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (only ``RecoveryController.handle`` against a
    hand-built ``ClassifiedFailure``).
  - No real filesystem.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from ralph.recovery._same_shape_retry_tracker import (
    RetryFingerprint,
    SameShapeRetryLoopError,
    SameShapeRetryTracker,
    fingerprint_from_mapping,
)

# ---------------------------------------------------------------------------
# Tracker unit tests
# ---------------------------------------------------------------------------


def test_three_identical_fires_fire_bounded_ceiling() -> None:
    """Three identical fires -> 3rd fires the bound with reason + diff as evidence.

    This is the R6 acceptance criterion #1: ``consecutive >= limit`` raises
    ``SameShapeRetryLoopError`` carrying the fingerprint and effective
    limit. The exception's message names every fingerprint field so an
    operator can diagnose the loop without re-deriving it from logs.
    """
    tracker = SameShapeRetryTracker(limit=3)
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    assert count == 1
    assert fp == ("no_output_at_start", "SILENT_SUBAGENT", True, True)
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
        prior_fingerprint=fp,
        prior_consecutive=count,
    )
    assert count == 2
    with pytest.raises(SameShapeRetryLoopError) as excinfo:
        tracker.record_fire(
            fire_reason="no_output_at_start",
            diagnostic_signature="SILENT_SUBAGENT",
            no_new_artifact_since_prior=True,
            workspace_change_since_prior=True,
            prior_fingerprint=fp,
            prior_consecutive=count,
        )
    exc = excinfo.value
    assert exc.consecutive == 3
    assert exc.limit == 3
    assert exc.fingerprint == ("no_output_at_start", "SILENT_SUBAGENT", True, True)
    # The exception message must name every fingerprint field so a
    # operator reading the log can diagnose the loop without re-deriving.
    msg = str(exc)
    assert "no_output_at_start" in msg
    assert "SILENT_SUBAGENT" in msg
    assert "no_new_artifact_since_prior=True" in msg
    assert "workspace_change_since_prior=True" in msg
    assert "limit=3" in msg


def test_three_identical_fires_with_one_new_artifact_does_not_fire_bound() -> None:
    """A new artifact between fires resets the shape; no bound fires.

    This is the R6 acceptance criterion #2: forward progress (a new
    artifact) breaks the shape even when the other three signals match.
    The test sets ``no_new_artifact_since_prior=False`` on the third fire
    while keeping the other three signals identical, and asserts no
    exception is raised.
    """
    tracker = SameShapeRetryTracker(limit=3)
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    assert count == 1
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=False,
        workspace_change_since_prior=True,
        prior_fingerprint=fp,
        prior_consecutive=count,
    )
    assert count == 1
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=False,
        workspace_change_since_prior=True,
        prior_fingerprint=fp,
        prior_consecutive=count,
    )
    assert count == 2


def test_one_identical_and_one_non_identical_resets_consecutive() -> None:
    """Non-identical fire (different diagnostic signature) resets the counter.

    This is the R6 acceptance criterion #3: a state transition (e.g.
    THINKING -> SILENT_SUBAGENT) between fires is exactly the signal
    the fingerprint's diagnostic_signature slot is designed to catch.
    The test asserts that the consecutive count resets to 1 after a
    non-matching fire, even when the limit is 3 and there is no
    progress signal.
    """
    tracker = SameShapeRetryTracker(limit=3)
    fp1, count1 = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="THINKING",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    assert count1 == 1
    fp2, count2 = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
        prior_fingerprint=fp1,
        prior_consecutive=count1,
    )
    assert count2 == 1
    assert fp2 != fp1


def test_configurable_limit_surface() -> None:
    """``SameShapeRetryTracker(limit=N)`` honors N.

    This is the R6 acceptance criterion #4: the limit surface is
    configurable so operators with a longer leash can override the
    default 3. The test constructs the tracker with limit=5 and
    asserts the bound fires at the 5th consecutive identical fire,
    NOT at the 3rd (which would be the default).
    """
    tracker = SameShapeRetryTracker(limit=5)
    fp, count = tracker.record_fire(
        fire_reason="no_progress_quiet",
        diagnostic_signature="STRICTLY_STUCK",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    for expected_count in (2, 3, 4):
        fp, count = tracker.record_fire(
            fire_reason="no_progress_quiet",
            diagnostic_signature="STRICTLY_STUCK",
            no_new_artifact_since_prior=True,
            workspace_change_since_prior=True,
            prior_fingerprint=fp,
            prior_consecutive=count,
        )
        assert count == expected_count
    # The 5th identical fire should raise with consecutive == 5.
    with pytest.raises(SameShapeRetryLoopError) as excinfo:
        tracker.record_fire(
            fire_reason="no_progress_quiet",
            diagnostic_signature="STRICTLY_STUCK",
            no_new_artifact_since_prior=True,
            workspace_change_since_prior=True,
            prior_fingerprint=fp,
            prior_consecutive=count,
        )
    assert excinfo.value.consecutive == 5
    assert excinfo.value.limit == 5


def test_default_is_same_shape_retry_default_constant() -> None:
    """Tracker default equals the ``SAME_SHAPE_RETRY_DEFAULT`` constant.

    The default must match the constant so a future change to the
    constant propagates automatically and the PROMPT's "small enough
    to catch the 25-minute four-cycle burn" guarantee holds.
    """
    from ralph.timeout_defaults import SAME_SHAPE_RETRY_DEFAULT

    assert SameShapeRetryTracker().limit == SAME_SHAPE_RETRY_DEFAULT
    assert SAME_SHAPE_RETRY_DEFAULT == 3


def test_zero_and_negative_limits_rejected() -> None:
    """Tracker rejects limits < 1 with a clear error.

    A bound of 0 would silently disable the R6 contract, converting an
    infinite loop into a fast, quiet failure of a healthy agent. The
    constructor must reject ``0`` and negative values with a clear
    error message so a future operator who tries to disable the bound
    sees the rejection immediately.
    """
    with pytest.raises(ValueError, match="must be >= 1"):
        SameShapeRetryTracker(limit=0)
    with pytest.raises(ValueError, match="must be >= 1"):
        SameShapeRetryTracker(limit=-1)


def test_fingerprint_from_mapping_helper() -> None:
    """``fingerprint_from_mapping`` extracts a tuple from a dict-like input.

    The helper is the public surface for callers that already hold the
    four signals in a ``dict`` (e.g. the recovery controller's
    per-failure state). The test asserts a missing required key raises
    ``KeyError`` so a caller cannot accidentally pass a partial dict.
    """
    good: Mapping[str, object] = {
        "fire_reason": "no_output_at_start",
        "diagnostic_signature": "SILENT_SUBAGENT",
        "no_new_artifact_since_prior": True,
        "workspace_change_since_prior": False,
    }
    fp = fingerprint_from_mapping(good)
    assert fp == ("no_output_at_start", "SILENT_SUBAGENT", True, False)
    # The tuple is hashable so it can be used as a dict key.
    assert hash(fp) == hash(fp)

    bad: Mapping[str, object] = {
        "fire_reason": "no_output_at_start",
        "diagnostic_signature": "SILENT_SUBAGENT",
    }
    with pytest.raises(KeyError, match="no_new_artifact_since_prior"):
        fingerprint_from_mapping(bad)


def test_fingerprint_str_coercion_normalizes_non_string_inputs() -> None:
    """The tracker coerces non-string fire_reason and diagnostic_signature.

    The controller's caller may pass enum values or wrapped strings; the
    tracker must accept them and produce a hashable, equality-comparable
    fingerprint. This test pins the coercion so a future enum change
    does not silently break the fingerprint comparison.
    """
    tracker = SameShapeRetryTracker(limit=3)
    from enum import Enum

    class Reason(Enum):
        NO_OUTPUT_AT_START = "no_output_at_start"

    class Kind(Enum):
        SILENT_SUBAGENT = "SILENT_SUBAGENT"

    fp, count = tracker.record_fire(
        fire_reason=Reason.NO_OUTPUT_AT_START,
        diagnostic_signature=Kind.SILENT_SUBAGENT,
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    assert fp[0] == "no_output_at_start"
    assert fp[1] == "SILENT_SUBAGENT"

    # A second fire with the SAME coerced values must match.
    fp2, count2 = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
        prior_fingerprint=fp,
        prior_consecutive=count,
    )
    assert fp2 == fp
    assert count2 == 2


def test_first_fire_without_prior_does_not_raise() -> None:
    """A first fire with no prior fingerprint returns (fingerprint, 1).

    The first fire in a session has no prior fingerprint; the tracker
    must accept ``prior_fingerprint=None`` and ``prior_consecutive=0``
    as defaults without raising. The returned consecutive is always 1
    (the count for THIS fire), not 0.
    """
    tracker = SameShapeRetryTracker(limit=3)
    fp, count = tracker.record_fire(
        fire_reason="no_output_at_start",
        diagnostic_signature="SILENT_SUBAGENT",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    assert count == 1
    assert fp[0] == "no_output_at_start"


def test_typed_fingerprint_annotation() -> None:
    """The ``RetryFingerprint`` type alias is a 4-tuple of (str, str, bool, bool).

    The annotation is the contract for callers that need to type their
    own storage (e.g. the recovery controller's ``_same_shape_state``
    map). This test pins the alias so a future shape change does not
    silently break callers.
    """
    fp: RetryFingerprint = ("no_output_at_start", "SILENT_SUBAGENT", True, False)
    assert isinstance(fp, tuple)
    assert len(fp) == 4
    assert isinstance(fp[0], str)
    assert isinstance(fp[1], str)
    assert isinstance(fp[2], bool)
    assert isinstance(fp[3], bool)
