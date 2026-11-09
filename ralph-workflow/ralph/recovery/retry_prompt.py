"""Shared formatting helpers for technical retry prompts and retry hints."""

from __future__ import annotations

from typing import Final

VALIDATION_FAILURE_BANNER: Final[str] = "VALIDATION FAILURE"

#: Banner that marks the premise-check instruction inside a retry prompt.
#: The marker makes the block unambiguous to a test (and to a reviewer
#: reading the prompt) without touching any other part of the prompt text.
PREMISE_CHECK_BANNER: Final[str] = "PREMISE CHECK"

#: The premise-check instruction, word for word as the kernel rule states
#: it. The agent never counts retries and never remembers earlier
#: attempts, so this deterministic formatter is the only channel that can
#: carry the instruction to a cold agent.
PREMISE_CHECK_INSTRUCTION: Final[str] = (
    "test the premise shared by the failed fixes before another patch"
)


def build_premise_check_block(*, same_failure_retry_count: int = 0) -> str:
    """Return the premise-check block for a repeated failure, or ``""``.

    The runtime knows how many times it has retried the SAME failure; the
    agent does not count retries and does not remember earlier attempts.
    The block is therefore emitted only when the runtime reports at least
    one retry of the same failure, and it is empty otherwise so that the
    prompt text is unchanged for a first-attempt retry.
    """
    if same_failure_retry_count < 1:
        return ""
    return (
        f"{PREMISE_CHECK_BANNER}\n"
        "The runtime has already retried this same failure: "
        f"{PREMISE_CHECK_INSTRUCTION}."
    )


def build_validation_retry_footer() -> str:
    """Return the final blocking instruction for a validation retry prompt."""
    return (
        f"{VALIDATION_FAILURE_BANNER}\n"
        "Fix the underlying issue before submitting again.\n"
        "Do not resubmit unchanged work."
    )


def build_retry_error_block(
    *,
    failure_summary: str,
    detail: str | None = None,
    prompt_path: str | None = None,
    context_path: str | None = None,
    validation: bool = False,
    same_failure_retry_count: int = 0,
) -> str:
    """Return a shared error-first retry block.

    The failure must lead the prompt. Original prompt and prior context paths are
    secondary references for continuing the same task after addressing the error.

    ``same_failure_retry_count`` is the runtime's count of retries of THIS
    SAME failure. When it is at least one, the premise-check block is appended
    (the agent cannot work that fact out for itself). The default keeps the
    prompt byte-identical to the pre-premise-check text.
    """
    lines = [
        VALIDATION_FAILURE_BANNER if validation else "ERROR RECOVERY REQUIRED",
        f"PREVIOUS ATTEMPT FAILED: {failure_summary}",
    ]
    if detail:
        lines.append(f"Best available detail: {detail}")
    lines.extend(
        [
            (
                "Fix the underlying issue before submitting again. Do not resubmit unchanged work."
                if validation
                else (
                    "The detail above names the cause."
                    if detail
                    else "The exact cause may be unknown."
                )
            ),
            *(
                []
                if validation
                else [
                    (
                        "If the signal above names a reason, use it; otherwise treat "
                        "transient or external issues (for example, an internet outage) "
                        "as possible contributors."
                    )
                ]
            ),
            (
                "Focus on resolving the failure above before continuing. "
                "Do not restart the task from scratch."
            ),
        ]
    )
    if prompt_path:
        lines.append(f"Original prompt: `{prompt_path}`")
    if context_path:
        lines.append(f"Previous context summary: `{context_path}`")
    premise_block = build_premise_check_block(same_failure_retry_count=same_failure_retry_count)
    if premise_block:
        lines.extend(("", premise_block))
    return "\n".join(lines)


__all__ = [
    "PREMISE_CHECK_BANNER",
    "PREMISE_CHECK_INSTRUCTION",
    "VALIDATION_FAILURE_BANNER",
    "build_premise_check_block",
    "build_retry_error_block",
    "build_validation_retry_footer",
]
