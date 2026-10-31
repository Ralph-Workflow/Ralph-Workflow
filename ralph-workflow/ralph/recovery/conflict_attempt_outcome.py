"""Typed outcome of one conflict-resolution attempt, as handed to the recovery owner.

Recovery owns this vocabulary because it is the classifier's input: the
conflict-resolution pipeline produces these facts and
:meth:`ralph.recovery.controller.RecoveryController.classify_conflict_attempt`
consumes them. Keeping it here lets the pipeline import it without the
recovery layer importing the conflict-resolution package back.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ConflictAttemptOutcome"]


class ConflictAttemptOutcome(StrEnum):
    """Typed outcome of one conflict-resolution attempt (the §11 fix).

    The owner must be handed a typed fact, never a prose sentence to
    re-match: every shape Ralph itself can already describe typed is one of
    these values, and only genuinely unrecognised EXTERNAL output takes the
    ``RAW`` text path through the failure classifier.
    """

    UNRESOLVED_REMAINING = "unresolved_remaining"
    NO_ACTIVITY = "no_activity"
    ATTEMPT_FAILED = "attempt_failed"
    RAW = "raw"
