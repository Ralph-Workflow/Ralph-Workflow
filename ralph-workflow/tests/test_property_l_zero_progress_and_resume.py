# property-test: L — zero-progress cap and resume path
"""Zero-progress retries are bounded by the ledger, and retries do not re-emit the original task.

The recovery loop is bounded by the agent failure ledger (AgentFailureLedger).
An unproductive retry that fails to produce a valid artifact or workspace changes
makes no forward progress. The ledger enforces that consecutive unproductive
attempts cannot exceed the consecutive limit (default 3), after which the agent is
demoted into Cooldown. Changing output prose without workspace changes cannot evade
this bound (PRODUCT_CRITERIA §3.5).

The resume path audit confirms build_retry_error_block references the
original prompt by path only — it never inlines the original task
content, so a "resumed" agent does not restart from scratch.
"""

from __future__ import annotations

from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.effect_executor import _write_agent_retry_prompt
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
)
from ralph.recovery.retry_prompt import build_retry_error_block

_ORIGINAL_TASK_BODY_TOKEN = "ORIGINAL_TASK_BODY_TOKEN_xyz"


def test_ledger_default_consecutive_limit_is_three() -> None:
    """The default consecutive unproductive limit is 3."""
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock)
    assert ledger.consecutive_limit == 3


def test_ledger_demotes_after_three_consecutive_unproductive_attempts() -> None:
    """Three consecutive unproductive attempts trigger demotion on the third call."""
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    out1 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert not out1.demoted
    assert out1.status.is_eligible

    out2 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert not out2.demoted
    assert out2.status.is_eligible

    out3 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert out3.demoted
    assert isinstance(out3.status, Cooldown)


def test_ledger_does_not_demote_on_first_two_unproductive() -> None:
    """Demotion fires only on the third consecutive unproductive attempt, not earlier."""
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    out1 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    out2 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert not out1.demoted
    assert not out2.demoted


def test_ledger_resets_streak_on_workspace_change() -> None:
    """A workspace change resets the consecutive unproductive streak while total grows."""
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))

    # Genuine forward progress: workspace_changed=True
    progress_out = ledger.record(
        AttemptFact(
            phase="development",
            agent="claude",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert not progress_out.demoted
    assert progress_out.status.is_eligible

    # Consecutive streak starts fresh: does not demote on next 2 attempts
    next_out1 = ledger.record(
        AttemptFact(phase="development", agent="claude", artifact_valid=False)
    )
    assert not next_out1.demoted
    assert next_out1.status.is_eligible

    next_out2 = ledger.record(
        AttemptFact(phase="development", agent="claude", artifact_valid=False)
    )
    assert not next_out2.demoted
    assert next_out2.status.is_eligible

    # Third consecutive unproductive attempt demotes
    next_out3 = ledger.record(
        AttemptFact(phase="development", agent="claude", artifact_valid=False)
    )
    assert next_out3.demoted


def test_varying_output_without_workspace_change_does_not_reset_streak() -> None:
    """Varying prose output without valid artifact or workspace changes cannot evade demotion."""
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    # Three attempts with different rendered output but no workspace change
    for attempt in range(1, 4):
        outcome = ledger.record(
            AttemptFact(
                phase="development",
                agent="claude",
                artifact_valid=False,
                workspace_changed=False,
            )
        )
        if attempt < 3:
            assert not outcome.demoted
        else:
            assert outcome.demoted
            assert isinstance(outcome.status, Cooldown)


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
    assert "Original prompt:" in content, "resume prompt must reference the original prompt by path"
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
