
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from types import MappingProxyType
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
    _activity_only_operator_cap_reached,
    _emit_activity_only_status,
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
    record_invocation_end as activity_record_invocation_end,
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
from ._circumstantial_evidence import CircumstantialEvidence, is_structurally_small_output
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
    from collections.abc import Callable, Mapping

    from ralph.agents.clock import Clock
    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
    from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture

    from .timeout_policy import TimeoutPolicy
    from .waiting_status_event import WaitingStatusListener
    from .watchdog_verdict import WatchdogVerdict


EXPECTED_FIRE_REASONS: frozenset[str] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_DEADLINE.value,
        WatchdogFireReason.CONFLICT_INACTIVITY.value,
        WatchdogFireReason.OPERATOR_CAP_REACHED.value,
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
if _actual != EXPECTED_FIRE_REASONS:
    missing = EXPECTED_FIRE_REASONS - _actual
    extra = _actual - EXPECTED_FIRE_REASONS
    raise RuntimeError(
        "WatchdogFireReason.__members__ drifted from the IdleWatchdog owner's"
        " allowlist. The watchdog owner is the single source of truth for"
        " fire decisions. Missing:"
        f" {sorted(missing)}; extra: {sorted(extra)}."
        " Update BOTH this assertion AND the watchdog owner's classification"
        " logic so the fire decision is consistent with the new enum set."
    )


@dataclass
class IdleWatchdog:

    _config: TimeoutPolicy
    _clock: Clock
    _last_activity: float = field(init=False)
    _session_started_at: float = field(init=False)
    _last_meaningful_output_at: float | None = field(default=None, init=False)
    _has_meaningful_output: bool = field(default=False, init=False)
    _any_output_count: int = field(default=0, init=False)
    _observed_output_bytes: int = field(default=0, init=False)
    _captured_session_id: str | None = field(default=None, init=False)
    _process_alive: bool = field(default=True, init=False)
    _process_exit_observed_at: float | None = field(default=None, init=False)
    _invocation_started_at: float | None = field(default=None, init=False)
    _waiting_on_child_started_at: float | None = field(default=None, init=False)
    _cumulative_waiting_on_child_seconds: float = field(default=0.0, init=False)
    _strictly_stuck_run_started_at: float | None = field(default=None, init=False)
    _in_drain_window: bool = field(default=False, init=False)
    _drain_started_at: float | None = field(default=None, init=False)
    _last_fire_reason: WatchdogFireReason | None = field(default=None, init=False)
    _last_deferred_kind: StuckKind | None = field(default=None, init=False)
    _last_deferred_log_at: dict[tuple[str, str], float] = field(default_factory=dict, init=False)
    _last_any_deferred_log_at: dict[str, float] = field(default_factory=dict, init=False)
    _last_evidence_deferral_log_at: dict[str, float] = field(default_factory=dict, init=False)
    _last_alive_by: AliveBy | None = field(default=None, init=False)
    _last_waiting_status_at: float | None = field(default=None, init=False)
    _suspicion_announced_for_run: bool = field(default=False, init=False)
    _last_tool_result_at: float | None = field(default=None, init=False)
    _awaiting_post_tool_result_progression: bool = field(default=False, init=False)
    _mcp_tool_call_count: int = field(default=0, init=False)
    _last_mcp_tool_call_at: float | None = field(default=None, init=False)
    _subagent_progress_count: int = field(default=0, init=False)
    _last_subagent_progress_at: float | None = field(default=None, init=False)
    _last_subagent_progress_emit_at: float | None = field(default=None, init=False)
    _subagent_output_count: int = field(default=0, init=False)
    _last_subagent_output_at: float | None = field(default=None, init=False)
    _workspace_event_count_internal: int = field(default=0, init=False)
    _last_workspace_event_at: float | None = field(default=None, init=False)
    _last_workspace_event_weight: float = field(default=0.0, init=False)
    _subagent_output_captures: OrderedDict[str, SubagentOutputCapture] = field(
        default_factory=OrderedDict, init=False
    )
    _evicted_worker_tombstones: OrderedDict[str, None] = field(
        default_factory=OrderedDict, init=False
    )
    _workspace_kind_counts: dict[str, int] = field(default_factory=dict, init=False)
    _is_waiting_state: bool = field(default=False, init=False)
    _connectivity_state_provider: Callable[[], str | None] | None = field(default=None, init=False)
    _classify_quiet_provider: Callable[[], AgentExecutionState] | None = field(
        default=None, init=False
    )
    _tick_corroboration: CorroborationSnapshot | None = field(default=None, init=False)
    _evaluate_tick_active: bool = field(default=False, init=False)
    _stall_active: bool = field(default=False, init=False)

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
        self._any_output_count = 0
        self._observed_output_bytes = 0
        self._captured_session_id = None
        self._process_alive = True
        self._process_exit_observed_at = None
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None
        self._last_activity_only_status_at: float | None = None
        self._last_deferred_kind = None
        self._last_deferred_log_at = {}
        self._last_any_deferred_log_at = {}
        self._last_evidence_deferral_log_at = {}
        self._last_waiting_status_at = None
        self._suspicion_announced_for_run = False
        self._last_tool_result_at = None
        self._awaiting_post_tool_result_progression = False
        self._mcp_tool_call_count = 0
        self._last_mcp_tool_call_at = None
        self._subagent_progress_count = 0
        self._last_subagent_progress_at = None
        self._last_subagent_progress_description: str | None = None
        self._default_subagent_activity_listener: WaitingStatusListener | None = None
        self._subagent_output_count = 0
        self._last_subagent_output_at = None
        self._subagent_output_captures: OrderedDict[str, SubagentOutputCapture] = (
            OrderedDict()
        )
        self._evicted_worker_tombstones: OrderedDict[str, None] = (
            OrderedDict()
        )
        self._workspace_event_count_internal = 0
        self._last_workspace_event_at = None
        self._last_workspace_event_weight = 0.0
        self._workspace_kind_counts = {}
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
        self._stall_active = False
        self._last_verdict: WatchdogVerdict | None = None
        self._log = logger.bind(component="idle_watchdog")

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        return self._last_fire_reason

    @property
    def last_verdict(self) -> WatchdogVerdict | None:
        return self._last_verdict

    @property
    def last_deferred_kind(self) -> StuckKind | None:
        return self._last_deferred_kind

    @property
    def last_alive_by(self) -> AliveBy | None:
        return self._last_alive_by

    @property
    def is_stalled(self) -> bool:
        return self._stall_active

    def set_stall_state(self, active: bool, *, now: float, idle_elapsed: float) -> None:
        self._set_stall(active, now=now, idle_elapsed=idle_elapsed)

    def _set_stall(self, active: bool, *, now: float, idle_elapsed: float) -> None:
        if active == self._stall_active:
            return
        self._stall_active = active
        if active:
            self._emit(
                WaitingStatusKind.STALLED,
                current_run_seconds=0.0,
                idle_elapsed=idle_elapsed,
            )
        else:
            self._emit(
                WaitingStatusKind.STALL_RESUMED,
                current_run_seconds=0.0,
                idle_elapsed=idle_elapsed,
            )

    def diagnostic_snapshot(self, now: float | None = None) -> dict[str, object]:
        return activity_diagnostic_snapshot(self, now)

    def repetition_diagnostic(self) -> dict[str, str | int]:
        return self._repetition_tracker.diagnostic()

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        return self._cumulative_waiting_on_child_seconds

    @property
    def last_subagent_progress_description(self) -> str | None:
        return self._last_subagent_progress_description

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def config(self) -> TimeoutPolicy:
        return self._config

    @property
    def last_progress_fingerprint(self) -> str | None:
        return self._last_progress_fingerprint

    @property
    def last_subagent_progress_emit_at(self) -> float | None:
        return self._last_subagent_progress_emit_at

    @property
    def has_active_deferral_throttles(self) -> bool:
        return bool(
            self._last_deferred_log_at
            or self._last_any_deferred_log_at
            or self._last_evidence_deferral_log_at
        )

    @property
    def deferred_throttle_reasons(self) -> frozenset[str]:
        return frozenset(self._last_any_deferred_log_at.keys())

    @property
    def subagent_output_count(self) -> int:
        return self._subagent_output_count

    @property
    def subagent_output_captures(self) -> Mapping[str, SubagentOutputCapture]:
        return MappingProxyType(self._subagent_output_captures)

    @property
    def evicted_worker_tombstones(self) -> Mapping[str, None]:
        return MappingProxyType(self._evicted_worker_tombstones)

    @property
    def subagent_progress_count(self) -> int:
        return self._subagent_progress_count

    @property
    def last_subagent_progress_at(self) -> float | None:
        return self._last_subagent_progress_at

    @property
    def in_drain_window(self) -> bool:
        return self._in_drain_window

    @property
    def last_activity(self) -> float:
        return self._last_activity

    @property
    def last_mcp_tool_call_at(self) -> float | None:
        return self._last_mcp_tool_call_at

    @property
    def mcp_tool_call_count(self) -> int:
        return self._mcp_tool_call_count

    @property
    def last_workspace_event_at(self) -> float | None:
        return self._last_workspace_event_at

    @property
    def workspace_event_count(self) -> int:
        return self._workspace_event_count_internal

    @property
    def evaluate_tick_active(self) -> bool:
        return self._evaluate_tick_active

    @property
    def tick_corroboration(self) -> CorroborationSnapshot | None:
        return self._tick_corroboration

    def safe_corroborate(self) -> CorroborationSnapshot:
        return self._safe_corroborate()

    def channel_evidence_active(self, now: float) -> bool:
        return self._channel_evidence_active(now)

    def handle_evidence_deferral(self, now: float, idle_elapsed: float) -> WatchdogVerdict:
        return self._handle_evidence_deferral(now, idle_elapsed)

    def handle_active_branch(self, now: float) -> WatchdogVerdict:
        return self._handle_active_branch(now)

    def build_evidence_summary_diag(self, now: float) -> tuple[dict[str, object], float | None]:
        return self._build_evidence_summary_diag(now)

    def emit_waiting_status(
        self,
        kind: WaitingStatusKind,
        *,
        current_run_seconds: float,
        idle_elapsed: float,
        ceiling_seconds: float | None = None,
        diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
    ) -> None:
        self._emit(
            kind,
            current_run_seconds=current_run_seconds,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=ceiling_seconds,
            diagnostic=diagnostic,
        )

    def register_default_subagent_activity_listener(
        self,
        listener: WaitingStatusListener | None,
    ) -> None:
        self._default_subagent_activity_listener = listener

    def record_invocation_start(self) -> None:
        activity_record_invocation_start(self)

    def record_invocation_end(self) -> None:
        activity_record_invocation_end(self)

    def set_is_waiting_state(self, is_waiting_state: bool) -> None:
        self._is_waiting_state = is_waiting_state

    def set_connectivity_state_provider(
        self,
        provider: Callable[[], str | None] | None,
    ) -> None:
        self._connectivity_state_provider = provider

    def _current_connectivity_state(self) -> str | None:
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

    def classify_stuck_now(self, *, now: float, idle_elapsed: float) -> StuckKind:
        return self._classify_stuck_now(now=now, idle_elapsed=idle_elapsed)

    def evaluate_fire_gate(
        self,
        fire_reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> WatchdogVerdict:
        return self._gate_fire(
            fire_reason,
            now=now,
            idle_elapsed=idle_elapsed,
            corroboration=corroboration,
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
        if self._invocation_started_at is None:
            return 0.0
        return self._clock.monotonic() - self._invocation_started_at

    @property
    def drain_window_seconds(self) -> float:
        return self._config.drain_window_seconds

    @property
    def seconds_since_process_exit(self) -> float | None:
        if self._process_exit_observed_at is None:
            return None
        return self._clock.monotonic() - self._process_exit_observed_at

    @property
    def no_output_at_start_seconds(self) -> float | None:
        return self._config.no_output_at_start_seconds

    def circumstantial_evidence(self) -> CircumstantialEvidence:
        return CircumstantialEvidence(
            process_alive=self._process_alive,
            has_stdout_bytes=self._any_output_count > 0,
            has_meaningful_output=self.has_meaningful_output(),
            captured_session_id=self._captured_session_id,
            has_session_id_captured=self._captured_session_id is not None,
            process_started_at=self._invocation_started_at,
            elapsed_seconds=self.invocation_elapsed_seconds,
        )

    def record_process_liveness(self, is_alive: bool, *, observed: bool = True) -> None:
        self._process_alive = is_alive
        if not observed:
            return
        if is_alive:
            self._process_exit_observed_at = None
        elif self._process_exit_observed_at is None:
            self._process_exit_observed_at = self._clock.monotonic()

    def record_session_id_capture(self, session_id: str) -> None:
        self._captured_session_id = session_id

    def record_any_output(self, byte_size: int = 0) -> None:
        self._any_output_count += 1
        self._observed_output_bytes += max(0, byte_size)

    def record_prompt_echo(self, line: str) -> None:
        del line
        self.record_lifecycle_activity()

    def has_meaningful_output(self) -> bool:
        return self._has_meaningful_output

    def has_any_output(self) -> bool:
        return self._any_output_count > 0

    def observed_output_is_structurally_small(self) -> bool:
        return is_structurally_small_output(
            nonblank_line_count=self._any_output_count,
            total_bytes=self._observed_output_bytes,
        )

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
        return now - self._last_activity

    def record_activity(self) -> None:
        activity_record_activity(self)

    def record_lifecycle_activity(self) -> None:
        self.record_any_output()
        self._reset_idle_baseline()

    def record_tool_use_activity(self) -> None:
        self._reset_idle_baseline()
        self._last_meaningful_output_at = self._clock.monotonic()
        self._has_meaningful_output = True

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        activity_record_tool_call_activity(self, tool_name, tool_args)

    def record_error_activity(self, message: str) -> None:
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._repetition_tracker.note_error(message)

    def record_progress_report(self, message: str) -> None:
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
        self._set_stall(active=False, now=now, idle_elapsed=0.0)

    def record_tool_result_activity(self, *, is_harness_echo: bool = False) -> None:
        activity_record_tool_result_activity(self, is_harness_echo=is_harness_echo)

    def record_mcp_tool_call(self, now: float | None = None) -> None:
        timestamp = now if now is not None else self._clock.monotonic()
        self._mcp_tool_call_count += 1
        self._last_mcp_tool_call_at = timestamp

    def record_subagent_work(
        self, now: float | None = None, *, description: str | None = None
    ) -> None:
        activity_record_subagent_work(self, now, description=description)

    def record_subagent_output(self, line_count: int = 1, now: float | None = None) -> None:
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
        return dict(self._workspace_kind_counts)

    def last_evidence_summary(self, now: float | None = None) -> EvidenceSummary:
        return activity_last_evidence_summary(self, now)

    def _workspace_kind_breakdown_for_summary(self) -> dict[str, int] | None:
        if not self._workspace_kind_counts:
            return None
        return dict(self._workspace_kind_counts)

    def _subagent_output_summary(self, now: float) -> ChannelEvidenceSummary:
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
        monitor: ProcessMonitor | None = self._process_monitor
        if monitor is None:
            return 0
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
        summary = self.last_evidence_summary(now)
        ttl = self._config.activity_evidence_ttl_seconds
        fresh = summary.first_party_fresh(ttl)
        if fresh is not None:
            return True
        fresh = summary.side_channel_fresh(ttl)
        return fresh is not None

    def _accumulate_waiting_run(self, now: float) -> None:
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
            self._set_stall(active=False, now=now, idle_elapsed=idle_elapsed)

    def _safe_corroborate(self) -> CorroborationSnapshot:
        if self._evaluate_tick_active:
            if self._tick_corroboration is not None:
                return self._tick_corroboration
            snapshot = self._call_corroborator_raw()
            self._tick_corroboration = snapshot
            return snapshot
        return self._call_corroborator_raw()

    def _call_corroborator_raw(self) -> CorroborationSnapshot:
        if self._corroborator is None:
            return CorroborationSnapshot()
        try:
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
        diag["scoped_child_active"] = (
            current.scoped_child_active if current.scoped_child_active is not None else False
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

    def _build_evidence_summary_diag(self, now: float) -> tuple[dict[str, object], float | None]:
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
        verdict = active_evaluate(self, classify_quiet=classify_quiet)
        self._last_verdict = verdict
        return verdict

    def _evaluate_inner(
        self,
        *,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        return evaluate_inner(self, now=now, classify_quiet=classify_quiet)

    def activity_only_snapshot(self) -> tuple[str | None, float | None]:
        now = self._clock.monotonic()
        summary = self.last_evidence_summary(now)
        latest_kind: str | None = None
        latest_at: float | None = None
        latest_priority = -1
        priorities = {
            ChannelName.STDOUT: 0,
            ChannelName.MCP_TOOL: 1,
            ChannelName.SUBAGENT_LIVENESS: 1,
            ChannelName.SUBAGENT_OUTPUT: 2,
            ChannelName.WORKSPACE: 3,
        }
        for channel in summary.channels:
            timestamp = channel.last_at
            priority = priorities[channel.channel_name]
            if timestamp is not None and (
                latest_at is None
                or timestamp > latest_at
                or (timestamp == latest_at and priority > latest_priority)
            ):
                latest_kind = channel.channel_name.value
                latest_at = timestamp
                latest_priority = priority
        return (latest_kind, latest_at)

    def _activity_only_operator_cap_reached(self, now: float) -> bool:
        return _activity_only_operator_cap_reached(self, now)

    def _emit_activity_only_status(self, now: float, kind: str | None, last_at: float) -> None:
        _emit_activity_only_status(self, now, kind, last_at)

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
