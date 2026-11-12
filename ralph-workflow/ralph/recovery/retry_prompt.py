from __future__ import annotations

import hashlib
from typing import Final

VALIDATION_FAILURE_BANNER: Final[str] = "VALIDATION FAILURE"
PREMISE_CHECK_LINE: Final[str] = "test the premise shared by the failed fixes before another patch"


def failure_fingerprint(failure_summary: str, diagnostic_identity: str = "") -> str:
    payload = "\x1f".join((failure_summary.strip(), diagnostic_identity.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def premise_check_line(current_fingerprint: str, prior_fingerprint: str | None) -> str:
    if not current_fingerprint or not prior_fingerprint:
        return ""
    if current_fingerprint != prior_fingerprint:
        return ""
    return PREMISE_CHECK_LINE


build_premise_check_block = premise_check_line


def build_validation_retry_footer() -> str:
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
    return "\n".join(lines)


__all__ = [
    "PREMISE_CHECK_LINE",
    "VALIDATION_FAILURE_BANNER",
    "build_premise_check_block",
    "build_retry_error_block",
    "build_validation_retry_footer",
    "failure_fingerprint",
    "premise_check_line",
]
