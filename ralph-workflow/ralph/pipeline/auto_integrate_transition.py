"""Closed transition table for :class:`~ralph.pipeline.auto_integrate_record.IntegrationRecord`.

Defines the :class:`Transition` descriptor and the authoritative closed set
of record state transitions. Ad-hoc ``model_copy(update=...)`` mutations are
forbidden; every durable record mutation must flow through one of the
registered transition instances with its validated field map and phase guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.auto_integrate_record import IntegrationRecord


class Transition:
    """A single record-mutation transition. Each call site uses one of the
    pre-defined transitions; ad-hoc ``model_copy(update=...)`` is no
    longer allowed.

    Transitions are intentionally lightweight descriptors (not an
    ``Enum``) so the table can be extended without breaking the
    public surface. The class-level ``ALL`` tuple is the closed set;
    the loader and the transition validator consult it.

    Each transition carries an optional ``from_state_guard`` -- a
    callable accepting the current :class:`IntegrationRecord` and
    returning ``True`` when the transition is legal at the record's
    current phase. ``with_transition`` consults the guard before
    delegating to ``model_copy(update=...)`` so a caller that tries
    to advance a record whose state does not permit the transition
    fails loudly rather than silently writing a contradicting
    record.
    """

    __slots__ = ("_field_map", "_from_state_guard", "_name")

    ALL: tuple[Transition, ...] = ()

    def __init__(
        self,
        name: str,
        field_map: dict[str, str] | None = None,
        from_state_guard: Callable[[IntegrationRecord], bool] | None = None,
    ) -> None:
        self._name = name
        # field_map maps a logical field name to a constructor kwarg
        # the apply() method will pass to model_copy. Keeping the
        # mapping here makes the table the single source of truth.
        self._field_map = field_map or {}
        self._from_state_guard = from_state_guard

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Transition({self._name!r})"

    @property
    def name(self) -> str:
        return self._name

    def is_legal_from(self, record: IntegrationRecord) -> bool:
        """Return ``True`` when this transition is legal at the record's current phase."""
        if self._from_state_guard is None:
            return True
        return self._from_state_guard(record)

    def apply(
        self,
        record: IntegrationRecord,
        **fields: object,
    ) -> dict[str, object]:
        """Validate the transition and return the update dict for ``model_copy``.

        The from-state guard runs first; a record whose phase does
        not permit this transition raises :class:`ValueError`. The
        field map is then enforced: only names registered for this
        transition are accepted, so callers cannot smuggle
        arbitrary keys past the table.
        """
        if not self.is_legal_from(record):
            raise ValueError(f"Transition {self._name!r} is not legal from phase={record.phase!r}")
        update: dict[str, object] = {}
        for key, value in fields.items():
            if key not in self._field_map:
                raise ValueError(
                    f"Transition {self._name!r} does not accept field {key!r}; "
                    f"expected one of {sorted(self._field_map)}"
                )
            update[self._field_map[key]] = value
        return update


# --- Concrete transitions ---


def _is_integrating(record: IntegrationRecord) -> bool:
    return record.phase in ("integrating",)


def _is_integrating_or_integrated(record: IntegrationRecord) -> bool:
    return record.phase in ("integrating", "integrated")


START_INTEGRATING = Transition(
    "START_INTEGRATING",
    {
        "phase": "phase",
        "target": "target",
        "pre_feature_sha": "pre_feature_sha",
        "pre_target_sha": "pre_target_sha",
        "integrated_feature_sha": "integrated_feature_sha",
        "operation_kind": "operation_kind",
        "owning_worktree": "owning_worktree",
        "initiating_feature_sha": "initiating_feature_sha",
        "reintegrate_pending": "reintegrate_pending",
    },
    from_state_guard=_is_integrating_or_integrated,
)

PROMOTE_TO_INTEGRATED = Transition(
    "PROMOTE_TO_INTEGRATED",
    {
        "phase": "phase",
        "integrated_feature_sha": "integrated_feature_sha",
        "resolving_rebase": "resolving_rebase",
        "resolving_merge": "resolving_merge",
        "rebase_continue_pending": "rebase_continue_pending",
        "rebase_continue_metadata": "rebase_continue_metadata",
        "rebase_restore_metadata": "rebase_restore_metadata",
        "rebase_continue_head": "rebase_continue_head",
        "rebase_continue_stop": "rebase_continue_stop",
        "rebase_continue_tree": "rebase_continue_tree",
        "rebase_continue_error": "rebase_continue_error",
        "merge_commit_pending": "merge_commit_pending",
        "reintegrate_pending": "reintegrate_pending",
    },
    from_state_guard=_is_integrating,
)

MARK_RESOLVING_REBASE = Transition(
    "MARK_RESOLVING_REBASE",
    {
        "resolving_rebase": "resolving_rebase",
        "resolving_merge": "resolving_merge",
        "pre_target_sha": "pre_target_sha",
    },
    from_state_guard=_is_integrating,
)

MARK_PENDING_REBASE_CONTINUE = Transition(
    "MARK_PENDING_REBASE_CONTINUE",
    {
        "rebase_continue_pending": "rebase_continue_pending",
        "rebase_continue_metadata": "rebase_continue_metadata",
        "rebase_restore_metadata": "rebase_restore_metadata",
        "rebase_continue_head": "rebase_continue_head",
        "rebase_continue_stop": "rebase_continue_stop",
        "rebase_continue_tree": "rebase_continue_tree",
        "rebase_continue_error": "rebase_continue_error",
    },
    from_state_guard=_is_integrating,
)

MARK_RESOLVING_MERGE = Transition(
    "MARK_RESOLVING_MERGE",
    {
        "resolving_merge": "resolving_merge",
        "resolving_paths": "resolving_paths",
        "resolving_stop_sha": "resolving_stop_sha",
    },
    from_state_guard=_is_integrating,
)

MARK_PENDING_MERGE_COMMIT = Transition(
    "MARK_PENDING_MERGE_COMMIT",
    {
        "merge_commit_pending": "merge_commit_pending",
        "merge_commit_head": "merge_commit_head",
        "merge_commit_parent": "merge_commit_parent",
        "merge_commit_tree": "merge_commit_tree",
    },
    from_state_guard=_is_integrating,
)

ACCEPT_REPAIR_EDITS = Transition(
    "ACCEPT_REPAIR_EDITS",
    {
        "repair_pending_diff": "repair_pending_diff",
        "repair_pending_paths": "repair_pending_paths",
        "repair_commit_controls": "repair_commit_controls",
        "repair_original_tree": "repair_original_tree",
    },
)

RESUME_REPAIR = Transition(
    "RESUME_REPAIR",
    {
        "repair_pending_diff": "repair_pending_diff",
        "repair_pending_paths": "repair_pending_paths",
        "repair_commit_controls": "repair_commit_controls",
    },
)

HOLD_DIAGNOSTIC_EVIDENCE = Transition(
    "HOLD_DIAGNOSTIC_EVIDENCE",
    {"diagnostic_evidence": "diagnostic_evidence", "diagnostic_ownership": "diagnostic_ownership"},
)

RELEASE_DIAGNOSTIC_EVIDENCE = Transition(
    "RELEASE_DIAGNOSTIC_EVIDENCE",
    {
        "diagnostic_evidence": "diagnostic_evidence",
        "diagnostic_ownership": "diagnostic_ownership",
        "diagnostic_intervention_required": "diagnostic_intervention_required",
        "repair_cap_reason": "repair_cap_reason",
    },
)

BUMP_DIAGNOSTIC_ATTEMPT = Transition(
    "BUMP_DIAGNOSTIC_ATTEMPT",
    {
        "merge_commit_repair_attempts": "merge_commit_repair_attempts",
        "repair_last_error": "repair_last_error",
    },
)

RAISE_DIAGNOSTIC_INTERVENTION = Transition(
    "RAISE_DIAGNOSTIC_INTERVENTION",
    {
        "diagnostic_intervention_required": "diagnostic_intervention_required",
        "repair_cap_reason": "repair_cap_reason",
    },
)

MARK_REINTEGRATE_PENDING = Transition(
    "MARK_REINTEGRATE_PENDING",
    {
        "reintegrate_pending": "reintegrate_pending",
        "owning_worktree": "owning_worktree",
    },
)

MARK_LEGACY_FIELDS_PRESERVED = Transition(
    "MARK_LEGACY_FIELDS_PRESERVED",
    {
        "diagnostic_evidence": "diagnostic_evidence",
        "diagnostic_ownership": "diagnostic_ownership",
        "repair_cap_reason": "repair_cap_reason",
    },
)

CLEAR_TRANSITION = Transition(
    "CLEAR",
    {},
)
CLEAR = CLEAR_TRANSITION


def _register_all_transitions() -> tuple[Transition, ...]:
    transitions = (
        START_INTEGRATING,
        PROMOTE_TO_INTEGRATED,
        MARK_RESOLVING_REBASE,
        MARK_PENDING_REBASE_CONTINUE,
        MARK_RESOLVING_MERGE,
        MARK_PENDING_MERGE_COMMIT,
        ACCEPT_REPAIR_EDITS,
        RESUME_REPAIR,
        HOLD_DIAGNOSTIC_EVIDENCE,
        RELEASE_DIAGNOSTIC_EVIDENCE,
        BUMP_DIAGNOSTIC_ATTEMPT,
        RAISE_DIAGNOSTIC_INTERVENTION,
        MARK_REINTEGRATE_PENDING,
        MARK_LEGACY_FIELDS_PRESERVED,
        CLEAR_TRANSITION,
    )
    seen: set[str] = set()
    for transition in transitions:
        if transition.name in seen:
            raise RuntimeError(f"duplicate transition name: {transition.name}")
        seen.add(transition.name)
    return transitions


Transition.ALL = _register_all_transitions()


_TRANSITIONS_BY_NAME: dict[str, Transition] = {t.name: t for t in Transition.ALL}


def transition_by_name(name: str) -> Transition:
    """Return the :class:`Transition` with the given name.

    The loader and the diagnostic path both call this; an unknown
    name is a programming error and is rejected loudly so a future
    refactor that drops a transition from the closed set is caught
    at import time, not at the call site.
    """
    try:
        return _TRANSITIONS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown transition: {name!r}") from exc


__all__ = [
    "ACCEPT_REPAIR_EDITS",
    "BUMP_DIAGNOSTIC_ATTEMPT",
    "CLEAR",
    "CLEAR_TRANSITION",
    "HOLD_DIAGNOSTIC_EVIDENCE",
    "MARK_LEGACY_FIELDS_PRESERVED",
    "MARK_PENDING_MERGE_COMMIT",
    "MARK_PENDING_REBASE_CONTINUE",
    "MARK_REINTEGRATE_PENDING",
    "MARK_RESOLVING_MERGE",
    "MARK_RESOLVING_REBASE",
    "PROMOTE_TO_INTEGRATED",
    "RAISE_DIAGNOSTIC_INTERVENTION",
    "RELEASE_DIAGNOSTIC_EVIDENCE",
    "RESUME_REPAIR",
    "START_INTEGRATING",
    "Transition",
    "transition_by_name",
]
