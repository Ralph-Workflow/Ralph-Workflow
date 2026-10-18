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

from ralph.display.integration_display_label import DISPLAY_LABEL_MAP as _DISPLAY_LABEL_MAP
from ralph.display.integration_display_label import IntegrationDisplayLabel

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


_OPERATION_DISPLAY_STATES: frozenset[str] = frozenset(s.value for s in OperationDisplayState) | {
    "integration",
    "publication",
    "intervention",
}

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

    The ``remote`` and ``target`` arguments are the configured
    names (sourced from ``remote_target_name(config)`` /
    ``configured_target(config)``) and are required for the
    ``RECONCILED`` verb: the message MUST reflect the workspace's
    configured target/remote so the display never falls back to
    the literal ``origin`` / ``main`` defaults (AC-5). When
    ``remote`` or ``target`` is missing on the ``RECONCILED`` path
    the message degrades to the configured-verb prefix only --
    the operator-facing summary still carries the remote-sync
    outcome, but the unrecoverable fallback literal is dropped.
    """
    if not remote_sync:
        return ""
    base = {
        _ACTION_PULLED: " [remote: pulled]",
        _ACTION_PUSHED: " [remote: pushed]",
        _ACTION_PUSH_REJECTED: " [remote: push rejected]",
    }.get(remote_sync)
    if base is not None:
        return base
    if remote_sync == _ACTION_RECONCILED and remote and target:
        return f" [remote: reconciled with {remote}/{target}]"
    if remote_sync == _ACTION_RECONCILED:
        return " [remote: reconciled]"
    return f" [remote: {remote_sync}]"


_LEGACY_ACTION_DISPLAY_MAP: dict[str, str] = {
    "integrating": OperationDisplayState.INTEGRATING.value,
    "integration": OperationDisplayState.INTEGRATING.value,
    "landing": OperationDisplayState.LANDING.value,
    "publishing": OperationDisplayState.PUBLISHING.value,
    "publication": OperationDisplayState.PUBLISHING.value,
    "conflict_resolution": OperationDisplayState.CONFLICT_RESOLUTION.value,
    "evidence_diagnosis": OperationDisplayState.EVIDENCE_DIAGNOSIS.value,
    "waiting": OperationDisplayState.WAITING.value,
    "intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value,
    "intervention": OperationDisplayState.INTERVENTION_REQUIRED.value,
    "terminal": OperationDisplayState.TERMINAL.value,
    "diagnosing": OperationDisplayState.EVIDENCE_DIAGNOSIS.value,
    "diagnostic_intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value,
    "completed": OperationDisplayState.TERMINAL.value,
    "rebased": OperationDisplayState.LANDING.value,
    "merged": OperationDisplayState.LANDING.value,
    "fast_forwarded": OperationDisplayState.LANDING.value,
    "fast_forward": OperationDisplayState.LANDING.value,
    "skipped": OperationDisplayState.TERMINAL.value,
    "conflict": OperationDisplayState.CONFLICT_RESOLUTION.value,
    "recovered": OperationDisplayState.TERMINAL.value,
    "remote_pushed": OperationDisplayState.TERMINAL.value,
    "remote_failed": OperationDisplayState.PUBLISHING.value,
    "unknown": OperationDisplayState.INTERVENTION_REQUIRED.value,
}

_ACTION_TO_DISPLAY_STATE: dict[str, OperationDisplayState] = {
    "resolve_active_operation": OperationDisplayState.CONFLICT_RESOLUTION,
    "conflict": OperationDisplayState.CONFLICT_RESOLUTION,
    "conflict_resolution": OperationDisplayState.CONFLICT_RESOLUTION,
    "continue_git_operation": OperationDisplayState.INTEGRATING,
    "reintegrate_against_target": OperationDisplayState.INTEGRATING,
    "integrating": OperationDisplayState.INTEGRATING,
    "integration": OperationDisplayState.INTEGRATING,
    "land_target": OperationDisplayState.LANDING,
    "landing": OperationDisplayState.LANDING,
    "rebased": OperationDisplayState.LANDING,
    "merged": OperationDisplayState.LANDING,
    "fast_forwarded": OperationDisplayState.LANDING,
    "publish_after_landing": OperationDisplayState.PUBLISHING,
    "publishing": OperationDisplayState.PUBLISHING,
    "publication": OperationDisplayState.PUBLISHING,
    "wait_for_bounded_retry": OperationDisplayState.WAITING,
    "waiting": OperationDisplayState.WAITING,
    "require_intervention": OperationDisplayState.INTERVENTION_REQUIRED,
    "intervention": OperationDisplayState.INTERVENTION_REQUIRED,
    "intervention_required": OperationDisplayState.INTERVENTION_REQUIRED,
    "diagnostic_intervention_required": OperationDisplayState.INTERVENTION_REQUIRED,
    "complete_with_proof": OperationDisplayState.TERMINAL,
    "terminal": OperationDisplayState.TERMINAL,
    "completed": OperationDisplayState.TERMINAL,
}


#: Closed-vocab evidence-diagnosis verbs that project to
#: ``OperationDisplayState.EVIDENCE_DIAGNOSIS``. Only the typed
#: ``is_diagnosis`` flag (raised by the authority on a verified
#: diagnosis) or one of these exact tokens is allowed to flip the
#: operator surface to the EVIDENCE_DIAGNOSIS label. Substring
#: matching on arbitrary prose is forbidden (invariant 13).
_DIAGNOSIS_ACTION_VERBS: frozenset[str] = frozenset(
    {
        "diagnosing",
        "evidence_diagnosis",
    }
)


def integration_action_to_operation_display_state(
    action: object,
    *,
    is_diagnosis: bool = False,
) -> OperationDisplayState | None:
    """Project an IntegrationAction or action verb to an OperationDisplayState.

    Evidence diagnosis is strictly distinguished and never returned as CONFLICT_RESOLUTION.
    """
    if action is None:
        return None
    raw = action.value if isinstance(action, StrEnum) else str(action)
    norm = raw.strip().lower()
    # Closed-vocab evidence-diagnosis tokens only; substring matching on
    # arbitrary prose is forbidden (invariant 13: prose cannot drive the
    # operator surface). The "is_diagnosis" flag is the typed call from
    # the authority; the lookup uses the same closed set as the rest of
    # the action vocabulary.
    if is_diagnosis:
        return OperationDisplayState.EVIDENCE_DIAGNOSIS
    if norm in _DIAGNOSIS_ACTION_VERBS:
        return OperationDisplayState.EVIDENCE_DIAGNOSIS
    return _ACTION_TO_DISPLAY_STATE.get(norm)


def _is_diagnosis_state(action_or_state: object, is_diagnosis: bool) -> bool:
    if is_diagnosis:
        return True
    # A settled intervention projection is decision-complete, never an open
    # diagnosis: the recovery-record retention that comes with it must not
    # reclassify it as evidence diagnosis (the unreadable-record handoff
    # carries both and must show 'intervention').
    settled_last_outcome = _safe_attr(action_or_state, "last_outcome")
    if settled_last_outcome is not None:
        settled_norm = (
            (
                settled_last_outcome.value
                if isinstance(settled_last_outcome, StrEnum)
                else str(settled_last_outcome)
            )
            .strip()
            .lower()
        )
        if settled_norm in ("intervention_required", "diagnostic_intervention_required"):
            return False
    diag_owner = _safe_attr(action_or_state, "diagnostic_ownership")
    retained_rec = _safe_attr(action_or_state, "recovery_record_retained")
    if diag_owner is not None or retained_rec is True:
        return True
    raw_last_outcome = _safe_attr(action_or_state, "last_outcome")
    if raw_last_outcome is not None:
        raw_norm = (
            (
                raw_last_outcome.value
                if isinstance(raw_last_outcome, StrEnum)
                else str(raw_last_outcome)
            )
            .strip()
            .lower()
        )
        if raw_norm in ("evidence_diagnosis", "diagnosing"):
            return True
    raw_disp = _safe_attr(action_or_state, "operation_display_state")
    if raw_disp is not None:
        cand_str = (
            (raw_disp.value if isinstance(raw_disp, StrEnum) else str(raw_disp)).strip().lower()
        )
        if cand_str in ("evidence_diagnosis", "diagnosing"):
            return True
    return False


def format_integration_label(
    action_or_state: object,
    *,
    is_diagnosis: bool = False,
) -> str:
    """Derive the canonical operator display label from an IntegrationAction or display state.

    Produces one of seven distinct labels:
    - 'conflict resolution'
    - 'evidence diagnosis'
    - 'integration'
    - 'landing'
    - 'publication'
    - 'waiting'
    - 'intervention'

    Evidence diagnosis is strictly distinguished and never shown or counted as conflict resolution.
    """
    if action_or_state is None:
        return IntegrationDisplayLabel.INTERVENTION.value
    if _is_diagnosis_state(action_or_state, is_diagnosis):
        return IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value

    raw_disp = _safe_attr(action_or_state, "operation_display_state")
    if raw_disp is not None:
        cand_str = (
            (raw_disp.value if isinstance(raw_disp, StrEnum) else str(raw_disp)).strip().lower()
        )
        mapped = _DISPLAY_LABEL_MAP.get(cand_str)
        if mapped is not None:
            return mapped

    raw_action = (
        _safe_attr(action_or_state, "next_action")
        or _safe_attr(action_or_state, "integration_action")
        or _safe_attr(action_or_state, "last_action")
    )
    target_obj = raw_action if raw_action is not None else action_or_state
    raw_val = (
        (target_obj.value if isinstance(target_obj, StrEnum) else str(target_obj)).strip().lower()
    )

    if raw_val in _DISPLAY_LABEL_MAP:
        return _DISPLAY_LABEL_MAP[raw_val]

    proj_state = rebase_outcome_to_operation_display_state(raw_val)
    if proj_state is not None and proj_state.value in _DISPLAY_LABEL_MAP:
        return _DISPLAY_LABEL_MAP[proj_state.value]

    return IntegrationDisplayLabel.INTERVENTION.value


def _safe_attr(obj: object, name: str) -> object:
    if obj is None:
        return None
    try:
        val: object = object.__getattribute__(obj, name)
        return val
    except AttributeError:
        return None


def rebase_outcome_to_operation_display_state(
    outcome: RebaseOutcomeKind | str | object | None,
) -> OperationDisplayState | None:
    """Project a RebaseOutcomeKind or outcome string to an OperationDisplayState.

    Single typed projection from last_outcome (and rebase.last_outcome) to
    OperationDisplayState (ADR-0007 / ADR-0008). Accepts a RebaseOutcomeKind,
    raw verb string, or a state/record object bearing ``last_outcome`` (or
    ``last_action``).
    """
    if outcome is None:
        return None
    extracted = _safe_attr(outcome, "last_outcome")
    if extracted is not None:
        outcome = extracted
    else:
        extracted_action = _safe_attr(outcome, "last_action")
        if extracted_action is not None:
            outcome = extracted_action
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
        raw_action = action.value if isinstance(action, StrEnum) else action
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
    if state_key in (OperationDisplayState.INTEGRATING, "integration"):
        base = "integrating" + (f" onto target{target_clause}" if target else "")
        if reason:
            message = f"{base}: {reason}"
        elif fast_forwarded:
            message = base + _fast_forward_suffix(
                target=target, fast_forwarded=fast_forwarded, reason=reason
            )
        else:
            message = base
    elif state_key in (OperationDisplayState.LANDING, "landing"):
        base = "landing" + target_clause
        message = base + _fast_forward_suffix(
            target=target, fast_forwarded=fast_forwarded, reason=reason
        )
    elif state_key in (OperationDisplayState.PUBLISHING, "publication"):
        dest = f" to {remote}" if remote else ""
        base = f"publishing{dest}{target_clause}"
        message = f"{base}: {reason}" if reason else base
    elif state_key in (
        OperationDisplayState.CONFLICT_RESOLUTION,
        "conflict_resolution",
        "conflict resolution",
    ):
        message = f"conflict resolution{target_clause}: {reason or 'unresolved conflict'}"
    elif state_key in (
        OperationDisplayState.EVIDENCE_DIAGNOSIS,
        "evidence_diagnosis",
        "evidence diagnosis",
    ):
        message = f"evidence diagnosis{target_clause}: {reason or 'diagnosing retained evidence'}"
    elif state_key in (OperationDisplayState.WAITING, "waiting"):
        message = f"waiting{target_clause}: {reason or 'retry pending'}"
    elif state_key in (
        OperationDisplayState.INTERVENTION_REQUIRED,
        "intervention_required",
        "intervention",
    ):
        message = (
            f"intervention required{target_clause}: {reason or 'manual intervention required'}"
        )
    elif state_key in (OperationDisplayState.TERMINAL, "terminal"):
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


def _extract_rebase_params(
    rebase: object,
) -> tuple[
    str | None,
    str | OperationDisplayState | None,
    object | None,
    str | None,
    str | None,
    str | None,
    bool,
]:
    raw_last_outcome = _safe_attr(rebase, "last_outcome")
    last_outcome = raw_last_outcome if isinstance(raw_last_outcome, str) else None

    raw_display_state = _safe_attr(rebase, "operation_display_state")
    operation_display_state = (
        raw_display_state if isinstance(raw_display_state, (str, OperationDisplayState)) else None
    )

    integration_action = _safe_attr(rebase, "next_action") or _safe_attr(
        rebase, "integration_action"
    )

    raw_act = _safe_attr(rebase, "last_action")
    action = raw_act if isinstance(raw_act, str) else None

    raw_target = _safe_attr(rebase, "last_target")
    target = raw_target if isinstance(raw_target, str) else None

    raw_reason = _safe_attr(rebase, "last_reason")
    reason = raw_reason if isinstance(raw_reason, str) else None

    fast_forwarded = bool(_safe_attr(rebase, "fast_forwarded"))

    return (
        last_outcome,
        operation_display_state,
        integration_action,
        action,
        target,
        reason,
        fast_forwarded,
    )


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
    rebase: object | None = None,
    integration_action: object | None = None,
) -> str:
    """Render the auto-integrate outcome into a single human-readable phrase.

    Reads ``operation_display_state`` first (the eight-branch typed lifecycle
    authority from ADR-0007); falls back to ``integration_action``, ``last_outcome``
    (or ``rebase.last_outcome``) projection, or ``action``/``reason`` only when
    the typed state is unset (legacy checkpoints).

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
    if rebase is not None:
        (
            rb_outcome,
            rb_display_state,
            rb_action_obj,
            rb_action,
            rb_target,
            rb_reason,
            rb_ff,
        ) = _extract_rebase_params(rebase)
        if last_outcome is None:
            last_outcome = rb_outcome
        if operation_display_state is None:
            operation_display_state = rb_display_state
        if integration_action is None:
            integration_action = rb_action_obj
        if action is None:
            action = rb_action
        if target is None:
            target = rb_target
        if reason is None:
            reason = rb_reason
        if not fast_forwarded and rb_ff:
            fast_forwarded = True

    effective_display_state = (
        operation_display_state
        if operation_display_state is not None
        else (
            integration_action_to_operation_display_state(integration_action)
            if integration_action is not None
            else (
                rebase_outcome_to_operation_display_state(last_outcome)
                if last_outcome is not None
                else None
            )
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
    "IntegrationDisplayLabel",
    "OperationDisplayState",
    "format_auto_integrate_message",
    "format_integration_label",
    "integration_action_to_operation_display_state",
    "rebase_outcome_to_operation_display_state",
]
