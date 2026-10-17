"""Load-time lease and counter-clamp invariants for the integration record loader.

These tests pin the parser-level helpers that make the O1 livelock
structurally unreachable:

* :func:`parse_lease` decodes the ``<fingerprint>:<held_at>`` lease
  string and refuses malformed input.
* :func:`parse_record_payload` clamps pre-existing over-cap
  ``merge_commit_repair_attempts`` to ``cap - 1`` so the diagnostic
  check (an inclusive ``>=``) cannot re-trigger intervention on a
  value left behind by an older or pre-fix version.
"""

from __future__ import annotations


def test_parse_lease_extracts_fingerprint_and_held_at() -> None:
    from ralph.pipeline.auto_integrate_record import parse_lease

    assert parse_lease("abc:1234.5") == ("abc", 1234.5)


def test_parse_lease_returns_none_for_malformed() -> None:
    from ralph.pipeline.auto_integrate_record import parse_lease

    assert parse_lease("abc") == (None, None)
    assert parse_lease("") == (None, None)
    assert parse_lease(None) == (None, None)
    assert parse_lease(":1234") == (None, None)
    assert parse_lease("abc:") == (None, None)
    assert parse_lease("abc:not-a-number") == (None, None)


def test_load_time_counter_clamp_clamps_to_cap_minus_one() -> None:
    """A record with ``merge_commit_repair_attempts=541`` is clamped to 4.

    Bound is ``cap - 1`` (not ``cap``) because the diagnostic check
    at ``_integration_diagnostic.py:222-224`` is an inclusive
    ``>=``; clamping to ``cap`` (5) would still satisfy the gate
    (``5 >= 5`` is True) and re-trigger intervention.
    """
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": 541,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 4
    assert parsed.diagnostic_intervention_required is False


def test_load_time_counter_clamp_preserves_under_cap_values() -> None:
    """Under-cap counters pass through unchanged (no double-clamp)."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": 2,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 2


def test_load_time_counter_clamp_handles_zero_and_missing() -> None:
    """Missing or zero counters are tolerated; the value remains 0."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 0


def test_load_time_counter_clamp_treats_garbage_as_rejected() -> None:
    """Non-integer values for the counter are rejected by pydantic (fail-closed)."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": "not-an-int",
    }
    # The model rejects non-int values; the loader must surface the
    # rejection (returns None) rather than coerce silently.
    assert parse_record_payload(legacy) is None
