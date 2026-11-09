"""Black-box contract tests for the retry prompt's premise-check block.

The premise-check rule (v6 kernel) reaches a cold agent through exactly one
channel: the deterministic retry prompt formatter in
``ralph/recovery/retry_prompt.py``. The agent never counts retries and never
remembers earlier attempts, so the formatter carries the fact and appends the
instruction when (and only when) the runtime reports that it has already
retried the SAME failure.

The contract pinned here:

  * a retry the runtime reports as a repeat of the same failure carries the
    marked premise-check instruction;
  * a retry with no such report produces byte-identical text to the
    pre-premise-check prompt.

No real subprocess, no real network, no sleep, no file I/O.
"""

from __future__ import annotations

from ralph.recovery.retry_prompt import (
    PREMISE_CHECK_BANNER,
    PREMISE_CHECK_INSTRUCTION,
    build_premise_check_block,
    build_retry_error_block,
)

#: The exact prompt text a first (non-repeated) retry must still produce.
#: Pinned as a literal so any change to the existing retry text fails here
#: instead of silently altering what a cold agent reads.
_EXPECTED_FIRST_RETRY_TEXT = (
    "ERROR RECOVERY REQUIRED\n"
    "PREVIOUS ATTEMPT FAILED: the previous attempt failed because of AgentError\n"
    "The exact cause may be unknown.\n"
    "If the signal above names a reason, use it; otherwise treat transient or "
    "external issues (for example, an internet outage) as possible contributors.\n"
    "Focus on resolving the failure above before continuing. "
    "Do not restart the task from scratch.\n"
    "Original prompt: `/tmp/agent_prompt.md`\n"
    "Previous context summary: `/tmp/agent_context.md`"
)


def _first_retry_block() -> str:
    """Return the retry block a runtime emits before it repeats a failure."""
    return build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
    )


def _repeated_failure_block(*, same_failure_retry_count: int) -> str:
    """Return the retry block a runtime emits when it repeats a failure."""
    return build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
        same_failure_retry_count=same_failure_retry_count,
    )


# --- First retry: no premise-check text at all. ------------------------------


def test_first_retry_block_omits_the_premise_check_banner() -> None:
    """The banner never appears when the runtime has not repeated the failure."""
    assert PREMISE_CHECK_BANNER not in _first_retry_block()


def test_first_retry_block_omits_the_premise_check_instruction() -> None:
    """The instruction never appears when the runtime has not repeated the failure."""
    assert PREMISE_CHECK_INSTRUCTION not in _first_retry_block()


def test_first_retry_block_is_byte_identical_to_the_pinned_text() -> None:
    """The default call reproduces today's prompt text byte for byte."""
    assert _first_retry_block() == _EXPECTED_FIRST_RETRY_TEXT


def test_explicit_zero_retry_count_matches_the_default_call() -> None:
    """An explicit zero count is the same prompt as the default call."""
    explicit = build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
        same_failure_retry_count=0,
    )
    assert explicit == _EXPECTED_FIRST_RETRY_TEXT


def test_negative_retry_count_is_treated_as_a_first_retry() -> None:
    """A negative count cannot smuggle the premise check into the prompt."""
    negative = build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
        same_failure_retry_count=-1,
    )
    assert negative == _EXPECTED_FIRST_RETRY_TEXT


# --- Repeated failure: the marked premise check is appended. ----------------


def test_repeated_failure_block_carries_the_premise_check_banner() -> None:
    """The marker makes the appended instruction unambiguous in the prompt."""
    assert PREMISE_CHECK_BANNER in _repeated_failure_block(same_failure_retry_count=1)


def test_repeated_failure_block_carries_the_premise_check_instruction() -> None:
    """The instruction states the premise check a cold agent cannot infer."""
    block = _repeated_failure_block(same_failure_retry_count=1)
    assert PREMISE_CHECK_INSTRUCTION in block


def test_repeated_failure_block_keeps_the_existing_prompt_text() -> None:
    """Only an append happens: the pre-premise-check text is untouched."""
    block = _repeated_failure_block(same_failure_retry_count=2)
    assert block.startswith(_EXPECTED_FIRST_RETRY_TEXT)


def test_repeated_failure_block_appends_the_premise_check_once() -> None:
    """The instruction is not repeated when the runtime retries again."""
    block = _repeated_failure_block(same_failure_retry_count=3)
    assert block.count(PREMISE_CHECK_BANNER) == 1
    assert block.count(PREMISE_CHECK_INSTRUCTION) == 1


def test_validation_retry_block_also_carries_the_premise_check() -> None:
    """A validation retry of a repeated failure keeps both blocks."""
    block = build_retry_error_block(
        failure_summary="the previous attempt failed because of ArtifactValidation",
        validation=True,
        same_failure_retry_count=1,
    )
    assert block.startswith("VALIDATION FAILURE")
    assert PREMISE_CHECK_BANNER in block
    assert PREMISE_CHECK_INSTRUCTION in block


# --- The standalone helper follows the same retry-count condition. ----------


def test_helper_is_empty_without_a_retry() -> None:
    """No retry means no premise-check block."""
    assert build_premise_check_block() == ""
    assert build_premise_check_block(same_failure_retry_count=0) == ""


def test_helper_carries_the_marked_instruction_from_the_first_retry() -> None:
    """One retry of the same failure is enough to emit the block."""
    block = build_premise_check_block(same_failure_retry_count=1)
    assert block.startswith(PREMISE_CHECK_BANNER)
    assert PREMISE_CHECK_INSTRUCTION in block


def test_helper_is_empty_for_a_negative_retry_count() -> None:
    """A negative count is not a retry."""
    assert build_premise_check_block(same_failure_retry_count=-1) == ""
