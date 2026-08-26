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

    # Freshness is structured so seam control and display never infer safety
    # from human-readable reasons. ``verified`` means a healthy fetch or
    # shared-ref observation established the base; ``degraded`` is fail-open
    # remote unavailability; ``unverified`` is an intentionally suppressed
    # probe; ``unsafe`` blocks the feature rebase for this seam.
    freshness_verdict: str | None = None
    freshness_source: str | None = None
    freshness_safe: bool = True
    freshness_target_sha: str | None = None

    # Reclamation is structured separately from warning prose so checkpoints,
    # summaries, and recovery tooling can report destructive target-owner work
    # without parsing a log line. ``None`` / 0 preserve legacy checkpoints.
    reclaimed_worktree_path: str | None = None
    reclaim_snapshot_ref: str | None = None
    reclaim_discarded_path_count: int = 0

    # ``last_push`` records the fail-open configured-remote push after a
    # successful local landing. The summary is operator-visible but never
    # changes the landing result. The field is None when sync is disabled
    # or when the prior integration did not produce a record, so legacy
    # checkpoints load unchanged.
    last_push: str | None = None

    # ``last_remote_sync`` carries the latest opt-in remote-sync
    # outcome produced by
    # :mod:`ralph.pipeline.auto_integrate_remote_sync`. Distinct from
    # ``last_push`` (which is the free-form summary string) and from
    # ``last_refresh`` (which is the observe-only outcome); the value
    # is one of the ``REMOTE_*`` constants in that module and names
    # the higher-level verb (`pulled`, `reconciled`, `pushed`,
    # `push rejected`, `pending push`, ...). Defaults to ``None`` so
    # legacy checkpoints load unchanged.
    last_remote_sync: str | None = None

    # Configured remote used for the latest remote-sync outcome.
    last_remote: str | None = None

    # Structured classification of the latest publication attempt. This is
    # deliberately separate from ``last_push`` prose: retry decisions must
    # never parse operator display text. Defaults preserve old checkpoints.
    last_push_status: str | None = None

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
    last_conflict_paths: tuple[str, ...] = ()
    last_conflict_stage_oids: tuple[str, ...] = ()
    last_conflict_scope: str = ""

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

    @property
    def integration_unresolved(self) -> bool:
        """Whether a durable integration outcome still blocks phase advancement.

        Conflict resolution and retained crash-recovery records are both
        unresolved integration ownership. Callers must fail closed rather than
        materializing prompts, dispatching agents, or reporting success.
        """
        return self.last_action == "conflict" or self.recovery_record_retained
