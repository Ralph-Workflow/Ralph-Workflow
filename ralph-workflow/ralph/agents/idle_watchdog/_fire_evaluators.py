"""Fire-evaluation helpers for :class:`IdleWatchdog`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.process.child_liveness import AliveBy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog

_FRESH_ALIVE_BY_STATES: frozenset[AliveBy] = frozenset(
    {AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY}
)


def _alive_by_is_fresh(alive_by: AliveBy | None) -> bool:
    return alive_by in _FRESH_ALIVE_BY_STATES

def is_no_progress_quiet(
    self: IdleWatchdog, now: float, corroboration: CorroborationSnapshot
) -> bool:
    """Return True when all no-progress quiet conditions are met.

    The dumb-kill floor (no_progress_quiet_minimum_invocation_seconds)
    is consulted FIRST so a recently-launched agent that is doing real
    thinking work (planning, exploration, dispatching subagents) but
    has not yet produced first-party activity evidence is not killed.
    When the floor field is None, the floor is disabled (not
    recommended). The SESSION_CEILING_EXCEEDED and the post-tool-result
    STALLED_AFTER_TOOL_RESULT paths are not affected by this floor.
    """
    if self._config.no_progress_quiet_seconds is None:
        return False
    # Heartbeat-only ceiling: a heartbeat-only subagent
    # (``AliveBy.FRESH_HEARTBEAT_ONLY`` -- alive per the corroborator
    # but no first-party progress) bypasses BOTH NO_PROGRESS_QUIET
    # (because ``alive_by is not None``) AND STRICTLY_STUCK
    # (because ``alive_by`` is FRESH so it is not in the strictly-stuck
    # stale set) so the watchdog would defer indefinitely until the
    # cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default
    # 600s). Without this branch a heartbeat-only subagent that
    # emits heartbeats but no real work runs for the full
    # cumulative ceiling -- too late. The dedicated heartbeat-only
    # ceiling (default 240s, sourced from
    # ``NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` in
    # ``ralph/timeout_defaults.py``; must be <= ``no_progress_quiet_seconds``
    # per the cross-field validator) trips NO_PROGRESS_QUIET when
    # the agent has been alive for at least
    # ``no_progress_quiet_heartbeat_ceiling_seconds``. This branch
    # MUST be checked BEFORE the ``invocation_elapsed_seconds <
    # no_progress_quiet_seconds`` short-circuit below so a heartbeat
    # ceiling shorter than ``no_progress_quiet_seconds`` can fire
    # EARLY (the whole point of the operator knob). It MUST also be
    # checked BEFORE the ``alive_by is not None`` short-circuit so
    # the heartbeat-only ceiling is consulted before the wt-012
    # gate refinement suppresses the evaluator. ``None`` disables
    # the heartbeat-only ceiling (operators can opt out via
    # ``[general]`` config). The dumb-kill floor
    # (``no_progress_quiet_minimum_invocation_seconds``) still
    # protects recently-launched agents from premature fires.
    if (
        corroboration.alive_by == AliveBy.FRESH_HEARTBEAT_ONLY
        and self._config.no_progress_quiet_heartbeat_ceiling_seconds is not None
        and self.invocation_elapsed_seconds
        >= self._config.no_progress_quiet_heartbeat_ceiling_seconds
        and (
            self._config.no_progress_quiet_minimum_invocation_seconds is None
            or self.invocation_elapsed_seconds
            >= self._config.no_progress_quiet_minimum_invocation_seconds
        )
    ):
        # Heartbeat-only branch tripped: a heartbeat-only subagent
        # (``AliveBy.FRESH_HEARTBEAT_ONLY`` -- alive per the
        # corroborator but no first-party progress) has been alive
        # past the heartbeat-only ceiling AND the dumb-kill floor
        # has elapsed. Without this branch the watchdog would
        # defer indefinitely until the cumulative
        # ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s).
        return True
    if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
        return False
    # Dumb-kill floor: defer the fire while the agent has been alive
    # for less than the configured floor. The floor must be checked
    # BEFORE the channel-evidence check so a recently-launched agent
    # with all channels stale still gets the floor protection.
    if (
        self._config.no_progress_quiet_minimum_invocation_seconds is not None
        and self.invocation_elapsed_seconds
        < self._config.no_progress_quiet_minimum_invocation_seconds
    ):
        return False
    # Defer the fire when the corroborator confirms ANY alive_by signal —
    # the child is alive (per AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    # CPU_IDLE_WHILE_ALIVE, LOG_STALE_WHILE_ALIVE, FRESH_HEARTBEAT_ONLY, or
    # STALE_LABEL_ONLY) so the cumulative CHILDREN_PERSIST_TOO_LONG ceiling
    # (default 600s) is the correct upper bound for live-child stalls, not
    # the 120s NO_PROGRESS_QUIET fire. NO_PROGRESS_QUIET now fires ONLY
    # when the corroborator returns no alive_by signal at all
    # (corroboration.alive_by is None — no live signal from the
    # corroborator) AND no fresh channel evidence is present (the agent is
    # silent and the channels are stale). When the corroborator returns
    # alive_by is None, the conservative policy preserves the old fire
    # path so legacy construction sites that do not set the signal
    # continue to behave identically.
    if corroboration.alive_by is not None:
        return False
    return not self._channel_evidence_active(now)


def evaluate_no_progress_quiet(  # noqa: PLR0911, PLR0912 - heartbeat-only + dumb-kill branches are independent fire paths
    self: IdleWatchdog, now: float, idle_elapsed: float
) -> WatchdogVerdict | None:
    """Evaluate if the watchdog should fire due to lack of progress.

    Two independent triggers consult the NO_PROGRESS_QUIET reason:

    1. The dumb-kill ceiling (``no_progress_quiet_seconds``): fires
       when the agent has been alive for at least the dumb-kill
       ceiling AND no first-party activity has been observed AND
       the corroborator reports no live-child signal
       (``alive_by is None``). The dumb-kill floor
       (``no_progress_quiet_minimum_invocation_seconds``) protects
       recently-launched agents.

    2. The heartbeat-only ceiling
       (``no_progress_quiet_heartbeat_ceiling_seconds``): fires
       when the corroborator reports ``AliveBy.FRESH_HEARTBEAT_ONLY``
       AND ``invocation_elapsed_seconds`` >= the heartbeat ceiling
       AND the dumb-kill floor has elapsed. This is an ORTHOGONAL,
       SHORTER ceiling that catches a heartbeat-only subagent
       (alive per the corroborator but no first-party progress)
       BEFORE the cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling
       (default 600s). The heartbeat-only ceiling fires the same
       ``NO_PROGRESS_QUIET`` reason to avoid widening
       ``WatchdogFireReason``.

    The heartbeat-only branch is evaluated FIRST so a heartbeat
    ceiling shorter than ``no_progress_quiet_seconds`` can fire
    early (the whole point of the operator knob). The dumb-kill
    branch is evaluated SECOND.

    Note: ``no_progress_quiet_seconds=None`` does NOT disable the
    heartbeat-only branch. The two ceilings are ORTHOGONAL; a
    ``None`` dumb-kill ceiling only disables the dumb-kill path,
    not the heartbeat-only path. The heartbeat-only path is the
    only stuck-job detector when the operator opts out of the
    dumb-kill ceiling (e.g. trusting heartbeats for long-running
    sessions but still wanting a heartbeat-only trip after N
    seconds). The early return at the top of this function only
    short-circuits when BOTH ceilings are disabled.
    """
    # Short-circuit only when BOTH ceilings are disabled. A None
    # ``no_progress_quiet_seconds`` (dumb-kill disabled) does NOT
    # block the heartbeat-only ceiling; the heartbeat-only branch
    # is consulted first.
    if (
        self._config.no_progress_quiet_seconds is None
        and self._config.no_progress_quiet_heartbeat_ceiling_seconds is None
    ):
        return None

    # Heartbeat-only ceiling (orthogonal, shorter ceiling).
    # Evaluated BEFORE the ``invocation_elapsed_seconds <
    # no_progress_quiet_seconds`` early-return so a heartbeat
    # ceiling shorter than the dumb-kill ceiling fires EARLY. The
    # corroborator snapshot is reused for the dumb-kill branch
    # below via ``_safe_corroborate`` cache when called inside
    # ``evaluate()`` (the tick-scoped cache). When called outside
    # ``evaluate()`` the cache is empty and the corroborator is
    # invoked directly; the second invocation is cheap because the
    # correlator is a callable that returns a snapshot.
    corroboration = self._safe_corroborate()
    if (
        corroboration.alive_by == AliveBy.FRESH_HEARTBEAT_ONLY
        and self._config.no_progress_quiet_heartbeat_ceiling_seconds is not None
        and self.invocation_elapsed_seconds
        >= self._config.no_progress_quiet_heartbeat_ceiling_seconds
        and (
            self._config.no_progress_quiet_minimum_invocation_seconds is None
            or self.invocation_elapsed_seconds
            >= self._config.no_progress_quiet_minimum_invocation_seconds
        )
    ):
        # Heartbeat-only branch fires the NO_PROGRESS_QUIET reason.
        # We deliberately reuse the gate path so a deferred
        # heartbeat-only fire still routes through the
        # StuckClassifier (e.g. operator override, smart-verdict
        # deferral). The diagnostic block tags the ceiling as
        # ``heartbeat_only`` so the operator can distinguish this
        # fire from a dumb-kill fire in the structured event.
        gate_verdict = self._gate_fire(
            WatchdogFireReason.NO_PROGRESS_QUIET,
            now=now,
            idle_elapsed=idle_elapsed,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
        self._last_alive_by = corroboration.alive_by
        diag: dict[str, object] = {
            "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
            "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
            "idle_elapsed": round(idle_elapsed, 1),
            "ceiling": self._config.no_progress_quiet_heartbeat_ceiling_seconds,
            "effective_ceiling": "heartbeat_only",
        }
        corr_diag = self._build_corroboration_diag(corroboration)
        for key, val in corr_diag.items():
            if key not in diag:
                diag[key] = val
        evidence_block, _ = self._build_evidence_summary_diag(now)
        for ev_key, ev_val in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_val
        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=round(self.invocation_elapsed_seconds, 1),
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.no_progress_quiet_heartbeat_ceiling_seconds,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} effective_ceiling=heartbeat_only"
            " idle_elapsed={}s invocation_elapsed={}s heartbeat_ceiling={}s",
            WatchdogFireReason.NO_PROGRESS_QUIET,
            round(idle_elapsed, 1),
            round(self.invocation_elapsed_seconds, 1),
            self._config.no_progress_quiet_heartbeat_ceiling_seconds,
        )
        return WatchdogVerdict.FIRE

    # Dumb-kill ceiling (standard NO_PROGRESS_QUIET path). Only
    # engaged when the heartbeat-only branch did NOT trip and the
    # invocation has been alive for at least the dumb-kill ceiling.
    if self._config.no_progress_quiet_seconds is None:
        return None
    if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
        return None
    if idle_elapsed < self._config.no_progress_quiet_seconds:
        return None

    if not self._is_no_progress_quiet(now, corroboration):
        return None

    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_PROGRESS_QUIET, now=now, idle_elapsed=idle_elapsed
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE

    self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
    # Capture the corroborator's alive_by signal at the moment of
    # the fire. NO_PROGRESS_QUIET is the only fire path where
    # live-child vs dead-child differentiation matters; other
    # fire helpers (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG,
    # NO_OUTPUT_AT_START, etc.) do not need to capture alive_by.
    # The signal is consumed by IdleWatchdogKilledError.child_alive
    # so the failure classifier can read the live-child signal
    # end-to-end via the typed exception's __cause__ chain. When
    # corroboration.alive_by is None, child_alive will be False
    # (truly dead child -> Rule 2: exponential backoff).
    self._last_alive_by = corroboration.alive_by
    diag = {
        "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        "ceiling": self._config.no_progress_quiet_seconds,
        "effective_ceiling": "no_progress_quiet",
    }
    corr_diag = self._build_corroboration_diag(corroboration)
    for key, val in corr_diag.items():
        if key not in diag:
            diag[key] = val
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val

    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=self._config.no_progress_quiet_seconds,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._log.warning(
        "idle watchdog: FIRE reason={} idle_elapsed={}s invocation_elapsed={}s",
        WatchdogFireReason.NO_PROGRESS_QUIET,
        round(idle_elapsed, 1),
        round(self.invocation_elapsed_seconds, 1),
    )
    return WatchdogVerdict.FIRE


def evaluate_strictly_stuck(  # noqa: PLR0911 - early-exit guards per branch of the strictly-stuck state machine
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
) -> WatchdogVerdict | None:
    """Evaluate the STRICTLY_STUCK orthogonal ceiling for stuck-but-alive jobs.

    Fires ``WatchdogFireReason.STRICTLY_STUCK`` when ALL of the following
    are true:

    1. ``self._config.no_progress_quiet_strictly_stuck_seconds`` is not
       None (the ceiling is enabled).
    2. The corroborator reports ``alive_by`` in the strictly-stuck
       set ``{OS_DESCENDANT_ONLY_STALE_PROGRESS, CPU_IDLE_WHILE_ALIVE,
       LOG_STALE_WHILE_ALIVE}``.
    3. The agent has been in this strictly-stuck alive_by state for at
       least ``no_progress_quiet_strictly_stuck_seconds`` (tracked
       via the ``_strictly_stuck_run_started_at`` field).
    4. NO first-party channel is fresh (a productive agent in this
       state would be emitting stdout / tool calls / workspace
       events; the lack of any fresh channel means the agent is
       genuinely silent).

    The ceiling is ORTHOGONAL to ``NO_PROGRESS_QUIET`` (which
    requires ``alive_by is None``) and to ``CHILDREN_PERSIST_TOO_LONG``
    (which fires on the cumulative wall-clock). The new ceiling is
    tuned for the stuck-but-alive case which is too lenient to be
    caught by the standard 600s ``CHILDREN_PERSIST_TOO_LONG`` ceiling
    but too noisy to be caught by ``NO_PROGRESS_QUIET`` (the agent
    IS technically alive).

    Returns ``WatchdogVerdict.FIRE`` when the conditions are met AND
    the smart-verdict gate allows the fire (a fresh subagent
    liveness signal in the corroborator at this very tick will
    defer). Returns ``WatchdogVerdict.CONTINUE`` when the gate
    defers. Returns ``None`` when the ceiling is not engaged.
    """
    if self._config.no_progress_quiet_strictly_stuck_seconds is None:
        # Reset the run counter so a future enable starts fresh.
        self._strictly_stuck_run_started_at = None
        return None
    _strictly_stuck_alive_by = (
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
    )
    if corroboration.alive_by not in _strictly_stuck_alive_by:
        # Transition OUT of the strictly-stuck alive_by set: reset
        # the run counter so a brief liveness gap does not accumulate
        # across runs.
        self._strictly_stuck_run_started_at = None
        return None
    if self._strictly_stuck_run_started_at is None:
        self._strictly_stuck_run_started_at = now
        return None
    strictly_stuck_run_seconds = now - self._strictly_stuck_run_started_at
    if strictly_stuck_run_seconds < self._config.no_progress_quiet_strictly_stuck_seconds:
        return None
    if self._channel_evidence_active(now):
        # A first-party channel is fresh (mcp_tool, subagent_output,
        # workspace) -- the agent is making forward progress on a
        # non-stdout channel; defer.
        return None
    # Route through the gate so a fresh subagent_liveness signal
    # in the corroborator can defer (defense-in-depth, mirrors the
    # NO_OUTPUT_AT_START gate pattern at line 1252-1256).
    gate_verdict = self._gate_fire(
        WatchdogFireReason.STRICTLY_STUCK,
        now=now,
        idle_elapsed=idle_elapsed,
        corroboration=corroboration,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.STRICTLY_STUCK
    diag: dict[str, object] = {
        "alive_by": corroboration.alive_by.value
        if corroboration.alive_by is not None
        else None,
        "strictly_stuck_run_seconds": round(strictly_stuck_run_seconds, 1),
        "strictly_stuck_ceiling_seconds": (
            self._config.no_progress_quiet_strictly_stuck_seconds
        ),
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        # Populate ``scoped_child_active`` so the 3 consumer
        # sites never fall through to the ``?`` fallback.
        "scoped_child_active": (
            corroboration.scoped_child_active
            if corroboration.scoped_child_active is not None
            else False
        ),
    }
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val
    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(strictly_stuck_run_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=self._config.no_progress_quiet_strictly_stuck_seconds,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._log.warning(
        "idle watchdog: FIRE reason={} idle_elapsed={}s"
        " strictly_stuck_run_seconds={}s alive_by={}",
        WatchdogFireReason.STRICTLY_STUCK,
        round(idle_elapsed, 1),
        round(strictly_stuck_run_seconds, 1),
        corroboration.alive_by,
    )
    return WatchdogVerdict.FIRE


def evaluate_no_output_at_start(  # noqa: PLR0911 - 3 early-exit guards + 2 deferral gates + final verdict path; each is a distinct condition
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict | None:
    """Evaluate if the watchdog should fire due to no output at start.

    Fires when the agent has been alive for no_output_at_start_seconds with
    ZERO recorded activity (no stdout, no tool call, no file change, no
    subagent output). This is different from NO_PROGRESS_QUIET which fires
    inside WAITING_ON_CHILD deferral when the agent HAS produced output at
    some point but is now stuck with stale-progress evidence.

    Defers (returns ``None``) before the gate when ANY of the following
    live signals is present:

    - ``classify_quiet()`` returns ``AgentExecutionState.WAITING_ON_CHILD``
      -- the execution strategy has already classified the run as
      waiting on a live child.  This early-exit prevents the prompt
      false-positive where a subagent dispatched at invocation start
      caused ``NO_OUTPUT_AT_START`` to fire at 30s before the
      WAITING_ON_CHILD deferral path (``_handle_waiting_branch``)
      could consult its 600s cumulative ceiling.  The cumulative
      ``CHILDREN_PERSIST_TOO_LONG`` ceiling remains the correct upper
      bound for live-child stalls.
    - ``self._safe_corroborate()`` returns a ``CorroborationSnapshot``
      whose ``alive_by`` is a FRESH corroboration state -- the
      corroborator (process tree / OS descendant scan / heartbeat)
      confirms a live child agent with a recent progress or
      heartbeat signal. The helper ``_alive_by_is_fresh(...)``
      (driven by ``_FRESH_ALIVE_BY_STATES`` = ``{FRESH_PROGRESS,
      FRESH_HEARTBEAT_ONLY}``) returns True ONLY for those two
      states. Stale ``AliveBy`` values (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
      ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
      ``STALE_LABEL_ONLY``) and ``None`` DO NOT defer: they describe
      a child that has stopped producing fresh evidence and the
      short ``NO_OUTPUT_AT_START`` kill MUST still apply. The
      ``self._last_alive_by`` field is intentionally NOT consulted
      here: it is only populated post-fire by ``NO_PROGRESS_QUIET``
      at line 620 and is never set for ``NO_OUTPUT_AT_START``.
      Reading the stale field would never trigger (when
      ``NO_PROGRESS_QUIET`` has never fired) or forever suppress
      ``NO_OUTPUT_AT_START`` after a prior ``NO_PROGRESS_QUIET``
      fire.
    - ``self._cumulative_waiting_on_child_seconds > 0`` -- the agent
      has already survived a full ``WAITING_ON_CHILD`` entry/exit
      cycle this invocation, which demonstrates it is alive enough
      that ``NO_OUTPUT_AT_START`` no longer applies.
    """
    if (
        self._config.no_output_at_start_seconds is None
        or self._has_meaningful_output
        or self._last_meaningful_output_at is None
        or (now - self._last_meaningful_output_at) < self._config.no_output_at_start_seconds
    ):
        return None
    # NOTE: NO_OUTPUT_AT_START has NO dumb-kill floor. The dumb-kill
    # floor (``no_progress_quiet_minimum_invocation_seconds``) is
    # ONLY consulted inside ``_is_no_progress_quiet`` for
    # ``NO_PROGRESS_QUIET``. The 30s/60s ``NO_OUTPUT_AT_START``
    # short ceiling fires on a truly silent ACTIVE run regardless
    # of how recently the agent was launched -- a freshly-launched
    # agent that never produces any channel evidence (stdout, MCP
    # tool call, file change, subagent progress) inside the
    # ``no_output_at_start_seconds`` window is a stuck process and
    # the short kill MUST fire. The 120s default dumb-kill floor
    # is intentionally NOT consulted here so the operator's
    # documented ``no_output_at_start_seconds`` threshold is the
    # single source of truth for ``NO_OUTPUT_AT_START`` lifetime.
    # ``classify_quiet()`` (waiting / subagent deferral) and the
    # corroborator's alive_by (FRESH subagent deferral) are still
    # consulted below as the canonical subagent deferral paths.
    try:
        quiet_state = classify_quiet()
    except Exception:
        quiet_state = AgentExecutionState.ACTIVE
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return None
    if quiet_state not in {
        AgentExecutionState.ACTIVE,
        AgentExecutionState.WAITING_ON_CHILD,
    }:
        return None
    if self._channel_evidence_active(now):
        return None
    # Defer when the LIVE corroborator reports a FRESH live-child
    # signal. We MUST call ``_safe_corroborate()`` here, NOT read
    # ``self._last_alive_by``: that field is only populated post-fire
    # by ``NO_PROGRESS_QUIET`` (line 620) so it carries stale state
    # from a prior fire and is never useful as a pre-fire deferral
    # signal for ``NO_OUTPUT_AT_START``. The LIVE call returns the
    # fresh snapshot from the corroborator at the moment of this
    # evaluation.
    #
    # Stale alive_by values (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
    # ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
    # ``STALE_LABEL_ONLY``) DO NOT defer: they describe a child
    # that has stopped producing fresh evidence (process tree
    # presence only, no progress / no heartbeat, or log-truncated
    # state) and the NO_OUTPUT_AT_START short kill MUST still
    # apply. The earlier ``is not None`` check was a false-positive
    # deferral gate: a wedged startup where the corroborator
    # reports an OS-descendant-only stale progress state would
    # suppress the short kill and never reach ``_gate_fire`` /
    # StuckClassifier. The fix is to gate the deferral on the
    # fresh-evidence subset of ``AliveBy`` and let stale values
    # fall through to ``_gate_fire`` so the StuckClassifier sees
    # the live snapshot. See
    # ``TestNoOutputAtStartStaleAliveByDoesNotDefer`` in
    # ``tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py``
    # for the regression test that pins this behavior.
    corroboration = self._safe_corroborate()
    if _alive_by_is_fresh(corroboration.alive_by):
        return None
    # Defer when we have already accumulated ``WAITING_ON_CHILD`` time
    # this run; an agent that survived a full waiting run has
    # demonstrated it is alive enough that ``NO_OUTPUT_AT_START`` no
    # longer applies. The cumulative ceiling
    # (``max_waiting_on_child_seconds``) is the bounded upper bound
    # for live-child stalls; this gate is a NO_OUTPUT_AT_START-specific
    # early-out that prevents a false-positive kill when the
    # corroborator is not injected.
    if self._cumulative_waiting_on_child_seconds > 0.0:
        return None

    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_OUTPUT_AT_START,
        now=now,
        idle_elapsed=idle_elapsed,
        corroboration=corroboration,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE

    self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_AT_START
    diag: dict[str, object] = {
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "no_output_at_start_seconds": self._config.no_output_at_start_seconds,
        "last_activity_equals_started_at": True,
        # Populate ``scoped_child_active`` from the live corroboration
        # snapshot so the 3 consumer sites (subscriber.py:114,
        # _idle_stream_timeout_error.py:30,
        # _agent_inactivity_timeout_error.py:30) never fall through
        # to the ``?`` fallback. Default False when the
        # corroborator has no scoped child active.
        "scoped_child_active": (
            corroboration.scoped_child_active
            if corroboration.scoped_child_active is not None
            else False
        ),
    }
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val

    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=self._config.no_output_at_start_seconds,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._emit_fire_log(
        WatchdogFireReason.NO_OUTPUT_AT_START,
        now=now,
        idle_elapsed=idle_elapsed,
        message_suffix=(
            f" no_output_at_start_seconds={self._config.no_output_at_start_seconds}s"
        ),
    )
    return WatchdogVerdict.FIRE
