"""User-facing phrase for an auto-integration outcome.

Single mapping between the auto-integrate producer state (see
:mod:`ralph.pipeline.auto_integrate`) and the human-readable phrase
shown to the operator. Both the live activity line emitted by
``ralph.pipeline.runner`` and the end-of-run completion summary route
through this function so the two surfaces cannot drift.
"""

from __future__ import annotations

#: Outcome verbs recorded on ``RebaseState.last_action`` by the producer.
_ACTION_SKIPPED = "skipped"
_ACTION_REBASED = "rebased"
_ACTION_MERGED = "merged"
_ACTION_CONFLICT = "conflict"
_ACTION_RECOVERED = "recovered"


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


def format_auto_integrate_message(
    action: str | None,
    target: str | None,
    reason: str | None,
    *,
    fast_forwarded: bool = False,
) -> str:
    """Render the auto-integrate outcome into a single human-readable phrase.

    The four arguments are exactly the four fields the producer records on
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

    The unknown-verb fallback returns a bare ``f"{action}"`` rather than
    ``f"auto-integrate: {action}"``; the single ``auto-integrate:`` prefix
    is added at the emit site, and emitting a second one here previously
    doubled the prefix in the live line.
    """
    normalized = action or "noop"

    if normalized == _ACTION_REBASED:
        base = "rebased onto target" + (f" ({target})" if target else "")
        message = base + _fast_forward_suffix(
            target=target, fast_forwarded=fast_forwarded, reason=reason
        )
    elif normalized == _ACTION_MERGED:
        base = "merged target into feature" + (f" ({target})" if target else "")
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
    else:
        message = f"{normalized}"

    return message


__all__ = ["format_auto_integrate_message"]
