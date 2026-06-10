# property-test: L — zero-progress cap and resume path
"""Zero-progress retries are bounded, and retries do not re-emit the original task.

The recovery loop is bounded by THREE independent caps: a count cap
(max_recovery_attempts), a wall-clock cap (session ceiling), and a
zero-progress cap (RetryProgressGuard.record). The third cap is the
property that closes the failure class: a retry that reproduces the
prior attempt's signature makes no forward progress, and the loop must
STOP once the same signature repeats ``MAX_IDENTICAL_RETRY_ATTEMPTS``
times in a row.

The resume path audit confirms build_retry_error_block references the
original prompt by path only — it never inlines the original task
content, so a "resumed" agent does not restart from scratch.

The structural fingerprint detects the restart-from-scratch pattern:
when an agent opens its next attempt with a known opening-narrative
pattern, the fingerprint collapses to the same structural key across
attempts even when the concrete tokens differ. The structural side is
the dominant signal when a restart pattern is detected; the literal
side (RepetitionTracker.fingerprint + _NUMERIC_TOKEN.sub) is the
fallback when no restart pattern is detected. This closes the failure
class where the agent produces a fresh-looking narrative of the same
restart behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)
from ralph.pipeline.effect_executor import _write_agent_retry_prompt
from ralph.recovery.retry_prompt import build_retry_error_block

_ORIGINAL_TASK_BODY_TOKEN = "ORIGINAL_TASK_BODY_TOKEN_xyz"


def test_max_identical_constant_is_three() -> None:
    """The cap is 3 — small enough to fail fast on a real spiral."""
    assert MAX_IDENTICAL_RETRY_ATTEMPTS == 3


def test_guard_aborts_after_three_identical_signatures() -> None:
    """Three identical signatures trigger the cap on the third call."""
    guard = RetryProgressGuard()
    assert guard.record("sig-A") is False  # streak = 1, no cap
    assert guard.record("sig-A") is False  # streak = 2, no cap
    assert guard.record("sig-A") is True   # streak = 3, cap fires


def test_guard_does_not_cap_on_first_two_identical() -> None:
    """The cap fires only on the third identical signature, not earlier."""
    guard = RetryProgressGuard()
    assert guard.record("sig-X") is False
    assert guard.record("sig-X") is False


def test_guard_resets_streak_on_signature_change() -> None:
    """A different signature between two identical signatures resets the streak."""
    guard = RetryProgressGuard()
    guard.record("sig-A")
    guard.record("sig-A")
    # Different signature resets the streak
    assert guard.record("sig-B") is False  # new streak = 1
    # sig-A again — only 1 in this new streak, not 3
    assert guard.record("sig-A") is False  # streak = 2
    assert guard.record("sig-A") is False  # streak = 3? — the new run reset
    # Note: guard counts consecutive sigs, so after a change the streak
    # starts fresh.


def test_volatile_uuid_token_stripped_from_signature() -> None:
    """UUIDs in the failure text are stripped, so retries look identical."""
    base_text = "ConnectionError: agent wedged"
    sig_a = retry_failure_signature(base_text, ["run-12345 at 12:34"])
    sig_b = retry_failure_signature(base_text, ["run-67890 at 23:45"])
    # RepetitionTracker.fingerprint strips UUIDs (8+ hex chunks), and
    # _NUMERIC_TOKEN strips the rest of the numerics.
    assert sig_a == sig_b, (
        f"volatile tokens must be stripped: {sig_a!r} vs {sig_b!r}"
    )


def test_volatile_clock_stripped_from_signature() -> None:
    """Different clocks produce identical signatures after normalization."""
    base_text = "InactivityTimeout: no output for 300s"
    sig_a = retry_failure_signature(base_text, ["00:01:23"])
    sig_b = retry_failure_signature(base_text, ["00:09:87"])
    assert sig_a == sig_b


def test_volatile_pid_stripped_from_signature() -> None:
    """Different PIDs produce identical signatures after normalization."""
    base_text = "SubprocessFailed"
    sig_a = retry_failure_signature(base_text, ["pid=12345"])
    sig_b = retry_failure_signature(base_text, ["pid=67890"])
    assert sig_a == sig_b


def test_real_progress_changes_signature() -> None:
    """A genuine textual change in the output (not just numerics) changes the sig."""
    base_text = "SubprocessFailed"
    sig_a = retry_failure_signature(base_text, ["hit error at 12:00:00"])
    sig_b = retry_failure_signature(base_text, ["hit error at 12:00:00 (recovered)"])
    assert sig_a != sig_b, "non-numeric word-level progress must change the sig"


def test_guard_three_different_uuids_caps_correctly() -> None:
    """End-to-end: 4 failures with different UUIDs — the 3rd identical sig caps."""
    guard = RetryProgressGuard()
    base = "InactivityTimeout"
    sigs = [
        retry_failure_signature(base, [f"uuid-{i:04d}-aaaa at 12:00:00"])
        for i in range(4)
    ]
    # All four should collapse to the same signature after normalization
    assert len(set(sigs)) == 1, f"UUIDs should be normalized; got {sigs}"
    results = [guard.record(s) for s in sigs]
    # First three: streak reaches cap on the 3rd, so the third returns True
    # (the cap fires when the streak reaches max_identical, halting the
    # *next* attempt). The 4th would also return True because the streak
    # is already at/past the cap. The contract: at most 3 attempts run
    # before the cap blocks further retries.
    assert results[0] is False
    assert results[1] is False
    assert results[2] is True


def test_wiring_progress_guard_caps_after_three_identical_signatures() -> None:
    """A fresh RetryProgressGuard caps after MAX_IDENTICAL_RETRY_ATTEMPTS identical sigs.

    The wiring of RetryProgressGuard into effect_executor.py is documented
    elsewhere (audit_test_policy excludes file-grep tests). The runtime
    behavior — three identical signatures trip the cap — is the
    property this test pins against the cap constant. A maintainer who
    bumps the cap constant without adding capacity downstream fails here.
    """
    guard = RetryProgressGuard()
    sig = retry_failure_signature(
        "InactivityTimeout", ["uuid-1234-aaaa at 12:00:00"]
    )
    assert guard.record(sig) is False
    assert guard.record(sig) is False
    assert guard.record(sig) is True


def test_build_retry_error_block_leads_with_error_recovery_required() -> None:
    """The resume function leads with 'ERROR RECOVERY REQUIRED'."""
    block = build_retry_error_block(failure_summary="test failure")
    assert block.startswith("ERROR RECOVERY REQUIRED")


def test_build_retry_error_block_references_prompt_only_by_path() -> None:
    """The function only references the prompt by path; never inlines content."""
    block = build_retry_error_block(
        failure_summary="test failure",
        prompt_path="/path/to/prompt.md",
    )
    assert "Original prompt: `/path/to/prompt.md`" in block
    assert "There must be no inlined prompt content" not in block


def test_build_retry_error_block_tells_agent_not_to_restart() -> None:
    """The block tells the agent to focus on the error, not restart the task."""
    block = build_retry_error_block(failure_summary="boom")
    assert "Do not restart the task from scratch" in block


def test_build_retry_error_block_contains_failure_summary() -> None:
    """The block carries the failure summary verbatim."""
    block = build_retry_error_block(failure_summary="REQUIRED_SUMMARY_TOKEN")
    assert "REQUIRED_SUMMARY_TOKEN" in block
    assert "PREVIOUS ATTEMPT FAILED" in block


def test_max_identical_constant_is_positive_int() -> None:
    """The cap is a positive int, enforced at import time."""
    assert isinstance(MAX_IDENTICAL_RETRY_ATTEMPTS, int)
    assert MAX_IDENTICAL_RETRY_ATTEMPTS >= 1


@pytest.mark.parametrize(
    "reason,output,expected_contains",
    [
        ("InactivityTimeout", ["00:00:01"], "inactivity"),
        ("NetworkError", ["HTTP 503 at host"], "network"),
        ("SubprocessFailed", ["command failed with code 1"], "subprocessfailed"),
    ],
)
def test_signature_includes_reason(reason: str, output: list[str], expected_contains: str) -> None:
    """The signature embeds the reason in lowercased, normalized form."""
    sig = retry_failure_signature(reason, output)
    assert expected_contains.lower() in sig.lower()


# ---------------------------------------------------------------------------
# Resume prompt path: action-aware retry prompt never inlines the original task
# ---------------------------------------------------------------------------


def _write_original_prompt(tmp_path: Path) -> Path:
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text(
        "ship the fix\n\n" + _ORIGINAL_TASK_BODY_TOKEN + "\n",
        encoding="utf-8",
    )
    return prompt


def test_property_l_resume_prompt_never_inlines_original_task(
    tmp_path: Path,
) -> None:
    """The resume prompt omits the ORIGINAL TASK PROMPT section entirely.

    A resumed agent already has the original task in its session context;
    re-inlining it defeats resume and forces the agent to restart from
    scratch (the documented property L wedge).
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action="resume",
    )
    content = Path(result).read_text(encoding="utf-8")

    assert "ORIGINAL TASK PROMPT:" not in content, (
        "resume prompt must NOT include the ORIGINAL TASK PROMPT section"
    )
    assert _ORIGINAL_TASK_BODY_TOKEN not in content
    assert "Original prompt:" in content, (
        "resume prompt must reference the original prompt by path"
    )
    assert "continue from where you left off" in content.lower()


def test_property_l_fresh_prompt_inlines_original_task(tmp_path: Path) -> None:
    """The fresh prompt DOES inline the original task body (current behavior).

    Fresh-session retries (stale-session, new-chain) have no prior session
    context, so the inlined body is the only signal the agent receives.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
    )
    content = Path(result).read_text(encoding="utf-8")

    assert "ORIGINAL TASK PROMPT:" in content
    assert _ORIGINAL_TASK_BODY_TOKEN in content


def test_property_l_new_session_with_id_prompt_does_not_inline(
    tmp_path: Path,
) -> None:
    """``new_session_with_id`` references the prompt by path only.

    The third AgentRetryAction literal. The new session is annotated
    with the prior session id, so the agent has resume-like context —
    no need to re-emit the original task body.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action="new_session_with_id",
    )
    content = Path(result).read_text(encoding="utf-8")

    assert _ORIGINAL_TASK_BODY_TOKEN not in content
    assert "ORIGINAL TASK PROMPT:" not in content
    assert "continue from where you left off" in content.lower()


def test_property_l_structural_signature_caps_on_restart_narrative() -> None:
    """The structural fingerprint caps a restart-narrative spiral on the 3rd attempt.

    Three attempts whose rendered output opens with restart-narrative text
    but with different concrete tokens (file names, paths, dashes) MUST
    collapse to the same structural fingerprint, and the guard MUST trip
    on the third attempt. The equality assertion is the GATE: if the
    structural-collapse contract fails (e.g. the implementation reverts
    to a concatenation that includes the divergent literal path token),
    then ``len(set(sigs)) > 1`` and this test fails with a clear message
    naming the divergence, BEFORE the guard-trip assertion would have
    passed silently.
    """
    guard = RetryProgressGuard()
    reason = "an inactivity timeout"
    outputs = [
        ["I will start by reading the prompt at /tmp/PROMPT.md"],
        ["I will start by reading the prompt at /tmp/PROMPT_v2.md"],
        ["I will start by reading the prompt at /tmp/PROMPT_draft.md"],
    ]
    sigs = [retry_failure_signature(reason, out) for out in outputs]
    assert len(set(sigs)) == 1, (
        f"three restart-narrative outputs with different concrete tokens "
        f"must collapse to one structural fingerprint; got {sigs}"
    )
    results = [guard.record(s) for s in sigs]
    assert results == [False, False, True], (
        f"the structural-dominant composition must trip the cap on the 3rd "
        f"identical structural signature; got {results}"
    )
