"""The closed outcome kind of one external git call.

:class:`GitOutcomeKind` is the CLOSED set of outcomes a git subprocess can
have. It lives in its own module so that every module in the git layer
defines at most ONE public top-level class (the repo-structure audit
enforces that rule), while :mod:`ralph.git.git_run_result` keeps
re-exporting the name for every caller that already imports it from there.

The kind travels WITH the cause (see
:data:`ralph.git._git_outcome.GitOutcome`): a caller can never observe a
``"failed"`` outcome without the reason that produced it, and can never
observe a ``"succeeded"`` outcome carrying a stale cause.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["GitOutcomeKind"]


class GitOutcomeKind(StrEnum):
    """The closed set of outcomes one external git call can have.

    No other value is a valid outcome. An unreadable or indeterminate
    reading is :attr:`UNKNOWN` and never :attr:`FAILED`.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"
    UNKNOWN = "unknown"
