"""User-facing phrase for an auto-integration outcome.

Single mapping between the auto-integrate producer state (see
:mod:`ralph.pipeline.auto_integrate`) and the human-readable phrase
shown to the operator. Both the live activity line emitted by
``ralph.pipeline.runner`` and the end-of-run completion summary route
through this function so the two surfaces cannot drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline._integration_action import RebaseOutcomeKind


class OperationDisplayState(StrEnum):
    """The closed vocabulary for the auto-integrate display state (ADR-0007)."""

    INTEGRATING = "integrating"
    LANDING = "landing"
    PUBLISHING = "publishing"
    CONFLICT_RESOLUTION = "conflict_resolution"
    EVIDENCE_DIAGNOSIS = "evidence_diagnosis"
    WAITING = "waiting"
    INTERVENTION_REQUIRED = "intervention_required"
    TERMINAL = "terminal"


_OPERATION_DISPLAY_STATES: frozenset[str] = frozenset(s.value for s in OperationDisplayState)

#: Outcome verbs recorded on ``RebaseState.last_action`` by the producer.
_ACTION_SKIPPED = "skipped"
_ACTION_REBASED = "rebased"
_ACTION_MERGED = "merged"
_ACTION_CONFLICT = "conflict"
_ACTION_RECOVERED = "recovered"

_LEGACY_ACTIONS: frozenset[str] = frozenset(
    {
        _ACTION_SKIPPED,
        _ACTION_REBASED,
        _ACTION_MERGED,
        _ACTION_CONFLICT,
        _ACTION_RECOVERED,
    }
)
#: Remote-sync verb recorded on ``RebaseState.last_remote_sync`` by the
#: opt-in remote sync tier. Rendered as a trailing ``[remote: <verb>]``
#: suffix on the live line and the completion summary, so the
#: operator sees the pull/reconcile/push outcome alongside the local
#: land without changing the headline action.
_ACTION_PULLED = "pulled"
_ACTION_RECONCILED = "reconciled"
_ACTION_PUSHED = "pushed"
_ACTION_PUSH_REJECTED = "push rejected"


def _fast_forward_suffix(
    *,
    target: str | None,
    fast_forwarded: bool,
    reason: str | None,
) -> str:
    """Append the fast-forward outcome to a rebased/merged base phrase.

    When ``fast_forwarded`` is True the suffix says ``, fast-forwarded
    <target>`` (or ``, fast-forwarded target`` when no target is known).
    When False a non-None ``reason`` is the fast-forward SKIP reason and
    is appended as ``, fast-forward skipped: <reason>``. Otherwise
    nothing is appended.
    """
    if fast_forwarded:
        return f", fast-forwarded {target}" if target else ", fast-forwarded target"
    if reason:
        return f", fast-forward skipped: {reason}"
    return ""


def _freshness_suffix(verdict: str | None) -> str:
    """Render the three operator-visible freshness classes without parsing prose."""
    labels = {
        "verified": "verified-fresh",
        "degraded": "degraded",
        "unverified": "unverified",
    }
    return f" [base: {labels.get(verdict, verdict)}]" if verdict else ""


def _refresh_suffix(refresh: str | None) -> str:
    """Append the pre-landing origin-refresh outcome, when one was recorded.

    The refresh is fail-open -- an unreachable or diverged mainline
    still lands locally -- so without this clause an integration
    computed against a stale pointer is indistinguishable from a
    healthy one in the operator-facing line.
    """
    return f" [target refresh: {refresh}]" if refresh else ""


def _push_suffix(push: str | None) -> str:
    """Append the opt-in configured-remote push summary, when recorded."""
    return f" [push: {push}]" if push else ""


def _remote_suffix(
    remote_sync: str | None, remote: str | None = None, target: str | None = None
) -> str:
    """Append the opt-in remote-sync verb, when one was recorded.

    The verb is one of the ``REMOTE_*`` constants in
    :mod:`ralph.pipeline.auto_integrate_remote_sync`. Rendered as a
    single trailing ``[remote: <verb>]`` so the operator sees the
    pull / reconcile / push outcome alongside the local land without
    changing the headline action. Distinct from :func:`_push_suffix`,
    which carries the free-form push summary string.
    """
    if not remote_sync:
        return ""
    if remote_sync == _ACTION_PULLED:
        return " [remote: pulled]"
    if remote_sync == _ACTION_RECONCILED:
        return f" [remote: reconciled with {remote or 'origin'}/{target or 'target'}]"
    if remote_sync == _ACTION_PUSHED:
        return " [remote: pushed]"
    if remote_sync == _ACTION_PUSH_REJECTED:
        return " [remote: push rejected]"
    return f" [remote: {remote_sync}]"


_LEGACY_ACTION_DISPLAY_MAP: dict[str, str] = {
    "integrating": OperationDisplayState.INTEGRATING.value,
    "landing": OperationDisplayState.LANDING.value,
    "publishing": OperationDisplayState.PUBLISHING.value,
    "conflict_resolution": OperationDisplayState.CONFLICT_RESOLUTION.value,
    "evidence_diagnosis": OperationDisplayState.EVIDENCE_DIAGNOSIS.value,
    "waiting": OperationDisplayState.WAITING.value,
    "intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value,
    "terminal": OperationDisplayState.TERMINAL.value,
    "diagnosing": OperationDisplayState.EVIDENCE_DIAGNOSIS.value,
    "diagnostic_intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value,
    "completed": OperationDisplayState.TERMINAL.value,
    "rebased": OperationDisplayState.LANDING.value,
    "merged": OperationDisplayState.LANDING.value,
    "fast_forwarded": OperationDisplayState.LANDING.value,
    "skipped": OperationDisplayState.TERMINAL.value,
    "conflict": OperationDisplayState.CONFLICT_RESOLUTION.value,
    "recovered": OperationDisplayState.TERMINAL.value,
    "remote_pushed": OperationDisplayState.TERMINAL.value,
    "remote_failed": OperationDisplayState.PUBLISHING.value,
}


def rebase_outcome_to_operation_display_state(
    outcome: RebaseOutcomeKind | str | None,
) -> OperationDisplayState | None:
    """Project a RebaseOutcomeKind or outcome string to an OperationDisplayState.

    Single typed projection from last_outcome to OperationDisplayState (ADR-0007 / ADR-0008).
    """
    if outcome is None:
        return None
    raw = outcome.value if isinstance(outcome, StrEnum) else str(outcome)
    state_str = _LEGACY_ACTION_DISPLAY_MAP.get(raw.strip().lower())
    if state_str is not None:
        return OperationDisplayState(state_str)
    return None


def _resolve_operation_display_state(
    operation_display_state: str | OperationDisplayState | None,
    action: str | None,
) -> str | None:
    if operation_display_state is not None:
        cand = (
            str(
                operation_display_state.value
                if isinstance(operation_display_state, OperationDisplayState)
                else operation_display_state
            )
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if cand in _OPERATION_DISPLAY_STATES:
            return cand
        return None

    if action is not None:
        raw_action = action.value if isinstance(action, OperationDisplayState) else action
        if isinstance(raw_action, str):
            raw_norm = raw_action.strip().lower()
            if raw_norm not in _LEGACY_ACTIONS:
                return _LEGACY_ACTION_DISPLAY_MAP.get(raw_norm)
    return None


def _format_typed_state_message(
    state_key: str,
    target: str | None,
    reason: str | None,
    *,
    fast_forwarded: bool,
    remote: str | None,
) -> str:
    target_clause = f" ({target})" if target else ""
    message = f"{state_key}"
    if state_key == OperationDisplayState.INTEGRATING:
        base = "integrating" + (f" onto target{target_clause}" if target else "")
        if reason:
            message = f"{base}: {reason}"
        elif fast_forwarded:
            message = base + _fast_forward_suffix(
                target=target, fast_forwarded=fast_forwarded, reason=reason
            )
        else:
            message = base
    elif state_key == OperationDisplayState.LANDING:
        base = "landing" + target_clause
        message = base + _fast_forward_suffix(
            target=target, fast_forwarded=fast_forwarded, reason=reason
        )
    elif state_key == OperationDisplayState.PUBLISHING:
        dest = f" to {remote}" if remote else ""
        base = f"publishing{dest}{target_clause}"
        message = f"{base}: {reason}" if reason else base
    elif state_key == OperationDisplayState.CONFLICT_RESOLUTION:
        message = f"conflict resolution{target_clause}: {reason or 'unresolved conflict'}"
    elif state_key == OperationDisplayState.EVIDENCE_DIAGNOSIS:
        message = f"evidence diagnosis{target_clause}: {reason or 'diagnosing retained evidence'}"
    elif state_key == OperationDisplayState.WAITING:
        message = f"waiting{target_clause}: {reason or 'retry pending'}"
    elif state_key == OperationDisplayState.INTERVENTION_REQUIRED:
        message = (
            f"intervention required{target_clause}: {reason or 'manual intervention required'}"
        )
    elif state_key == OperationDisplayState.TERMINAL:
        message = (
            f"terminal{target_clause}: {reason or 'completed'}"
            if reason
            else f"terminal{target_clause}"
        )
    return message


def _format_legacy_action_message(
    action: str | None,
    target: str | None,
    reason: str | None,
    *,
    fast_forwarded: bool,
) -> str:
    target_clause = f" ({target})" if target else ""
    normalized = action or "noop"
    message = f"{normalized}"
    if normalized == _ACTION_REBASED:
        base = "rebased onto target" + target_clause
        message = base + _fast_forward_suffix(
            target=target, fast_forwarded=fast_forwarded, reason=reason
        )
    elif normalized == _ACTION_MERGED:
        base = "merged target into feature" + target_clause
        message = base + _fast_forward_suffix(
            target=target, fast_forwarded=fast_forwarded, reason=reason
        )
    elif normalized == _ACTION_SKIPPED:
        message = f"skipped: {reason or 'no reason recorded'}"
    elif normalized == _ACTION_CONFLICT:
        message = f"conflict: {reason or 'unresolved conflict'}"
    elif normalized == _ACTION_RECOVERED:
        if reason:
            message = f"recovered ({reason})"
        elif fast_forwarded and target:
            message = f"recovered: fast-forwarded {target}"
        else:
            message = "recovered"
    return message


def format_auto_integrate_message(
    action: str | None = None,
    target: str | None = None,
    reason: str | None = None,
    *,
    fast_forwarded: bool = False,
    refresh: str | None = None,
    push: str | None = None,
    remote_sync: str | None = None,
    remote: str | None = None,
    freshness_verdict: str | None = None,
    operation_display_state: str | OperationDisplayState | None = None,
    last_outcome: RebaseOutcomeKind | str | None = None,
) -> str:
    """Render the auto-integrate outcome into a single human-readable phrase.

    Reads ``operation_display_state`` first (the eight-branch typed lifecycle
    authority from ADR-0007); falls back to ``last_outcome`` projection or
    ``action``/``reason`` only when the typed state is unset (legacy checkpoints).

    The four basic arguments are the four fields the producer records on
    :class:`ralph.pipeline.rebase_state.RebaseState`. The ``fast_forwarded``
    parameter is the only signal that distinguishes a successful land from a
    refused one, because the producer keeps ``last_action`` as
    ``rebased``/``merged`` in BOTH cases and records the fast-forward result
    on the boolean instead. A successful land renders
    ``rebased onto target (<target>), fast-forwarded <target>`` (or its
    ``merged`` variant); a refused land renders the same prefix followed by
    ``, fast-forward skipped: <reason>``.

    ``reason`` is treated as a fast-forward SKIP reason ONLY when
    ``fast_forwarded`` is False. When ``fast_forwarded`` is True any
    non-None ``reason`` is a stale :class:`RebaseNoOp` rebase reason
    (the producer retains it on a successful land) and must not be
    rendered as a skip -- doing so would emit a phantom
    ``fast-forward skipped`` on a healthy run.

    ``refresh`` is the ``RebaseState.last_refresh`` outcome of the origin
    refresh performed immediately before the fast-forward observed the
    target SHA. It is appended as ``[target refresh: <outcome>]`` when
    present, and omitted entirely when ``None`` (no refresh was recorded,
    e.g. a skip that short-circuited before the landing phase).

    ``push`` is the ``RebaseState.last_push`` summary produced by the
    opt-in push to the configured remote after a successful local landing.
    It is appended as ``[push: <summary>]`` when present, and omitted
    entirely when ``None``. The push is fail-open, so local success is
    unchanged regardless of what the push reported.

    The unknown-verb fallback returns a bare ``f"{action}"`` rather than
    ``f"auto-integrate: {action}"``; the single ``auto-integrate:`` prefix
    is added at the emit site, and emitting a second one here previously
    doubled the prefix in the live line.
    """
    effective_display_state = (
        operation_display_state
        if operation_display_state is not None
        else (
            rebase_outcome_to_operation_display_state(last_outcome)
            if last_outcome is not None
            else None
        )
    )
    state_key = _resolve_operation_display_state(effective_display_state, action)
    if state_key is not None:
        message = _format_typed_state_message(
            state_key,
            target,
            reason,
            fast_forwarded=fast_forwarded,
            remote=remote,
        )
    else:
        message = _format_legacy_action_message(
            action,
            target,
            reason,
            fast_forwarded=fast_forwarded,
        )

    return (
        message
        + _freshness_suffix(freshness_verdict)
        + _refresh_suffix(refresh)
        + _push_suffix(push)
        + _remote_suffix(remote_sync, remote, target)
    )


__all__ = [
    "OperationDisplayState",
    "format_auto_integrate_message",
    "rebase_outcome_to_operation_display_state",
]
