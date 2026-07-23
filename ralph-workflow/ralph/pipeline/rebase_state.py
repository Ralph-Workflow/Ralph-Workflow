"""Rebase state model for pipeline state."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class RebaseState(RalphBaseModel):
    """State for git rebase operations.

    The legacy ``pending``/``in_progress``/``completed`` fields are
    preserved so existing checkpoints deserialize cleanly. The
    auto-integration fields (``last_action``/``last_reason``/
    ``last_target``/``fast_forwarded``) are the surface the runner
    reads to thread the integration outcome into the persisted
    checkpoint, and the surface operators see in run state when a
    skip or conflict is recorded. All four new fields have defaults
    so legacy checkpoints load without modification.
    """

    model_config = _FROZEN

    pending: bool = False
    in_progress: bool = False
    completed: bool = False

    # Auto-integration outcome. ``last_action`` is the high-level
    # verb (``rebased``/``merged``/``fast_forwarded``/``skipped``/
    # ``conflict``/``recovered``); ``last_reason`` is the human-readable
    # skip / failure reason when one applies; ``last_target`` is the
    # mainline branch the integration step targeted; ``fast_forwarded``
    # records whether the fast-forward phase actually advanced the
    # target ref (False on conflict, on dirty worktree, on
    # concurrent-target-update race, etc.). See
    # ``ralph.pipeline.auto_integrate`` for the producer of these
    # values.
    last_action: str | None = None
    last_reason: str | None = None
    last_target: str | None = None
    fast_forwarded: bool = False

    # ``last_refresh`` records the outcome of the origin refresh that
    # ran immediately before the fast-forward observed the target SHA
    # (one of the ``REFRESH_*`` values in
    # ``ralph.pipeline.auto_integrate_sync``). The refresh is fail-open
    # -- an unreachable remote still lands locally -- so this field is
    # the only signal that tells an operator whether the mainline
    # pointer just landed against was actually fresh. Defaulted, so
    # legacy checkpoints load unchanged.
    last_refresh: str | None = None

    # ``last_push`` records the outcome of the OPT-IN multi-remote
    # push that runs AFTER a successful local landing (the summary
    # string produced by
    # :func:`ralph.git.remote_push.push_branch_to_all_remotes`). The
    # push is fail-open and best-effort, so a partial push is recorded
    # as the operator-visible summary, not as a skipped landing. The
    # field is None when push is disabled, when there is no remote,
    # or when the previous integration did not produce a record, so
    # legacy checkpoints load unchanged.
    last_push: str | None = None

    # ``consecutive_conflicts`` counts unresolved integration conflicts
    # against ``last_target`` in a row. It bounds how often the
    # dev-agent conflict resolver is invoked for the same conflict (see
    # ``ralph.pipeline.auto_integrate_conflict_budget``) and resets to 0
    # on any successful land. Defaulted, so legacy checkpoints load
    # unchanged.
    consecutive_conflicts: int = 0

    # Durable identity of the conflict ``consecutive_conflicts`` counts.
    # ``last_target`` alone does not identify a conflict: a developer
    # can add a feature commit that changes what conflicts while the
    # mainline branch NAME stays ``main``, and other agents move the
    # mainline tip continuously. Without these two observations the
    # budget would keep suppressing the resolver for a conflict it has
    # never actually seen. The pair is the feature tip and the target
    # tip observed immediately before the integration attempt that
    # recorded the conflict; when either differs at the next seam the
    # budget starts fresh (see
    # ``ralph.pipeline.auto_integrate_conflict_budget.ConflictIdentity``).
    # Both are cleared on a successful land and both are defaulted, so
    # legacy checkpoints load unchanged and conservatively keep their
    # carried count.
    last_conflict_feature_sha: str | None = None
    last_conflict_target_sha: str | None = None

    # ``recovery_record_retained`` marks a startup crash-recovery outcome
    # that deliberately LEFT the durable ``IntegrationRecord`` on disk for
    # the next startup to retry (a failed abort, a failed reset, a target
    # pointer that could not be refreshed, a transient fast-forward
    # failure). Recovery still owns that record, so the caller must not
    # begin a fresh integration in the same startup: ``_integrate_once``
    # writes a new ``IntegrationRecord(phase='integrating', ...)`` before
    # it mutates anything, which would overwrite the only durable
    # metadata describing the interrupted operation.
    #
    # This is a STRUCTURED flag on purpose. The retention fact used to be
    # legible only from the free-form ``last_reason`` display text, which
    # no caller may parse. Produced by
    # ``ralph.pipeline.auto_integrate_recovery`` and read through its
    # ``recovery_retained_record`` predicate; defaulted, so legacy
    # checkpoints load unchanged and conservatively read as "not
    # retained".
    recovery_record_retained: bool = False
