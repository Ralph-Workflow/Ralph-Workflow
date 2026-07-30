"""Waiting status event for idle watchdog corroboration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .waiting_status_kind import WaitingStatusKind


@dataclass(frozen=True)
class WaitingStatusEvent:
    """Structured watchdog status event emitted by IdleWatchdog.

    IdleWatchdog is the sole owner of in-stream stall/liveness
    decisions, and this dataclass is the public, single-channel
    surface through which it publishes its assessment to
    subscribers (the waiting-status listener, the
    ``PipelineSubscriber`` waiting-dispatch path, and the Status
    Bar's ``STALLED`` slot). Although several transition kinds
    historically lined up with the WAITING_ON_CHILD branch, the
    event itself is **not** scoped to that branch: every transition
    listed in ``WaitingStatusKind`` (waiting, stall, and per-tick
    progress) flows through this single dataclass, and the
    ``kind`` field is the discriminator.

    This dataclass is frozen so subscribers cannot accidentally mutate shared state.

    The ``diagnostic`` dict is a forward-compatible extension point for Phase 3
    corroborating signals (workspace_event_delta, oldest_child_seconds,
    scoped_child_active, etc.). This plan ships only the throttle, transition,
    suspicion, and hard-stop summary semantics; Phase 3 fields are out of scope.

    Transition kinds (see ``WaitingStatusKind`` for the canonical
    enumeration and per-kind docstring):

    - ``ENTERED`` -- transition into a WAITING_ON_CHILD deferral run.
    - ``PROGRESS`` -- periodic status update while still waiting (rate-limited).
    - ``SUBAGENT_PROGRESS`` -- per-subagent progress heartbeat for the
      waiting-status stream; rate-limited independently of ``PROGRESS``
      so the live subagent's activity is visible without inflating
      the existing PROGRESS cadence.
    - ``SUSPECTED_FROZEN`` -- cumulative wait crossed the suspect
      threshold; the child may be frozen.
    - ``EXITED`` -- transition out of a WAITING_ON_CHILD run. It remains a
      waiting-run marker; ``STALL_RESUMED`` is the sole stall-clear signal
      for the Status Bar slot.
    - ``HARD_STOP`` -- cumulative ceiling crossed; the watchdog is
      about to fire ``CHILDREN_PERSIST_TOO_LONG``.
    - ``STALLED`` -- the watchdog's stall state has transitioned
      ON. This is the **single source of truth** for the Status
      Bar's ``STALLED`` slot: the watchdog is the sole owner of
      in-stream stall decisions, and downstream consumers render
      ``STALLED`` only from this event (or, equivalently, from the
      watchdog's ``is_stalled`` property). Emitted exactly once
      per transition into a stall (no per-tick spam) and deduped
      by the watchdog's runtime stall-state flag.
    - ``STALL_RESUMED`` -- the watchdog's stall state has
      transitioned OFF. Emitted exactly once per transition out
      of a stall (driven by ``record_activity`` /
      ``record_invocation_start`` / ``EXITED`` / a later tick
      where the SILENT_SUBAGENT gate no longer defers). Mirrors
      ``STALLED`` so subscribers can render explicit lines for
      both transition markers without falling through to any
      generic template.

    Attributes:
        kind: The type of event (one of the ``WaitingStatusKind`` values).
        cumulative_seconds: Cumulative WAITING_ON_CHILD seconds across the session so far.
        current_run_seconds: Seconds spent in the current WAITING_ON_CHILD run.
        idle_elapsed_seconds: Seconds since last record_activity() call.
        ceiling_seconds: The active WAITING_ON_CHILD ceiling for this event.
        suspect_threshold_seconds: The suspect_waiting_on_child_seconds threshold, or None.
        diagnostic: Optional dict of extra diagnostic keys for HARD_STOP events.
        subagent_activity: Optional short string (truncated to 200 chars by the
            watchdog at write time) describing the most recent child-progress
            observation recorded via ``record_subagent_work``. The watchdog
            captures the latest raw line so operators see which subagent
            activity was current at the moment of the event (fires, transitions,
            suspicion, progress). ``None`` when no subagent observation has
            happened yet. Optional with a default so existing positional
            callers continue to work without changes.
        last_subagent_progress_at: Optional monotonic timestamp of the
            most recent subagent observation that populated
            ``subagent_activity``. Mirrors the watchdog's
            ``last_subagent_progress_at`` channel-evidence timestamp so
            every emitted event carries BOTH the textual description and
            when it was last observed. Optional with a default so existing
            positional callers continue to work without changes.
        current_subagent_tool_call: Optional parsed ``verb:`` prefix
            from ``subagent_activity`` (the current tool call the
            subagent is executing). Mirrors the watchdog's
            ``diagnostic_snapshot()["current_subagent_tool_call"]`` field
            so both surfaces carry the same parsed value. Optional with
            a default so existing positional callers continue to work
            without changes.
        stall_active: The watchdog's authoritative stall assessment at
            emission time. Consumers mirror this value rather than
            deriving independent stall state.
    """

    kind: WaitingStatusKind
    cumulative_seconds: float
    current_run_seconds: float
    idle_elapsed_seconds: float
    ceiling_seconds: float
    suspect_threshold_seconds: float | None
    diagnostic: dict[str, str | int | float | bool | list[object]] = field(default_factory=dict)
    subagent_activity: str | None = None
    last_subagent_progress_at: float | None = None
    current_subagent_tool_call: str | None = None
    stall_active: bool = False


WaitingStatusListener = Callable[[WaitingStatusEvent], None]
