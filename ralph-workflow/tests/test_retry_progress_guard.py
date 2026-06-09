"""The recovery loop must not spin on zero-progress retries.

The agent recovery loop retries a failed agent up to ``max_recovery_attempts``.
Without a progress check it re-runs an identically-failing attempt up to that
maximum (and the whole session budget), making zero forward progress — the wedge
that surfaces as an endless ``Retrying ... (N/10)`` loop restarting from scratch.

``RetryProgressGuard`` makes the zero-progress case provably bounded: it collapses
each failure to a normalized signature and forces the loop to STOP once the same
signature repeats ``MAX_IDENTICAL_RETRY_ATTEMPTS`` times in a row. A signature
change (a genuinely different failure / forward progress) resets the streak, so a
legitimately-progressing-but-failing agent still gets its full retry budget.
"""

from __future__ import annotations

from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)


def test_constant_is_a_small_positive_bound() -> None:
    assert isinstance(MAX_IDENTICAL_RETRY_ATTEMPTS, int)
    assert MAX_IDENTICAL_RETRY_ATTEMPTS >= 1


def test_identical_signatures_abort_after_exactly_the_cap() -> None:
    guard = RetryProgressGuard(max_identical=3)
    # The first (cap - 1) identical failures are allowed to retry...
    assert guard.record("sig") is False
    assert guard.record("sig") is False
    # ...the cap-th identical failure forces a stop.
    assert guard.record("sig") is True


def test_a_changed_signature_resets_the_streak() -> None:
    guard = RetryProgressGuard(max_identical=3)
    assert guard.record("a") is False
    assert guard.record("a") is False
    # Progress: a different failure signature resets the streak to 1.
    assert guard.record("b") is False
    assert guard.record("b") is False
    assert guard.record("b") is True


def test_alternating_signatures_never_trip() -> None:
    guard = RetryProgressGuard(max_identical=3)
    for index in range(50):
        assert guard.record("a" if index % 2 == 0 else "b") is False


def test_cap_of_one_aborts_on_first_repeat_basis() -> None:
    # With a cap of 1, the very first failure of any signature stops retrying.
    guard = RetryProgressGuard(max_identical=1)
    assert guard.record("anything") is True


def test_signature_is_stable_across_equivalent_failures() -> None:
    # Two failures that differ only in a volatile token (timestamp / uuid / a
    # changing duration) must collapse to the SAME signature, so a spiral whose
    # surface text wiggles cannot evade the bound.
    sig_a = retry_failure_signature(
        "an inactivity timeout",
        ["2026-06-09T21:33:53 Agent produced no output for 300s"],
    )
    sig_b = retry_failure_signature(
        "an inactivity timeout",
        ["2026-06-09T21:38:11 Agent produced no output for 300s"],
    )
    assert sig_a == sig_b


def test_signature_differs_when_failure_reason_differs() -> None:
    sig_timeout = retry_failure_signature("an inactivity timeout", ["same tail"])
    sig_conn = retry_failure_signature("a transient connectivity failure", ["same tail"])
    assert sig_timeout != sig_conn


def test_signature_differs_when_output_reflects_progress() -> None:
    sig_first = retry_failure_signature("an inactivity timeout", ["working on the auth module"])
    sig_second = retry_failure_signature(
        "an inactivity timeout", ["working on the database module"]
    )
    assert sig_first != sig_second


def test_numeric_noise_cannot_evade_the_bound() -> None:
    """A doomed loop whose only per-attempt difference is a number must NOT reset
    the streak. These are the exact evasion vectors an adversarial review found:
    short durations, bare clock times, PIDs, and incrementing counters — none of
    which RepetitionTracker.fingerprint normalizes on its own.
    """
    reason = "a transient connectivity failure"
    short_duration = [
        retry_failure_signature(reason, ["Agent produced no output for 295s"]),
        retry_failure_signature(reason, ["Agent produced no output for 300s"]),
    ]
    bare_clock = [
        retry_failure_signature(reason, ["14:33:53 still waiting on child"]),
        retry_failure_signature(reason, ["14:38:11 still waiting on child"]),
    ]
    pid = [
        retry_failure_signature(reason, ["process 4521 died"]),
        retry_failure_signature(reason, ["process 9987 died"]),
    ]
    counter = [retry_failure_signature(reason, [f"elapsed {i}s"]) for i in range(300, 320)]

    assert short_duration[0] == short_duration[1]
    assert bare_clock[0] == bare_clock[1]
    assert pid[0] == pid[1]
    assert len(set(counter)) == 1, "an incrementing counter must collapse to one signature"


def test_numeric_form_alternation_cannot_evade_the_bound() -> None:
    """Changing a number's textual FORM (not just its value) must not vary the
    signature: int<->decimal, version/IP, scientific notation, and short hex all
    collapse to one token. (Second adversarial review found these survived a
    plain ``\\d+`` collapse because the '.', '+'/'-', and hex letters are not
    digits.)
    """
    reason = "an inactivity timeout"
    int_vs_decimal = [
        retry_failure_signature(reason, ["waited 10s for the child"]),
        retry_failure_signature(reason, ["waited 10.5s for the child"]),
        retry_failure_signature(reason, ["waited 11.25s for the child"]),
    ]
    version_or_ip = [
        retry_failure_signature(reason, ["bound to 10.0.0.1 ok"]),
        retry_failure_signature(reason, ["bound to 192.168.1.254 ok"]),
    ]
    scientific = [
        retry_failure_signature(reason, ["delta 1e-9 over budget"]),
        retry_failure_signature(reason, ["delta 2e+3 over budget"]),
    ]
    short_hex = [
        retry_failure_signature(reason, ["addr 0x1f failed"]),
        retry_failure_signature(reason, ["addr 0xabcd failed"]),
    ]
    assert len(set(int_vs_decimal)) == 1
    assert len(set(version_or_ip)) == 1
    assert len(set(scientific)) == 1
    assert len(set(short_hex)) == 1


def test_int_decimal_alternating_spiral_trips_the_guard() -> None:
    """A spiral alternating between integer and decimal form (the exact residual
    the second review flagged) must still trip the cap, not run to the larger
    attempt/time budget."""
    guard = RetryProgressGuard(max_identical=3)
    reason = "an inactivity timeout"
    tripped_at = None
    for attempt in range(50):
        token = f"{attempt}.5" if attempt % 2 == 0 else f"{attempt}"
        signature = retry_failure_signature(reason, [f"waited {token}s for the child"])
        if guard.record(signature):
            tripped_at = attempt
            break
    assert tripped_at == 2


def test_numeric_noise_spiral_trips_the_guard_within_the_cap() -> None:
    """End-to-end: a spiral whose output only wiggles a counter must still be
    stopped at the cap, not run to the larger attempt/time budget."""
    guard = RetryProgressGuard(max_identical=3)
    reason = "a transient connectivity failure"
    tripped_at = None
    for attempt in range(50):
        signature = retry_failure_signature(reason, [f"elapsed {300 + attempt}s waiting"])
        if guard.record(signature):
            tripped_at = attempt
            break
    assert tripped_at == 2, "the counter-wiggling spiral must trip on the 3rd identical attempt"
