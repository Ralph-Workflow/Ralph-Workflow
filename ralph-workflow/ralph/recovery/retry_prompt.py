"""Shared formatting helpers for technical retry prompts and retry hints."""

from __future__ import annotations


def build_retry_error_block(
    *,
    failure_summary: str,
    detail: str | None = None,
    prompt_path: str | None = None,
    context_path: str | None = None,
) -> str:
    """Return a shared error-first retry block.

    The failure must lead the prompt. Original prompt and prior context paths are
    secondary references for continuing the same task after addressing the error.
    """
    lines = [
        "ERROR RECOVERY REQUIRED",
        f"PREVIOUS ATTEMPT FAILED: {failure_summary}",
    ]
    if detail:
        lines.append(f"Best available detail: {detail}")
    lines.extend(
        [
            "The exact cause may be unknown.",
            (
                "If the signal above names a reason, use it; otherwise treat "
                "transient or external issues (for example, an internet outage) "
                "as possible contributors."
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


__all__ = ["build_retry_error_block"]
