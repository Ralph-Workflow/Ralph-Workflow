"""Public exception raised when the same-shape retry loop exceeds its bound.

Lives in its own module so the audit_repo_structure ``one top-level
class per file`` policy is satisfied; the tracker
(``_same_shape_retry_tracker.SameShapeRetryTracker``) raises this
exception, and the recovery controller catches it to surface the
loop evidence instead of resuming.
"""

from __future__ import annotations


class SameShapeRetryLoopError(RuntimeError):
    """Raised when the same-shape retry loop exceeds the configured ceiling.

    Carries structured evidence so the operator can diagnose the loop
    without re-deriving it from logs:

      - ``fingerprint`` -- the four-tuple that matched across all
        consecutive attempts (fire_reason, diagnostic_signature,
        no_new_artifact_since_prior, workspace_change_since_prior).
      - ``consecutive`` -- the number of consecutive identical fires
        that triggered this exception (equals ``limit``).
      - ``limit`` -- the effective limit at the time of the bound fire.

    The fingerprint's two boolean fields are stored as ``True`` when
    the loop ran with NO progress between attempts, and ``False``
    when something did change; the operator-facing interpretation is
    "no artifact AND no workspace change" for ``(True, True)``.
    """

    def __init__(
        self,
        fingerprint: tuple[str, str, bool, bool],
        consecutive: int,
        limit: int,
    ) -> None:
        msg = (
            f"same-shape retry loop exceeded: {consecutive} consecutive identical"
            f" AgentInactivityTimeoutError resumes with fingerprint"
            f" (fire_reason={fingerprint[0]!r},"
            f" diagnostic_signature={fingerprint[1]!r},"
            f" no_new_artifact_since_prior={fingerprint[2]},"
            f" workspace_change_since_prior={fingerprint[3]});"
            f" effective limit={limit}"
        )
        super().__init__(msg)
        self.fingerprint = fingerprint
        self.consecutive = consecutive
        self.limit = limit


__all__ = ["SameShapeRetryLoopError"]
