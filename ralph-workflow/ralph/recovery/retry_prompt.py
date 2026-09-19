"""Shared formatting helpers for technical retry prompts and retry hints."""

from __future__ import annotations

from typing import Final

VALIDATION_FAILURE_BANNER: Final[str] = "VALIDATION FAILURE"


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
) -> str:
    """Return a shared error-first retry block.

    The failure must lead the prompt. Original prompt and prior context paths are
    secondary references for continuing the same task after addressing the error.
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
                "Fix the underlying issue before submitting again. "
                "Do not resubmit unchanged work."
                if validation
                else "The exact cause may be unknown."
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
    return "\n".join(lines)


__all__ = [
    "VALIDATION_FAILURE_BANNER",
    "build_retry_error_block",
    "build_validation_retry_footer",
]
