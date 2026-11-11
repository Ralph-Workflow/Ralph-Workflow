
from __future__ import annotations


class RestartAttemptResult:

    BUDGET_EXHAUSTED = "budget_exhausted"
    ATTEMPT_FAILED = "attempt_failed"
    DEFERRAL_CEILING_TRIPPED = "deferral_ceiling_tripped"
    REPLACED = "replaced"
    KEPT_OLD = "kept_old"


__all__ = ["RestartAttemptResult"]
