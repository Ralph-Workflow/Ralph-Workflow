"""Black-box contract tests for the shared retry-prompt formatter.

``ralph/recovery/retry_prompt.py`` is the single formatter that turns a
runtime failure into the error-first block a cold agent reads. The
contract pinned here is the formatter's own output shape:

  * the failure leads the block, and the original prompt and the prior
    context are secondary references;
  * a validation retry uses the validation banner and carries the
    validation footer;
  * the same inputs always produce byte-identical text.

No real subprocess, no real network, no sleep, no file I/O.
"""

from __future__ import annotations

from ralph.recovery.retry_prompt import (
    VALIDATION_FAILURE_BANNER,
    build_retry_error_block,
    build_validation_retry_footer,
)

#: The exact prompt text the formatter produces for a first retry. Pinned
#: as a literal so any change to the retry text fails here instead of
#: silently altering what a cold agent reads.
_EXPECTED_RETRY_TEXT = (
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


def _retry_block() -> str:
    """Return the formatter output for the canonical retry inputs."""
    return build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
    )


def _retry_block_with_detail(detail: str) -> str:
    """Return the formatter output for a retry that carries a known cause."""
    return build_retry_error_block(
        failure_summary="the previous attempt failed because of AgentError",
        detail=detail,
        prompt_path="/tmp/agent_prompt.md",
        context_path="/tmp/agent_context.md",
    )


# --- The failure leads the block. -------------------------------------------


def test_retry_block_leads_with_the_failure() -> None:
    """The first two lines name the banner and the failure."""
    lines = _retry_block().splitlines()
    assert lines[0] == "ERROR RECOVERY REQUIRED"
    assert lines[1] == "PREVIOUS ATTEMPT FAILED: the previous attempt failed because of AgentError"


def test_retry_block_is_byte_identical_to_the_pinned_text() -> None:
    """The formatter output is pinned byte for byte."""
    assert _retry_block() == _EXPECTED_RETRY_TEXT


def test_retry_block_is_deterministic() -> None:
    """The same inputs always produce the same text."""
    assert _retry_block() == _retry_block()


# --- The detail is reported when the runtime has one. -----------------------


def test_retry_block_names_the_detail_when_present() -> None:
    """A known cause is reported verbatim."""
    block = _retry_block_with_detail("the agent crashed with exit code 2")
    assert "Best available detail: the agent crashed with exit code 2" in block
    assert "The detail above names the cause." in block


def test_retry_block_says_the_cause_may_be_unknown_without_detail() -> None:
    """An unknown cause is stated, not hidden."""
    assert "The exact cause may be unknown." in _retry_block()


# --- The secondary references come last. ------------------------------------


def test_retry_block_names_the_original_prompt_and_context() -> None:
    """The original prompt and the prior context are secondary references."""
    block = _retry_block()
    assert "Original prompt: `/tmp/agent_prompt.md`" in block
    assert "Previous context summary: `/tmp/agent_context.md`" in block


def test_retry_block_omits_absent_paths() -> None:
    """A block without paths carries no path lines."""
    block = build_retry_error_block(failure_summary="the previous attempt failed")
    assert "Original prompt:" not in block
    assert "Previous context summary:" not in block


# --- A validation retry uses the validation wording. ------------------------


def test_validation_retry_block_uses_the_validation_banner() -> None:
    """A validation retry leads with the validation banner."""
    block = build_retry_error_block(
        failure_summary="the previous attempt failed because of ArtifactValidation",
        validation=True,
    )
    assert block.startswith(VALIDATION_FAILURE_BANNER)
    assert "ERROR RECOVERY REQUIRED" not in block


def test_validation_retry_block_states_the_repair_instruction() -> None:
    """A validation retry tells the agent to repair and not resubmit."""
    block = build_retry_error_block(
        failure_summary="the previous attempt failed because of ArtifactValidation",
        validation=True,
    )
    assert "Fix the underlying issue before submitting again." in block
    assert "Do not resubmit unchanged work." in block


def test_validation_retry_footer_is_the_final_instruction() -> None:
    """The standalone validation footer carries the blocking instruction."""
    footer = build_validation_retry_footer()
    assert footer.splitlines()[0] == VALIDATION_FAILURE_BANNER
    assert "Fix the underlying issue before submitting again." in footer
    assert "Do not resubmit unchanged work." in footer
