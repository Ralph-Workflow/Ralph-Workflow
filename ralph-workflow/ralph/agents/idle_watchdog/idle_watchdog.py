"""Idle watchdog for detecting stalled agents.

Two-State Invariant
-------------------

The watchdog is one half of the recovery contract; the recovery
controller is the other. The pipeline can only enter TWO recovery
states; no third state is allowed:

  1. **Exponential backoff to the next agent** -- driven by
     ``AgentUnavailabilityTracker.mark_unavailable`` in
     ``ralph/recovery/agent_unavailability_tracker.py``. The current
     agent is marked unavailable for a per-reason backoff; the chain
     advances to the next agent whose cooldown has expired. The
     ``wrap=True`` re-arming in
     ``RecoveryController._next_available_agent_index`` reconsiders
     earlier agents whose cooldown has expired.

  2. **Retry with the same agent** -- driven by
     ``AgentChain.record_retry``. The same agent is retried in-place
     (chain.retries is incremented; the budget is debited but the
     chain index does not advance).

The watchdog contributes to state (1) only indirectly: when the
watchdog fires and the controller classifies the failure as
unavailable, the tracker applies the per-reason backoff. The
watchdog contributes to state (2) when it fires and the controller
classifies the failure as retryable.

Hard rules
----------

  - The watchdog NEVER calls ``sys.exit``, ``os._exit``, or
    ``raise SystemExit``. The run loop owns the exit decision.
  - The watchdog NEVER marks an agent as permanently unavailable.
    Every fire reason is transient; the cooldown math is owned by
    ``AgentUnavailabilityTracker`` and the only way for an agent to
    leave the unavailable set is for the cooldown to expire.
  - Every non-absolute fire is gated by the ``StuckClassifier``
    (``_stuck_classifier.py``) returning ``StuckKind.STUCK``. The
    absolute ``SESSION_CEILING_EXCEEDED`` reason is the ONLY reason
    that bypasses the gate (it is an operator-set hard cap, not a
    stuck-detection signal). Every other reason -- including
    ``CHILDREN_PERSIST_TOO_LONG`` -- is gated: the watchdog consults
    ``classify_stuck`` and returns CONTINUE for any non-STUCK kind
    so a productive session that has not yet been classified as
    "stuck" is not killed.
  - The watchdog is the sole owner of in-stream fire decisions;
    ``PostExitWatchdog`` is the sole owner of post-exit fire
    decisions. The import-time assertion on
    ``WatchdogFireReason.__members__`` (below) locks the enum set
    so a future PR cannot silently widen or narrow the fire set
    without updating the watchdog owner.

Channel freshness gate
----------------------

The ``evaluate()`` method consults a per-channel evidence summary
before returning ``WatchdogVerdict.FIRE``. A fire is deferred
(``WatchdogVerdict.CONTINUE``) when any of the following are true:

  - ``state.is_waiting_state`` is True (the pipeline has already
    committed to a wait -- this is the strongest signal and is
    checked first).
  - The connectivity monitor reports ``offline``.
  - A first-party channel (``mcp_tool`` or ``subagent_output``) is
    fresher than ``activity_evidence_ttl_seconds``.
  - The subagent-liveness side-channel is fresh.
  - The ``classify_quiet`` strategy returns ``WAITING_ON_CHILD`` or
    ``RESUMABLE_CONTINUE`` (these branches are evaluated by the
    live ``classify_quiet`` callable the watchdog receives from
    ``evaluate()`` -- the watchdog stores the most recent callable
    in ``self._classify_quiet_provider`` so the gate can consult it
    on every ``_classify_stuck_now`` call).

The classifier is a deterministic 7-kind enum (THINKING, LOADING,
WAITING_ON_CONNECTIVITY, TRANSITIONING, STUCK, DUPLICATE_KILL,
SILENT_SUBAGENT) and is a pure function of its inputs.
See ``_stuck_classifier.py`` for the full contract.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.idle_watchdog._evidence_tier import (
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
)
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.process.child_liveness import AliveBy

from ._active_branch import (
    build_evidence_summary_diag,
    emit,
    emit_fire_log,
    evaluate_final_verdict,
    evaluate_inner,
    handle_active_branch,
    handle_drain_window,
    handle_evidence_deferral,
    maybe_log_evidence_deferral,
    post_tool_result_stalled,
)
from ._active_branch import (
    evaluate as active_evaluate,
)
from ._activity_methods import (
    channel_summary,
    subagent_liveness_summary,
)
from ._activity_methods import (
    diagnostic_snapshot as activity_diagnostic_snapshot,
)
from ._activity_methods import (
    last_evidence_summary as activity_last_evidence_summary,
)
from ._activity_methods import (
    poll_subagent_output as activity_poll_subagent_output,
)
from ._activity_methods import (
    record_activity as activity_record_activity,
)
from ._activity_methods import (
    record_invocation_start as activity_record_invocation_start,
)
from ._activity_methods import (
    record_subagent_work as activity_record_subagent_work,
)
from ._activity_methods import (
    record_tool_call_activity as activity_record_tool_call_activity,
)
from ._activity_methods import (
    record_tool_result_activity as activity_record_tool_result_activity,
)
from ._activity_methods import (
    record_workspace_event as activity_record_workspace_event,
)
from ._fire_evaluators import (
    evaluate_no_output_at_start,
    evaluate_no_progress_quiet,
    evaluate_strictly_stuck,
    is_no_progress_quiet,
)
from ._gate import (
    classify_stuck_now,
    gate_fire,
    maybe_log_any_deferred,
    maybe_log_deferred,
)
from ._waiting_branch import (
    compute_effective_suspect,
    effective_ceiling_label,
    effective_waiting_ceiling,
    handle_waiting_branch,
)
from .corroboration_snapshot import (
    CorroborationSnapshot,
    WaitingCorroborator,
)
from .repetition_tracker import RepetitionTracker
from .waiting_status_kind import WaitingStatusKind
from .watchdog_fire_reason import WatchdogFireReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
    from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture

    from .timeout_policy import TimeoutPolicy
    from .waiting_status_event import WaitingStatusListener
    from .watchdog_verdict import WatchdogVerdict


# Lock the WatchdogFireReason enum set. IdleWatchdog is the sole owner of
# in-stream fire decisions; PostExitWatchdog is the sole owner of post-exit
# fire decisions. Any future addition (or removal) of a reason requires
# updating this assertion AND the watchdog owner's classification logic
# so a future PR cannot silently widen (or narrow) the fire set.
_EXPECTED_FIRE_REASONS: frozenset[str] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_DEADLINE.value,
        WatchdogFireReason.NO_OUTPUT_AT_START.value,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT.value,
        WatchdogFireReason.REPEATED_ERROR_LOOP.value,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL.value,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG.value,
        WatchdogFireReason.NO_PROGRESS_QUIET.value,
        WatchdogFireReason.STRICTLY_STUCK.value,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED.value,
        WatchdogFireReason.PROCESS_EXIT_HANG.value,
        WatchdogFireReason.DESCENDANT_HANG.value,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER.value,
    }
)
_actual = frozenset(member.value for member in WatchdogFireReason.__members__.values())
if _actual != _EXPECTED_FIRE_REASONS:
    missing = _EXPECTED_FIRE_REASONS - _actual
    extra = _actual - _EXPECTED_FIRE_REASONS
    msg = (
        "WatchdogFireReason.__members__ drifted from the IdleWatchdog owner's"
        " allowlist. The watchdog owner is the single source of truth for"
        " fire decisions. Missing:"
        f" {sorted(missing)}; extra: {sorted(extra)}."
        " Update BOTH this assertion AND the watchdog owner's classification"
        " logic so the fire decision is consistent with the new enum set."
    )


@dataclass
class IdleWatchdog:
    """Tracks agent idle time and decides when to fire the timeout.

    The watchdog owns the last_activity timestamp; the caller's loop must NEVER
    mutate `_last_activity` directly. Activity must flow through `record_activity()`,
    which preserves the cumulative WAITING_ON_CHILD ceiling while advancing the
    idle baseline. Direct resets here previously caused a false-negative bug where
    WAITING_ON_CHILD deferred the deadline forever.

    Cumulative WAITING_ON_CHILD time is an absolute ceiling that is preserved across
    every transition (heartbeat activity, drain windows, classify_quiet outcomes).
    Once recorded, cumulative time never decays during the session — this mirrors
    max_session_seconds semantics so neither ceiling can be defeated by a process
    that alternates between producing output and waiting on children.

    The session ceiling (max_session_seconds) is checked first on every evaluate()
    call and cannot be defeated by activity — record_activity() does not reset it.

    Status events are emitted via the optional listener.

    - ENTERED once when WAITING_ON_CHILD deferral begins.
    - PROGRESS at most once per waiting_status_interval_seconds (rate-limited).
    - SUSPECTED_FROZEN once per WAITING run when suspect threshold is crossed.
    - EXITED when transitioning out of WAITING_ON_CHILD.
    - HARD_STOP immediately before returning FIRE for CHILDREN_PERSIST_TOO_LONG.

    Listener exceptions are caught and logged at DEBUG; they never propagate.

    Per-channel activity evidence (NEW): the watchdog tracks three non-stdout
    channels in addition to the stdout baseline.

      - mcp_tool: MCP tools/call invocations/completions routed via the
        Ralph MCP server. Updated by ``record_mcp_tool_call``.
      - subagent: subagent progress signals (heartbeat, phase change) routed
        from the opencode child_liveness registry. Updated by
        ``record_subagent_work``.
      - workspace: workspace file change events captured by
        WorkspaceMonitor. Updated by ``record_workspace_event``, which is
        invoked by the readers' ``on_event`` callback passed to
        ``WorkspaceMonitor.set_on_event`` (the monitor is constructed in
        ``invoke_agent`` before the per-run watchdog exists, so the
        readers register the callback on the monitor immediately after
        the watchdog is created in ``read_lines``; the binding is
        cleared in the ``finally`` block so a stale callback can never
        fire after the run ends).

    The three recorders do NOT touch ``_last_activity`` (the stdout baseline);
    the existing "stdout only resets idle baseline" invariant is preserved.
    Instead, they update per-channel ``_last_at`` timestamps and counters. The
    verdict hook in ``evaluate()`` defers a NO_OUTPUT_DEADLINE fire when ANY
    non-stdout channel is fresher than ``activity_evidence_ttl_seconds``,
    returning CONTINUE with a debug log. Absolute ceilings
    (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG) are checked before
    the deferral hook and remain absolute.
    """

    _config: TimeoutPolicy
    _clock: Clock
    _last_activity: float = field(init=False)
    _session_started_at: float = field(init=False)
    _last_meaningful_output_at: float | None = field(default=None, init=False)
    _has_meaningful_output: bool = field(default=False, init=False)
    _invocation_started_at: float | None = field(default=None, init=False)
    _waiting_on_child_started_at: float | None = field(default=None, init=False)
    _cumulative_waiting_on_child_seconds: float = field(default=0.0, init=False)
    # STRICTLY_STUCK run counter. Set when the corroborator reports an
    # alive_by in the strictly-stuck set so the next call can compute
    # the elapsed run. Reset to None on transitions OUT of the strictly-
    # stuck alive_by set so a brief liveness gap does not accumulate.
    _strictly_stuck_run_started_at: float | None = field(default=None, init=False)
    _in_drain_window: bool = field(default=False, init=False)
    _drain_started_at: float | None = field(default=None, init=False)
    _last_fire_reason: WatchdogFireReason | None = field(default=None, init=False)
    # The ``StuckKind`` the gate used to defer the most recent
    # would-be fire.  ``None`` when the watchdog has not deferred a
    # fire yet OR when the most recent fire actually fired (the
    # kind is only set when ``_gate_fire`` returns CONTINUE).  The
    # field is the runtime surface for the SILENT_SUBAGENT
    # diagnostic described in AC-05: the watchdog's
    # ``last_fire_reason`` property collapses every non-FIRE
    # deferral to ``DEFERRED_BY_STUCK_CLASSIFIER``, but
    # ``last_deferred_kind`` retains the precise kind (e.g.
    # ``StuckKind.SILENT_SUBAGENT``) so an operator can see WHY a
    # would-be fire was deferred ("a subagent dispatched then went
    # silent for >180s").
    _last_deferred_kind: StuckKind | None = field(default=None, init=False)
    # Per-(fire_reason, deferred_kind) log throttle map. The PROMPT log showed
    # ~10 DEBUG records/sec at ``_gate_fire:949`` while a fire was deferred
    # (SILENT_SUBAGENT or generic non-STUCK kind) -- per-tick log spam. The
    # map keys on ``(fire_reason.value, deferred_kind.value)`` and stores the
    # monotonic timestamp of the most recent emission so a subsequent call
    # within ``watchdog_log_throttle_seconds`` is suppressed. Reset to empty
    # in ``record_invocation_start`` so a new invocation starts with an empty
    # throttle map; the throttle MUST survive long-lived WAITING runs but
    # MUST NOT carry state across invocations.
    _last_deferred_log_at: dict[tuple[str, str], float] = field(
        default_factory=dict, init=False
    )
    # Coarse single-key log throttle map for ``_gate_fire``. The PROMPT log
    # showed that ``_last_deferred_log_at`` (keyed on the tuple
    # ``(fire_reason, deferred_kind)``) cycles to a fresh key when the
    # ``deferred_kind`` transitions (e.g. SILENT_SUBAGENT -> LOADING ->
    # SILENT_SUBAGENT) which causes the per-tuple throttle to MISS and
    # re-emit a DEBUG record on every transition. This coarse map is keyed
    # on ``fire_reason.value`` ALONE and caps emissions to one DEBUG
    # record per ``watchdog_log_throttle_seconds`` per fire_reason,
    # regardless of how the deferred_kind cycles. The fine-grained
    # per-tuple throttle is still consulted FIRST so the kind label is
    # preserved in the throttle map; the coarse throttle ONLY suppresses
    # the duplicate emission when the per-tuple key has already been
    # logged within the throttle window. Reset to empty in
    # ``record_invocation_start`` so a new invocation starts with an
    # empty map (the coarse throttle must NOT carry state across
    # invocations).
    _last_any_deferred_log_at: dict[str, float] = field(
        default_factory=dict, init=False
    )
    # Per-channel log throttle map for ``_handle_evidence_deferral``.
    # Mirrors ``_last_deferred_log_at`` (which throttles ``_gate_fire``
    # DEBUG spam) but keys on the active channel name (mcp_tool /
    # subagent / workspace / none). The PROMPT log showed per-tick
    # debug spam at ``_handle_evidence_deferral`` while a session
    # stayed active only through non-stdout evidence; the same
    # ``watchdog_log_throttle_seconds`` window is reused so the
    # emission cadence stays aligned with the gate-throttle cadence.
    # Reset to empty in ``record_invocation_start`` so a new
    # invocation starts with an empty throttle map.
    _last_evidence_deferral_log_at: dict[str, float] = field(
        default_factory=dict, init=False
    )
    # Corroborator's alive_by signal at the moment of the most recent
    # NO_PROGRESS_QUIET fire. ``None`` when the watchdog has not fired
    # yet OR when the most recent fire was not NO_PROGRESS_QUIET
    # (other fire helpers do not capture alive_by because the
    # live-child vs dead-child differentiation only matters for the
    # NO_PROGRESS_QUIET path). Surfaced via ``last_alive_by`` and
    # consumed by ``IdleWatchdogKilledError.child_alive`` so the
    # failure classifier can read the live-child signal end-to-end
    # via the typed exception's ``__cause__`` chain.
    _last_alive_by: AliveBy | None = field(default=None, init=False)
    _last_waiting_status_at: float | None = field(default=None, init=False)
    _suspicion_announced_for_run: bool = field(default=False, init=False)
    # Post-tool-result progression state. The watchdog tracks when a
    # TOOL_RESULT activity was last recorded and whether we are still
    # waiting for the follow-up STREAM_DELTA/OUTPUT_LINE activity. When
    # ``_awaiting_post_tool_result_progression`` is True and the
    # configured ``post_tool_result_progression_seconds`` budget elapses
    # without a follow-up activity, the watchdog fires
    # STALLED_AFTER_TOOL_RESULT. This is a NEW BEHAVIOR: pre-fix, the
    # watchdog only fired NO_OUTPUT_DEADLINE at the full idle timeout,
    # which let the post-tool-result wedge linger for ~300s.
    _last_tool_result_at: float | None = field(default=None, init=False)
    _awaiting_post_tool_result_progression: bool = field(default=False, init=False)
    # Per-channel activity evidence state (NEW). The three recorders
    # ``record_mcp_tool_call``, ``record_subagent_work``, and
    # ``record_workspace_event`` only update these fields; they do NOT
    # touch ``_last_activity`` (the stdout baseline) or the cumulative
    # waiting-on-child ceiling. The verdict hook in ``evaluate()``
    # consults these fields via ``_channel_evidence_active`` and
    # ``last_evidence_summary``.
    _mcp_tool_call_count: int = field(default=0, init=False)
    _last_mcp_tool_call_at: float | None = field(default=None, init=False)
    _subagent_progress_count: int = field(default=0, init=False)
    _last_subagent_progress_at: float | None = field(default=None, init=False)
    # Throttle timestamp for the SUBAGENT_PROGRESS waiting-status emit
    # in ``_handle_waiting_branch``. Separate from
    # ``_last_subagent_progress_at`` (which is the channel-evidence
    # timestamp): this field tracks the LAST EMIT TIME so the emit
    # cadence is bounded by
    # ``TimeoutPolicy.watchdog_subagent_progress_interval_seconds``.
    _last_subagent_progress_emit_at: float | None = field(default=None, init=False)
    _subagent_output_count: int = field(default=0, init=False)
    _last_subagent_output_at: float | None = field(default=None, init=False)
    _workspace_event_count_internal: int = field(default=0, init=False)
    _last_workspace_event_at: float | None = field(default=None, init=False)
    _last_workspace_event_weight: float = field(default=0.0, init=False)
    # Subagent output capture state. The watchdog polls the injected
    # DiscoveryStrategy for output paths and reuses capture instances per
    # worker so only new lines are ingested as first-party evidence.
    # The cache is hard-bounded at ``_MAX_SUBAGENT_OUTPUT_CAPTURES``
    # (private constant in ``_activity_methods``). FIFO workers are
    # evicted when the cap binds (the OLDEST-INSERTED worker is
    # dropped first; there is no LRU refresh on poll). To prevent
    # the evicted workers' stateful captures from being immediately
    # recreated (which would re-emit historical lines), evicted
    # worker IDs are recorded in ``_evicted_worker_tombstones`` and
    # skipped on the next poll. The tombstone is itself bounded at
    # ``_MAX_EVICTED_TOMBSTONES`` and uses FIFO eviction. Tests
    # exercise the bound by generating enough workers to trigger the
    # production cap -- no DI seam is exposed on the public
    # ``IdleWatchdog`` constructor.
    _subagent_output_captures: OrderedDict[str, SubagentOutputCapture] = field(
        default_factory=OrderedDict, init=False
    )
    _evicted_worker_tombstones: OrderedDict[str, None] = field(
        default_factory=OrderedDict, init=False
    )
    # Per-kind workspace event counter. The watchdog tracks how many
    # file changes have been observed for each WorkspaceChangeKind
    # (source / log / cache / artifact / other) so the post-mortem
    # can see WHICH kinds were most active at the moment of a fire.
    # The workspace_kind_counts property returns a defensive copy.
    _workspace_kind_counts: dict[str, int] = field(default_factory=dict, init=False)
    # Smart-verdict gate state. The watchdog consults the StuckClassifier
    # before every non-absolute fire; the classifier returns one of six
    # kinds and the gate returns CONTINUE for any non-STUCK kind so a
    # productive session that does not look productive is not killed.
    # The two state fields below are the inputs the classifier needs
    # from the run-loop / connectivity monitor (the watchdog does not
    # own these signals itself):
    #   - is_waiting_state: True when the pipeline has already entered a
    #     wait state (the run loop will sleep and re-enter the phase).
    #     The classifier returns DUPLICATE_KILL when this is True so a
    #     second FIRE during a wait is impossible.
    #   - connectivity_state_provider: optional callable returning the
    #     current connectivity state label ("online" / "offline" /
    #     "unknown" / "degraded"). When "offline" the classifier returns
    #     WAITING_ON_CONNECTIVITY and the gate defers the fire. The
    #     callable is optional so the watchdog is constructible in tests
    #     without a real ConnectivityMonitor.
    _is_waiting_state: bool = field(default=False, init=False)
    _connectivity_state_provider: Callable[[], str | None] | None = field(default=None, init=False)
    # The most recent ``classify_quiet`` callable received by
    # ``evaluate()``. The gate (``_gate_fire``) consults the classifier
    # on every non-absolute fire, and the classifier's
    # ``WAITING_ON_CHILD`` / ``RESUMABLE_CONTINUE`` branches require a
    # live callable (a noop stub would always return ACTIVE and the
    # branches would never fire). Storing the callable here lets the
    # gate consult the same state the rest of ``evaluate()`` is
    # already consulting. ``None`` means ``evaluate()`` has not been
    # called yet; the gate falls back to a noop ACTIVE stub in that
    # case.
    _classify_quiet_provider: Callable[[], AgentExecutionState] | None = field(
        default=None, init=False
    )
    # Tick-scoped corroboration cache. ``evaluate()`` captures one
    # ``CorroborationSnapshot`` and reuses it for ALL sub-evaluators on
    # that tick (``_evaluate_no_output_at_start``,
    # ``_evaluate_strictly_stuck``, ``_evaluate_no_progress_quiet``,
    # ``_handle_waiting_branch``, etc.) so a single corroborator call
    # drives both the decision path and the diagnostic surface. Without
    # this cache the watchdog would call the corroborator once per
    # sub-evaluator, and a flaky corroborator that rotates alive_by
    # values on each call would produce inconsistent decisions vs.
    # diagnostics on the same tick (the bug the regression test
    # ``test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics``
    # pins). The cache is only consulted while ``_evaluate_tick_active``
    # is ``True``; outside an ``evaluate()`` call ``_safe_corroborate()``
    # bypasses the cache and invokes the corroborator directly so stale
    # out-of-band reads cannot feed into later watchdog probes
    # (``test_safe_corroborate_bypasses_cache_outside_evaluate`` pins
    # this contract).
    _tick_corroboration: CorroborationSnapshot | None = field(default=None, init=False)
    # Explicit "evaluate tick active" flag. The cache alone is not a
    # sufficient sentinel because ``_tick_corroboration is None`` is
    # overloaded: it means BOTH "no tick active" AND "tick active, cache
    # not yet populated". Storing a snapshot on the bypass path
    # (outside ``evaluate()``) would poison subsequent bypass-path reads
    # with stale liveness evidence. This flag makes the "tick active"
    # state unambiguous so ``_safe_corroborate()`` can route outside
    # calls to the raw corroborator without touching the cache.
    _evaluate_tick_active: bool = field(default=False, init=False)

    def __init__(
        self,
        config: TimeoutPolicy,
        clock: Clock,
        listener: WaitingStatusListener | None = None,
        *,
        corroborator: WaitingCorroborator | None = None,
        process_monitor: ProcessMonitor | None = None,
        connectivity_state_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._listener = listener
        self._corroborator = corroborator
        self._process_monitor = process_monitor
        self._connectivity_state_provider = connectivity_state_provider
        now = clock.monotonic()
        self._last_activity = now
        self._session_started_at = now
        self._invocation_started_at = None
        self._last_meaningful_output_at = None
        self._has_meaningful_output = False
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None
        self._last_deferred_kind = None
        self._last_deferred_log_at = {}  # bounded-accumulator-ok: drained on removal
        self._last_any_deferred_log_at = {}  # bounded-accumulator-ok: drained on removal
        self._last_evidence_deferral_log_at = {}  # bounded-accumulator-ok: drained on removal
        self._last_waiting_status_at = None
        self._suspicion_announced_for_run = False
        self._last_tool_result_at = None
        self._awaiting_post_tool_result_progression = False
        self._mcp_tool_call_count = 0
        self._last_mcp_tool_call_at = None
        self._subagent_progress_count = 0
        self._last_subagent_progress_at = None
        # Optional human-readable description of the most recent subagent
        # observation (truncated to 200 chars). Surfaced via the
        # ``subagent_activity`` field on ``WaitingStatusEvent``s so
        # operators see what the subagent was doing at the moment of
        # the event (transition, suspicion, fire). Reset to ``None`` in
        # ``record_invocation_start`` and updated by ``record_subagent_work``.
        self._last_subagent_progress_description: str | None = None
        self._default_subagent_activity_listener: WaitingStatusListener | None = None
        self._subagent_output_count = 0
        self._last_subagent_output_at = None
        # bounded-accumulator-ok: FIFO cap _MAX_SUBAGENT_OUTPUT_CAPTURES=128
        # (OrderedDict.popitem(last=False) eviction in poll_subagent_output)
        self._subagent_output_captures: OrderedDict[str, SubagentOutputCapture] = OrderedDict()  # bounded-accumulator-ok  # noqa: E501  # type: ignore[var-annotated]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        # wt-024 iteration-4 (AC-04): bounded eviction tombstone
        # for the hard-FIFO subagent output capture cache. Tracks
        # recently-evicted worker IDs so they cannot be immediately
        # re-added (which would re-emit historical lines). See
        # ``_activity_methods.poll_subagent_output`` for the
        # eviction policy.
        # bounded-accumulator-ok: FIFO cap _MAX_EVICTED_TOMBSTONES
        # (OrderedDict.popitem(last=False) eviction in poll_subagent_output)
        self._evicted_worker_tombstones: OrderedDict[str, None] = OrderedDict()  # bounded-accumulator-ok  # noqa: E501  # type: ignore[var-annotated]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        self._workspace_event_count_internal = 0
        self._last_workspace_event_at = None
        self._last_workspace_event_weight = 0.0
        self._workspace_kind_counts = {}  # bounded-accumulator-ok: drained on removal
        self._entry_corroboration: CorroborationSnapshot | None = None
        self._repetition_tracker = RepetitionTracker(
            clock,
            consecutive_threshold=config.repeated_error_consecutive_threshold,
            window_count=config.repeated_error_window_count,
            window_seconds=config.repeated_error_window_seconds,
        )
        self._last_progress_fingerprint: str | None = None
        self._is_waiting_state = False
        self._classify_quiet_provider = None
        self._log = logger.bind(component="idle_watchdog")

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        """The reason the watchdog fired, or None if it hasn't fired yet."""
        return self._last_fire_reason

    @property
    def last_deferred_kind(self) -> StuckKind | None:
        """The ``StuckKind`` that deferred the most recent would-be fire.

        ``None`` when the watchdog has not deferred a fire yet OR
        when the most recent fire actually FIREd (the gate only sets
        this when it returns ``WatchdogVerdict.CONTINUE`` to defer).

        The diagnostic surface for the SILENT_SUBAGENT label
        described in AC-05: ``last_fire_reason`` collapses every
        non-FIRE deferral to ``WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER``,
        but ``last_deferred_kind`` retains the precise
        ``StuckKind`` (e.g. ``StuckKind.SILENT_SUBAGENT``) so an
        operator can see WHY a would-be fire was deferred ("a
        subagent dispatched then went silent for >180s").  See
        ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``
        for the runtime-facing contract test.
        """
        return self._last_deferred_kind

    @property
    def last_alive_by(self) -> AliveBy | None:
        """The corroborator's ``alive_by`` signal at the most recent fire.

        ``None`` when the watchdog has not fired yet OR when the most
        recent fire was not ``NO_PROGRESS_QUIET`` (the
        live-child vs dead-child differentiation only matters for the
        NO_PROGRESS_QUIET path; other fire helpers do not capture
        ``alive_by``).

        Consumed by ``IdleWatchdogKilledError.child_alive`` so the
        failure classifier can read the live-child signal end-to-end
        via the typed exception's ``__cause__`` chain.
        """
        return self._last_alive_by

    def diagnostic_snapshot(self, now: float | None = None) -> dict[str, object]:
        return activity_diagnostic_snapshot(self, now)

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        """Cumulative seconds spent in WAITING_ON_CHILD state across all runs."""
        return self._cumulative_waiting_on_child_seconds

    @property
    def last_subagent_progress_description(self) -> str | None:
        """The most recent subagent progress description.

        Set by ``record_subagent_work`` and reset to ``None`` by
        ``record_invocation_start``. Surfaced publicly so operators and
        tooling can see what the subagent was doing at any moment without
        needing to supply a full ``WaitingStatusListener``.
        """
        return self._last_subagent_progress_description

    def register_default_subagent_activity_listener(
        self,
        listener: WaitingStatusListener | None,
    ) -> None:
        """Register a listener that receives every subagent activity event.

        The listener is invoked from ``_emit`` for every ``WaitingStatusEvent``
        whose ``subagent_activity`` field is non-None. This gives a cheap,
        real-time view of what the subagent is doing (e.g. the last child
        progress line) without requiring callers to implement a full
        ``WaitingStatusListener``.

        The listener is reset to ``None`` on ``record_invocation_start`` so
        state does not leak across invocations. Listener exceptions are
        caught and logged at DEBUG; they never propagate.
        """
        self._default_subagent_activity_listener = listener

    def record_invocation_start(self) -> None:
        activity_record_invocation_start(self)

    def set_is_waiting_state(self, is_waiting_state: bool) -> None:
        """Update the pipeline's wait-state flag for the StuckClassifier gate.

        The run loop calls this once per phase iteration with the live
        ``state.is_waiting_state`` value. The watchdog does not own this
        state; it only mirrors it so the classifier can return
        DUPLICATE_KILL when a candidate fire would land during a wait.
        """
        self._is_waiting_state = is_waiting_state

    def set_connectivity_state_provider(
        self,
        provider: Callable[[], str | None] | None,
    ) -> None:
        """Inject a callable returning the current connectivity state label.

        The watchdog does not own connectivity; it only mirrors the live
        state so the classifier can return WAITING_ON_CONNECTIVITY when
        the network is offline. None disables the connectivity branch
        of the classifier (returns None for the connectivity_state
        input, which the classifier treats as "online" - the gate does
        not defer on the connectivity branch).
        """
        self._connectivity_state_provider = provider

    def _current_connectivity_state(self) -> str | None:
        """Return the current connectivity state label, or None.

        Calls the injected provider if available; otherwise returns None
        (the classifier treats None as "online" / no deferral).
        """
        if self._connectivity_state_provider is None:
            return None
        try:
            return self._connectivity_state_provider()
        except Exception:
            self._log.debug("idle watchdog: connectivity provider raised (suppressed)")
            return None

    def _classify_stuck_now(
        self,
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return classify_stuck_now(
            self, now=now, idle_elapsed=idle_elapsed, corroboration=corroboration
        )

    def _gate_fire(
        self,
        fire_reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> WatchdogVerdict:
        return gate_fire(
            self, fire_reason, now=now, idle_elapsed=idle_elapsed, corroboration=corroboration
        )

    def _maybe_log_deferred(
        self,
        fire_reason: WatchdogFireReason,
        deferred_kind: StuckKind,
        idle_elapsed: float,
        now: float,
    ) -> bool:
        return maybe_log_deferred(self, fire_reason, deferred_kind, idle_elapsed, now)

    def _maybe_log_any_deferred(
        self,
        fire_reason: WatchdogFireReason,
        now: float,
    ) -> bool:
        return maybe_log_any_deferred(self, fire_reason, now)

    @property
    def invocation_elapsed_seconds(self) -> float:
        """Return the seconds elapsed since the start of the invocation."""
        if self._invocation_started_at is None:
            return 0.0
        return self._clock.monotonic() - self._invocation_started_at

    def _is_no_progress_quiet(self, now: float, corroboration: CorroborationSnapshot) -> bool:
        return is_no_progress_quiet(self, now, corroboration)

    def _evaluate_no_progress_quiet(
        self, now: float, idle_elapsed: float
    ) -> WatchdogVerdict | None:
        return evaluate_no_progress_quiet(self, now, idle_elapsed)

    def _evaluate_strictly_stuck(
        self,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot,
    ) -> WatchdogVerdict | None:
        return evaluate_strictly_stuck(self, now, idle_elapsed, corroboration)

    def _evaluate_no_output_at_start(
        self,
        now: float,
        idle_elapsed: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict | None:
        return evaluate_no_output_at_start(self, now, idle_elapsed, classify_quiet)

    def idle_elapsed_seconds(self, now: float) -> float:
        """Seconds since the last recorded activity (the idle duration).

        Public accessor so callers (e.g. the process-reader fire log) can report
        a meaningful idle-elapsed value instead of the raw monotonic clock.
        """
        return now - self._last_activity

    def record_activity(self) -> None:
        activity_record_activity(self)

    def record_lifecycle_activity(self) -> None:
        """Record cosmetic, non-meaningful activity (e.g. lifecycle frames).

        Resets the idle baseline exactly like ``record_activity()`` so the
        agent is not declared idle, but does NOT reset the repeated-error
        circuit breaker: cosmetic output interleaved between identical errors
        must not mask a wedged retry loop. LIFECYCLE frames are deliberately
        excluded from the NO_OUTPUT_AT_START baseline.
        """
        self._reset_idle_baseline()

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        activity_record_tool_call_activity(self, tool_name, tool_args)

    def record_error_activity(self, message: str) -> None:
        """Record an error/repeat line for the repeated-error circuit breaker.

        Deliberately does NOT reset the idle baseline: a stream of identical
        errors must still let the idle deadline advance (so a silent-after-errors
        agent is also caught), while the repeated-error rule catches a fast retry
        storm well before the idle timeout. The cumulative WAITING_ON_CHILD run is
        still flushed for bookkeeping parity.
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._repetition_tracker.note_error(message)

    def record_progress_report(self, message: str) -> None:
        """Record an explicit ``report_progress`` heartbeat from the agent.

        A report that REPEATS the previous status (same fingerprint) is a cosmetic
        heartbeat: it feeds the repeated-error circuit breaker and does NOT reset
        the idle baseline, so an agent narrating "still stuck" forever can no
        longer keep itself alive. A report whose status CHANGES is treated as
        genuine forward progress (resets the idle baseline and the streak).
        """
        fingerprint = RepetitionTracker.fingerprint(message)
        if fingerprint == self._last_progress_fingerprint:
            now = self._clock.monotonic()
            self._accumulate_waiting_run(now)
            self._repetition_tracker.note_error(message)
            return
        self._last_progress_fingerprint = fingerprint
        self.record_activity()

    def _reset_idle_baseline(self) -> None:
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None
        self._awaiting_post_tool_result_progression = False

    def record_tool_result_activity(self) -> None:
        activity_record_tool_result_activity(self)

    def record_mcp_tool_call(self, now: float | None = None) -> None:
        """Record an MCP tool-call activity signal (new channel).

        Increments the mcp_tool channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline) — the existing 'stdout only resets idle baseline' invariant
        is preserved. The verdict hook in ``evaluate()`` consults the per-channel
        ``_last_at`` via ``_channel_evidence_active`` and defers a
        NO_OUTPUT_DEADLINE fire while the channel is fresher than the configured
        ``activity_evidence_ttl_seconds``.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._mcp_tool_call_count += 1
        self._last_mcp_tool_call_at = timestamp

    def record_subagent_work(
        self, now: float | None = None, *, description: str | None = None
    ) -> None:
        activity_record_subagent_work(self, now, description=description)

    def record_subagent_output(self, line_count: int = 1, now: float | None = None) -> None:
        """Record fresh subagent output as first-party evidence.

        This is the channel that captures a subagent's own output/log stream
        when it is observable. Each new line read from the subagent's output
        advances the ``subagent_output`` first-party channel timestamp.

        Args:
            line_count: Number of new lines observed; defaults to 1.
            now: Optional monotonic timestamp override.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._subagent_output_count += line_count
        self._last_subagent_output_at = timestamp

    def poll_subagent_output(self, now: float | None = None) -> int:
        return activity_poll_subagent_output(self, now)

    def record_workspace_event(
        self,
        path: str | None = None,
        *,
        now: float | None = None,
        kind: WorkspaceChangeKind | str | None = None,
        weight: float = 1.0,
    ) -> None:
        del path
        workspace_kind = WorkspaceChangeKind.OTHER
        if isinstance(kind, WorkspaceChangeKind):
            workspace_kind = kind
        elif isinstance(kind, str):
            workspace_kind = WorkspaceChangeKind(kind)
        activity_record_workspace_event(self, now, kind=workspace_kind, weight=weight)

    @property
    def workspace_kind_counts(self) -> dict[str, int]:
        """Defensive copy of the per-kind workspace event counter.

        Returns a fresh dict on every access so callers (the
        post-mortem diagnostic, the operator UX) can mutate the
        result without affecting the watchdog's internal state. The
        keys are the five ``WorkspaceChangeKind`` string values
        (``source``, ``log``, ``cache``, ``artifact``, ``other``);
        kinds that have never been observed are absent from the
        returned dict.
        """
        return dict(self._workspace_kind_counts)

    def last_evidence_summary(self, now: float | None = None) -> EvidenceSummary:
        return activity_last_evidence_summary(self, now)

    def _workspace_kind_breakdown_for_summary(self) -> dict[str, int] | None:
        """Return the per-kind workspace counter snapshot for the summary.

        Returns ``None`` when no workspace activity has been observed
        yet (so the resulting ``ChannelEvidenceSummary.kind_breakdown``
        is ``None`` and is omitted from ``to_dict()`` for
        backward-compat with consumers that assert on the dict shape).
        Returns a fresh defensive copy when at least one kind has
        been observed (so the frozen dataclass invariant is preserved
        and the watchdog's internal state is not exposed).
        """
        if not self._workspace_kind_counts:
            return None
        return dict(self._workspace_kind_counts)

    def _subagent_output_summary(self, now: float) -> ChannelEvidenceSummary:
        """Build the first-party subagent_output summary.

        Combines explicit subagent progress signals (``record_subagent_work``)
        and captured subagent log-stream output (``record_subagent_output``).
        Either source is first-party evidence of subagent work and can defer
        the NO_OUTPUT_DEADLINE verdict while fresh.
        """
        candidates = [
            self._last_subagent_progress_at,
            self._last_subagent_output_at,
        ]
        last_at = max((t for t in candidates if t is not None), default=None)
        counter = self._subagent_progress_count + self._subagent_output_count
        return self._channel_summary(
            ChannelName.SUBAGENT_OUTPUT,
            last_at,
            counter,
            now,
            None,
            alive_by=None,
        )

    def _workspace_summary(self, now: float) -> ChannelEvidenceSummary:
        """Build the side-channel workspace summary with quality filtering.

        A workspace event only defers the verdict when its weight is greater
        than zero. The weight of the most recently recorded event determines
        the ``can_defer`` flag for the channel summary.
        """
        can_defer = self._last_workspace_event_weight > 0.0
        return self._channel_summary(
            ChannelName.WORKSPACE,
            self._last_workspace_event_at,
            self._workspace_event_count_internal,
            now,
            self._workspace_kind_breakdown_for_summary(),
            alive_by=None,
            can_defer_override=can_defer,
        )

    def _subagent_liveness_summary(self, now: float) -> ChannelEvidenceSummary:
        return subagent_liveness_summary(self, now)

    def _subagent_count_for_heartbeat(self) -> int:
        """Return the FILTERED subagent count for the waiting heartbeat.

        Used by ``_waiting_branch.handle_waiting_branch`` to surface the
        live subagent count in the human-readable heartbeat (R6, Trustworthy
        Idle Watchdog spec). Prefers ``ProcessMonitor.spawned_subagent_count()``
        (preferred name) and falls back to ``live_subagent_count()`` for
        duck-typed monitors in legacy tests that pre-date the alias.
        Returns 0 when no monitor is injected or when the call raises.

        The filtered count is the canonical signal: a process in
        ``psutil.children(recursive=True)`` but NOT in the
        ``SubagentPidRegistry`` (e.g. a shell helper like ``npm test``)
        MUST NOT contribute to this count.
        """
        monitor: ProcessMonitor | None = self._process_monitor
        if monitor is None:
            return 0
        # Prefer the canonical name; fall back to the legacy alias so
        # existing duck-typed test fakes keep working without forcing
        # every monitor to add the new method.
        for getter_name in ("spawned_subagent_count", "live_subagent_count"):
            getter: Callable[[], int] | None = getattr(monitor, getter_name, None)
            if getter is None:
                continue
            try:
                result: int = getter()
                return int(result)
            except Exception:
                continue
        return 0

    @staticmethod
    def _channel_summary(
        channel_name: ChannelName,
        last_at: float | None,
        counter: int | None,
        now: float,
        kind_breakdown: dict[str, int] | None,
        alive_by: AliveBy | None = None,
        can_defer_override: bool | None = None,
    ) -> ChannelEvidenceSummary:
        return channel_summary(
            channel_name,
            last_at,
            counter,
            now,
            kind_breakdown,
            alive_by=alive_by,
            can_defer_override=can_defer_override,
        )

    def _channel_evidence_active(self, now: float) -> bool:
        """Return True when any quality-filtered channel is fresher than the TTL.

        Consults the full tier-aware evidence summary. First-party channels
        (mcp_tool, subagent_output) always defer when fresh. Side-channel
        channels (workspace, subagent_liveness) only defer when explicitly
        marked ``can_defer=True`` by quality filtering.

        The stdout channel is intentionally excluded: a quiet stdout is the
        NORMAL state we are trying to detect, so it cannot itself defer the
        verdict.
        """
        summary = self.last_evidence_summary(now)
        ttl = self._config.activity_evidence_ttl_seconds
        fresh = summary.first_party_fresh(ttl)
        if fresh is not None:
            return True
        fresh = summary.side_channel_fresh(ttl)
        return fresh is not None

    def _accumulate_waiting_run(self, now: float) -> None:
        """Add elapsed time from the current WAITING run to the cumulative total.

        Called on every transition OUT of the WAITING_ON_CHILD state so the
        cumulative total is preserved across WAITING<->ACTIVE oscillation.
        Double-counting is prevented by only calling this on transitions (not on
        consecutive WAITING evaluations).

        Emits a EXITED event if we were actually in a WAITING run.
        """
        if self._waiting_on_child_started_at is not None:
            elapsed = now - self._waiting_on_child_started_at
            current_run_elapsed = max(0.0, elapsed)
            idle_elapsed = now - self._last_activity
            self._cumulative_waiting_on_child_seconds += current_run_elapsed
            self._emit(
                WaitingStatusKind.EXITED,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
            )
            self._waiting_on_child_started_at = None
            self._last_waiting_status_at = None
            self._suspicion_announced_for_run = False
            self._entry_corroboration = None

    def _safe_corroborate(self) -> CorroborationSnapshot:
        """Call the corroborator safely, returning an empty snapshot on None or error.

        Fail-closed invariant: when the corroborator returns ``None``
        (or any non-``CorroborationSnapshot`` value), normalize to an
        empty ``CorroborationSnapshot`` so callers can safely read
        ``corroboration.alive_by`` without a ``NoneType`` crash. An
        empty snapshot is equivalent to "no live evidence", which is
        the conservative no-defer signal. Callers such as
        ``_evaluate_no_output_at_start`` read ``corroboration.alive_by``
        directly, so a ``None`` return would otherwise raise
        ``AttributeError`` mid-evaluation and break the watchdog
        decision path instead of failing closed.

        Tick-scoped cache: when ``evaluate()`` is running
        (``_evaluate_tick_active`` is ``True``), the snapshot captured
        at the top of the tick (``self._tick_corroboration``) is
        returned on every subsequent ``_safe_corroborate()`` call so
        all sub-evaluators (NO_OUTPUT_AT_START, STRICTLY_STUCK,
        NO_PROGRESS_QUIET, WAITING_ON_CHILD) see the SAME alive_by
        signal on a single tick. The cache is LAZILY populated on the
        first ``_safe_corroborate()`` call inside ``evaluate()`` so the
        strategy's first ``classify_quiet()`` read of shared state
        (e.g. ``ChildLivenessRegistry.prune_stale`` inside the
        corroborator) is NOT pre-empted.

        Outside-evaluate bypass: when ``_evaluate_tick_active`` is
        ``False`` (external probing, helper access from test code that
        bypasses ``evaluate()``) this method invokes the corroborator
        DIRECTLY without consulting or storing ``_tick_corroboration``.
        A previous implementation stored the snapshot on the bypass
        path, which poisoned subsequent bypass-path reads with stale
        liveness evidence (the bug
        ``test_safe_corroborate_bypasses_cache_outside_evaluate``
        pins). The ``_evaluate_tick_active`` flag makes the "tick
        active" state unambiguous so ``None`` is never ambiguous
        between "no tick active" and "tick active, cache empty".
        """
        if self._evaluate_tick_active:
            if self._tick_corroboration is not None:
                return self._tick_corroboration
            snapshot = self._call_corroborator_raw()
            self._tick_corroboration = snapshot
            return snapshot
        return self._call_corroborator_raw()

    def _call_corroborator_raw(self) -> CorroborationSnapshot:
        """Invoke the underlying corroborator and normalize the result.

        No caching: this is the bypass path used by ``evaluate()`` to
        populate the tick-scoped cache (``self._tick_corroboration``)
        at the start of every tick. Returns an empty
        ``CorroborationSnapshot`` on ``None``, exceptions, or
        non-``CorroborationSnapshot`` returns (fail-closed).
        """
        if self._corroborator is None:
            return CorroborationSnapshot()
        try:
            # Cast to ``object`` so mypy doesn't narrow ``snapshot`` to
            # ``CorroborationSnapshot`` and reject the defensive
            # ``isinstance`` check below as unreachable. At runtime the
            # corroborator IS typed as ``Callable[[], CorroborationSnapshot]``
            # but the fail-closed invariant requires the isinstance
            # check to remain reachable so a misbehaving corroborator
            # (e.g. one that returns ``None``) is normalized to an empty
            # snapshot instead of crashing downstream callers.
            snapshot = cast("object", self._corroborator())
        except Exception:
            self._log.debug("idle watchdog: corroborator raised (suppressed)")
            return CorroborationSnapshot()
        if not isinstance(snapshot, CorroborationSnapshot):
            self._log.debug(
                "idle watchdog: corroborator returned non-CorroborationSnapshot"
                " (suppressed; treating as empty snapshot)"
            )
            return CorroborationSnapshot()
        return snapshot

    def _build_corroboration_diag(
        self,
        current: CorroborationSnapshot,
    ) -> dict[str, str | int | float | bool]:
        """Build a diagnostic dict comparing current corroboration snapshot to entry baseline."""
        diag: dict[str, str | int | float | bool] = {}
        entry = self._entry_corroboration
        if (
            current.workspace_event_count is not None
            and entry is not None
            and entry.workspace_event_count is not None
        ):
            diag["workspace_event_delta"] = (
                current.workspace_event_count - entry.workspace_event_count
            )
        if current.oldest_child_seconds is not None:
            diag["oldest_child_seconds"] = current.oldest_child_seconds
        # ALWAYS populate ``scoped_child_active`` (defaulting to False
        # when the corroboration snapshot returns None). The PROMPT
        # log showed the consumer sites (subscriber.py:114,
        # _idle_stream_timeout_error.py:30,
        # _agent_inactivity_timeout_error.py:30) falling through to
        # the ``?`` fallback when the diag dict lacked the key --
        # operators saw ``scoped_child_active=?`` instead of
        # ``scoped_child_active=True/False``. Defaulting to False
        # means the diagnostic is always concrete; a True value still
        # requires the corroborator to report it.
        diag["scoped_child_active"] = (
            current.scoped_child_active
            if current.scoped_child_active is not None
            else False
        )
        if current.scoped_child_count is not None:
            diag["scoped_child_count"] = current.scoped_child_count
        if (
            current.terminal_child_events_total is not None
            and entry is not None
            and entry.terminal_child_events_total is not None
        ):
            diag["terminal_child_events_since_entry"] = (
                current.terminal_child_events_total - entry.terminal_child_events_total
            )
        if current.last_activity_was_meaningful is False:
            diag["lifecycle_only_activity"] = True
        if current.alive_by is not None:
            diag["alive_by"] = current.alive_by
        return diag

    def _build_evidence_string(
        self,
        diag: dict[str, str | int | float | bool],
    ) -> str:
        """Compose a human-readable evidence label for a SUSPECTED_FROZEN event."""
        suspect = self._config.suspect_waiting_on_child_seconds
        tokens: list[str] = []
        ws_delta = diag.get("workspace_event_delta")
        oldest = diag.get("oldest_child_seconds")
        if (
            isinstance(ws_delta, int | float)
            and ws_delta == 0
            and isinstance(oldest, int | float)
            and suspect is not None
            and oldest >= suspect
        ):
            tokens.append("time_and_workspace_quiet")
        if diag.get("scoped_child_active") is True:
            tokens.append("time_and_scoped_child_active")
        if diag.get("lifecycle_only_activity") is True:
            tokens.append("time_and_lifecycle_only")
        return "+".join(tokens) if tokens else "time_only"

    def _build_evidence_summary_diag(
        self, now: float
    ) -> tuple[dict[str, object], float | None]:
        return build_evidence_summary_diag(self, now)

    def _emit_fire_log(
        self,
        reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        message_suffix: str = "",
    ) -> None:
        emit_fire_log(
            self,
            reason,
            now=now,
            idle_elapsed=idle_elapsed,
            message_suffix=message_suffix,
        )

    def _emit(
        self,
        kind: WaitingStatusKind,
        *,
        current_run_seconds: float,
        idle_elapsed: float,
        ceiling_seconds: float | None = None,
        suspect_threshold_seconds: float | None = None,
        diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
    ) -> None:
        emit(
            self,
            kind,
            current_run_seconds=current_run_seconds,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=ceiling_seconds,
            suspect_threshold_seconds=suspect_threshold_seconds,
            diagnostic=diagnostic,
        )

    def evaluate(self, classify_quiet: Callable[[], AgentExecutionState]) -> WatchdogVerdict:
        return active_evaluate(self, classify_quiet=classify_quiet)
    def _evaluate_inner(
        self,
        *,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        return evaluate_inner(self, now=now, classify_quiet=classify_quiet)

    def _handle_evidence_deferral(
        self,
        now: float,
        idle_elapsed: float,
    ) -> WatchdogVerdict:
        return handle_evidence_deferral(self, now, idle_elapsed)

    def _maybe_log_evidence_deferral(self, channel_label: str, now: float) -> bool:
        return maybe_log_evidence_deferral(self, channel_label, now)

    def _post_tool_result_stalled(self, now: float, idle_elapsed: float) -> WatchdogVerdict | None:
        return post_tool_result_stalled(self, now, idle_elapsed)

    def _handle_drain_window(
        self,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        return handle_drain_window(self, now, classify_quiet)

    _NON_PROGRESS_ALIVE_BY_VALUES = frozenset(
        [
            AliveBy.FRESH_HEARTBEAT_ONLY,
            AliveBy.STALE_LABEL_ONLY,
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            AliveBy.CPU_IDLE_WHILE_ALIVE,
            AliveBy.LOG_STALE_WHILE_ALIVE,
        ]
    )

    # Stuck-but-alive ``AliveBy`` values for the ``stuck_job_sub_ceiling_seconds``
    # sub-ceiling. This is a STRICT subset of ``_NON_PROGRESS_ALIVE_BY_VALUES``
    # that EXCLUDES ``FRESH_HEARTBEAT_ONLY``: a productive heartbeat-only
    # child is alive and may legitimately continue for the cumulative
    # ``max_waiting_on_child_seconds`` ceiling (the ``no_progress_quiet_heartbeat_ceiling_seconds``
    # knob is the dedicated detector for that case). The sub-ceiling is
    # EXCLUSIVELY the stuck-but-alive detector: a child whose process tree
    # entry or log file exists but is producing no progress, no heartbeat,
    # and no CPU activity. The four stuck values map to the prompt's
    # "2365s false negative" failure mode: the cumulative waiting time
    # climbed past the 600s no-progress ceiling because ``classify_stuck``
    # never returned STUCK while the corroborator reported one of these
    # stale alive_by values.
    _STUCK_ALIVE_BY_VALUES: frozenset[AliveBy] = frozenset(
        {
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            AliveBy.CPU_IDLE_WHILE_ALIVE,
            AliveBy.LOG_STALE_WHILE_ALIVE,
            AliveBy.STALE_LABEL_ONLY,
        }
    )

    def _effective_waiting_ceiling(
        self,
        corroboration: CorroborationSnapshot,
    ) -> float:
        return effective_waiting_ceiling(self, corroboration)

    def _effective_ceiling_label(
        self,
        corroboration: CorroborationSnapshot,
        effective_ceiling: float,
    ) -> str:
        return effective_ceiling_label(self, corroboration, effective_ceiling)

    def _compute_effective_suspect(
        self,
        alive_by: AliveBy | None,
        candidate_total: float,
    ) -> tuple[float | None, str]:
        return compute_effective_suspect(self, alive_by, candidate_total)

    def _handle_waiting_branch(
        self,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        return handle_waiting_branch(self, now, classify_quiet)

    def _handle_active_branch(self, now: float) -> WatchdogVerdict:
        return handle_active_branch(self, now)

    def _evaluate_final_verdict(
        self,
        now: float,
        idle_elapsed: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        return evaluate_final_verdict(self, now, idle_elapsed, classify_quiet)
