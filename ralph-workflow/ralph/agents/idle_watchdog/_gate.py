"""Smart-fire gate helpers for :class:`IdleWatchdog`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind, classify_stuck
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog

def classify_stuck_now(
    self: IdleWatchdog,
    *,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot | None = None,
) -> StuckKind:
    """Build the classifier inputs from the watchdog's own state and return the kind.

    This is a thin wrapper that calls the pure ``classify_stuck``
    function with the watchdog's own per-channel evidence summary,
    the cached ``is_waiting_state``, the live connectivity state,
    a noop ``classify_quiet`` that always returns ``ACTIVE``, and
    the configured TTL.

    When ``corroboration`` is provided, the live ``CorroborationSnapshot``
    is threaded into the classifier as the canonical "live child"
    input. The classifier's CURRENT verdict policy is INTENTIONALLY
    NON-DECISIVE on corroboration alone: the value is plumbed so the
    gate can surface the live corroboration at every fire path (the
    analysis-feedback contract for ``CHILDREN_PERSIST_TOO_LONG`` and
    ``NO_OUTPUT_AT_START``: the gate must see the LIVE corroboration,
    not the stale ``self._last_alive_by`` post-fire field which is
    only populated post-fire by ``NO_PROGRESS_QUIET``), but the
    classifier does NOT change its verdict based on the
    corroboration alone. The watchdog's own evaluators
    (``_is_no_progress_quiet``, ``_effective_waiting_ceiling``) own
    the ``alive_by``-driven deferrals; the classifier labels the
    apparent stall, it does not re-derive the wait/defer verdict
    from a different snapshot. See ``ClassifyStuckInputs.corroboration``
    and the ``test_corroboration_*`` regression tests in
    ``tests/agents/idle_watchdog/test_stuck_classifier.py`` for the
    full contract.

    The classifier's ``WAITING_ON_CHILD`` and ``RESUMABLE_CONTINUE``
    branches are intentionally NOT consulted from the gate. The
    watchdog enters the WAITING_ON_CHILD branch precisely because
    the previous ``classify_quiet()`` call returned
    ``WAITING_ON_CHILD``; consulting the same callable again from
    the gate would always report ``LOADING`` and defer every
    cumulative-ceiling fire -- the dumb-kill protection the gate
    is supposed to provide becomes a deadlock. The
    ``subagent_liveness`` channel (which the classifier consults
    BEFORE the ``classify_quiet`` branches) is the real signal
    for "live child": a live OS descendant / subagent process
    keeps the channel fresh, so ``LOADING`` wins via that branch
    first. When the corroboration does not see a live child
    (e.g. a deadlocked agent whose child has exited) the
    ``classify_quiet`` branches must NOT veto the fire.

    The live ``classify_quiet`` is still consulted by
    ``evaluate()`` itself to decide which branch to enter; the
    gate's call site is the boundary between "which branch am I
    in" (live signal) and "is the agent actually stuck" (noop
    signal). The watchdog stores the most recent callable in
    ``self._classify_quiet_provider`` for diagnostic exposure
    (e.g. ``last_evidence_summary`` consumers and the dumb-kill
    regression tests in
    ``tests/agents/idle_watchdog/test_smart_verdict_dumb_kills.py``
    that exercise the gate's deferral via the
    ``subagent_liveness`` channel).

    The function is intentionally side-effect free: it does not
    update any watchdog state, does not log, and does not mutate
    the fire reason. The gate is the side-effect boundary.
    """
    summary = self.last_evidence_summary(now)
    connectivity = self._current_connectivity_state()

    def _noop_classify_quiet() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    return classify_stuck(
        is_waiting_state=self._is_waiting_state,
        connectivity_state=connectivity,
        evidence_summary=summary,
        classify_quiet=_noop_classify_quiet,
        activity_evidence_ttl_seconds=self._config.activity_evidence_ttl_seconds,
        silent_subagent_seconds=self._config.silent_subagent_seconds,
        corroboration=corroboration,
    )


def gate_fire(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    *,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot | None = None,
) -> WatchdogVerdict:
    """Smart-verdict gate: defer non-absolute fires the classifier names non-STUCK.

    The absolute ``SESSION_CEILING_EXCEEDED`` reason is the ONLY
    reason that bypasses the gate -- it is an operator-set
    hard cap (session wall-clock), not a stuck-detection signal.
    Every other reason -- including ``CHILDREN_PERSIST_TOO_LONG``,
    ``NO_OUTPUT_DEADLINE``, ``NO_OUTPUT_AT_START``,
    ``STALLED_AFTER_TOOL_RESULT``, ``REPEATED_ERROR_LOOP``,
    ``NO_PROGRESS_QUIET``, and the post-exit reasons -- is gated:
    the watchdog consults ``classify_stuck`` and returns CONTINUE
    (with a debug log naming the kind) for any non-STUCK kind.

    When the caller supplies a live ``corroboration`` snapshot, it
    is threaded into the classifier as the canonical "live child"
    input (the analysis-feedback contract for
    ``CHILDREN_PERSIST_TOO_LONG`` and ``NO_OUTPUT_AT_START``).
    Without this parameter the classifier would only see the
    process-monitor subagent_liveness channel -- a corroborator-only
    live signal would be invisible to the gate. The classifier's
    CURRENT verdict policy does NOT change based on the
    corroboration alone; the watchdog's own evaluators own the
    ``alive_by``-driven deferrals. The corroboration parameter is
    exposed so future classifier extensions can use it without
    changing the call site.

    The helper returns the final verdict the caller should use:
    FIRE for an allowed fire, CONTINUE for a deferred fire. The
    helper is the single boundary between the fire-decision helpers
    and the verdict-returning logic; the helpers that compute a
    candidate fire (e.g. _handle_waiting_branch,
    _post_tool_result_stalled, _evaluate_no_progress_quiet,
    _evaluate_no_output_at_start) call this helper to decide
    whether the fire is actually allowed.
    """
    if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        return WatchdogVerdict.FIRE
    kind = self._classify_stuck_now(
        now=now, idle_elapsed=idle_elapsed, corroboration=corroboration
    )
    if kind == StuckKind.STUCK:
        return WatchdogVerdict.FIRE
    # Diagnostic-only kind (SILENT_SUBAGENT) gets its OWN
    # ``_last_fire_reason`` label so operators can see WHY a
    # would-be fire was deferred ("a subagent dispatched then went
    # silent for >180s").  Without this branch, every non-STUCK
    # deferral collapses to ``DEFERRED_BY_STUCK_CLASSIFIER`` and
    # the SILENT_SUBAGENT diagnostic is invisible at the
    # ``last_fire_reason`` surface.  See AC-05 + analysis
    # feedback for the runtime contract.
    if kind == StuckKind.SILENT_SUBAGENT:
        self._last_fire_reason = WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
        self._last_deferred_kind = kind
        # Coarse single-key throttle: caps emissions to one DEBUG
        # record per ``watchdog_log_throttle_seconds`` per fire_reason
        # regardless of how the deferred_kind cycles (e.g.
        # SILENT_SUBAGENT -> DUPLICATE_KILL -> SILENT_SUBAGENT, the
        # kind-cycle scenario pinned by
        # ``test_log_spam_throttle_public_surface_kind_cycle_via_public_surface``).
        # The PROMPT's observed spam was IDENTICAL SILENT_SUBAGENT
        # messages (the per-tuple throttle handles that case); the
        # coarse throttle is the defense for hypothetical regressions
        # that cause the deferred_kind to cycle, where the per-tuple
        # throttle would MISS because the key changes on every call.
        # The per-tuple throttle (``_maybe_log_deferred``) is
        # consulted FIRST so the kind label is preserved in the
        # ``_last_deferred_log_at`` map; the coarse throttle ONLY
        # suppresses the duplicate emission when the per-tuple key
        # has already been logged within the throttle window.
        coarse_allowed = self._maybe_log_any_deferred(fire_reason, now)
        if coarse_allowed and self._maybe_log_deferred(
            fire_reason, kind, idle_elapsed, now
        ):
            self._log.debug(
                "idle watchdog: silent subagent (deferred) reason={} idle_elapsed={}s",
                fire_reason,
                round(idle_elapsed, 1),
            )
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
    self._last_deferred_kind = kind
    coarse_allowed = self._maybe_log_any_deferred(fire_reason, now)
    if coarse_allowed and self._maybe_log_deferred(
        fire_reason, kind, idle_elapsed, now
    ):
        self._log.debug(
            "idle watchdog: deferred fire reason={} kind={} idle_elapsed={}s",
            fire_reason,
            kind,
            round(idle_elapsed, 1),
        )
    return WatchdogVerdict.CONTINUE


def maybe_log_deferred(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    deferred_kind: StuckKind,
    idle_elapsed: float,
    now: float,
) -> bool:
    """Return True (and stamp the throttle map) when a deferred DEBUG
    emission is allowed for this ``(fire_reason, deferred_kind)`` key.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    while a fire was deferred; per-tick DEBUG emission is log spam.
    This helper consults ``self._last_deferred_log_at`` and the
    configured ``watchdog_log_throttle_seconds`` to keep emissions
    to at most one per ``(fire_reason, deferred_kind)`` key per
    throttle window.

    Returns True when:
      - the key has never been logged (initial transition), OR
      - ``now - last_logged_at >= watchdog_log_throttle_seconds``
        (the throttle window has elapsed since the prior emission).

    Returns False when ``now - last_logged_at < watchdog_log_throttle_seconds``
    (the emission would be a duplicate).

    The map is updated on every call that returns True so a
    subsequent call within the throttle window returns False.
    """
    key = (fire_reason.value, deferred_kind.value)
    last = self._last_deferred_log_at.get(key)
    throttle = self._config.watchdog_log_throttle_seconds
    if last is None or (now - last) >= throttle:
        self._last_deferred_log_at[key] = now
        return True
    return False


def maybe_log_any_deferred(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    now: float,
) -> bool:
    """Coarse single-key throttle for ``_gate_fire`` deferred emissions.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    with IDENTICAL ``SILENT_SUBAGENT`` deferred_kind messages. The
    per-tuple throttle alone caps those emissions at one per
    throttle window per ``(fire_reason, deferred_kind)`` tuple.
    The coarse single-key throttle is the defense for HYPOTHETICAL
    regressions where the deferred_kind cycles between calls
    (e.g. ``SILENT_SUBAGENT`` -> ``DUPLICATE_KILL`` ->
    ``SILENT_SUBAGENT``, the kind-cycle scenario pinned by
    ``test_log_spam_throttle_public_surface_kind_cycle_via_public_surface``):
    when the deferred_kind changes, the per-tuple throttle key
    changes too, so the per-tuple throttle MISSES the duplicate
    emission -- the duplicate IS still a duplicate from the
    operator's perspective, and the coarse throttle catches it.

    This helper is the COARSE single-key throttle: it caps
    emissions to AT MOST one DEBUG record per
    ``watchdog_log_throttle_seconds`` per ``fire_reason.value``
    REGARDLESS of how the ``deferred_kind`` cycles. The per-tuple
    throttle is consulted FIRST (by ``_gate_fire``) so the kind
    label is preserved in ``_last_deferred_log_at``; this helper
    only suppresses the duplicate emission when the per-tuple key
    has already been logged within the throttle window.

    Returns True when:
      - the fire_reason key has never been logged, OR
      - ``now - last_logged_at >= watchdog_log_throttle_seconds``.

    Returns False when ``now - last_logged_at < throttle`` -- the
    emission would be a duplicate of the most recent DEBUG
    record for this fire_reason.

    The map is updated on every call that returns True.
    """
    last = self._last_any_deferred_log_at.get(fire_reason.value)
    throttle = self._config.watchdog_log_throttle_seconds
    if last is None or (now - last) >= throttle:
        self._last_any_deferred_log_at[fire_reason.value] = now
        return True
    return False
