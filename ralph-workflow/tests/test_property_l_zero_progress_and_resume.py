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
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock)
    assert ledger.consecutive_limit == 3


def test_ledger_demotes_after_three_consecutive_unproductive_attempts() -> None:
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
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    out1 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    out2 = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert not out1.demoted
    assert not out2.demoted


def test_ledger_resets_streak_on_workspace_change() -> None:
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))

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

    next_out3 = ledger.record(
        AttemptFact(phase="development", agent="claude", artifact_valid=False)
    )
    assert next_out3.demoted


def test_varying_output_without_workspace_change_does_not_reset_streak() -> None:
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

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
    block = build_retry_error_block(failure_summary="test failure")
    assert block.startswith("ERROR RECOVERY REQUIRED")


def test_build_retry_error_block_references_prompt_only_by_path() -> None:
    block = build_retry_error_block(
        failure_summary="test failure",
        prompt_path="/path/to/prompt.md",
    )
    assert "Original prompt: `/path/to/prompt.md`" in block
    assert "There must be no inlined prompt content" not in block


def test_build_retry_error_block_tells_agent_not_to_restart() -> None:
    block = build_retry_error_block(failure_summary="boom")
    assert "Do not restart the task from scratch" in block


def test_build_retry_error_block_contains_failure_summary() -> None:
    block = build_retry_error_block(failure_summary="REQUIRED_SUMMARY_TOKEN")
    assert "REQUIRED_SUMMARY_TOKEN" in block
    assert "PREVIOUS ATTEMPT FAILED" in block


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
