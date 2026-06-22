"""Discovery strategy protocol for finding subagent output streams.

A ``DiscoveryStrategy`` answers the question: "for the agent running under
``host_pid``, where (if anywhere) are its subagent output streams observable?"

Implementations are agent-specific and are injected into the process monitor
and the idle watchdog. If an implementation cannot establish the documented
output location for an agent, it must report an empty mapping rather than
inventing a path.

Cross-transport contract
------------------------

For each supported transport the watchdog must surface what every active
subagent is doing in real time. The transport-specific source of that
evidence differs:

* **OpenCode** emits structured child lifecycle events on stdout that carry
  child IDs and PIDs. Those events are ingested into a per-invocation
  ``ChildLivenessRegistry`` by ``OpenCodeExecutionStrategy``. The watchdog
  reads FROM THAT REGISTRY via :class:`OpenCodeRegistryDiscoveryStrategy`
  so a per-child :class:`RegistryBackedSubagentOutputCapture` can surface
  textual descriptions of progress / heartbeat / terminal events. This is
  documentation-grounded (the events arrive on the agent's own stdout) and
  avoids inventing an undocumented log path.

* **Claude / Claude-interactive / Codex / Nanocoder / Generic / Agy / Pi**
  do not document a stable per-worker subagent output path. Real-time
  subagent visibility for these transports flows through the cross-transport
  subagent activity sink (``IdleWatchdog.record_subagent_work``) which is
  invoked from :meth:`BaseExecutionStrategy.observe_line` when a line
  matches the generic child-signal classifier (cross-transport markers
  ``[child]``, ``[subagent]``, ``child_progress``, ``subagent_heartbeat``,
  etc.). The watchdog's ``last_subagent_progress_description`` and the
  ``register_default_subagent_activity_listener`` hook expose this evidence
  to operators uniformly across transports.

For every transport the discovery strategy returns either a real,
documentation-grounded implementation (OpenCode) or :class:`NullDiscoveryStrategy`
when no per-worker output path is documented. Inventing paths that are not
documented would produce false-positive subagent activity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry

    from ._subagent_output_capture import SubagentOutputCapture


@runtime_checkable
class DiscoveryStrategy(Protocol):
    """Agent-specific subagent output discovery.

    Implementations discover worker directories/log files for a particular
    agent CLI (Claude Code, OpenCode, etc.). They are documentation-grounded:
    every path they return must correspond to a documented convention for that
    agent.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return a mapping from worker_id to output capture for the host agent.

        Args:
            host_pid: PID of the top-level agent process.

        Returns:
            A dict mapping worker identifiers to ``SubagentOutputCapture``
            instances. An empty dict means the agent's subagent output is not
            observable (either because the agent does not expose it or because
            the documented location could not be confirmed).
        """
        ...


class NullDiscoveryStrategy:
    """Discovery strategy that returns an empty mapping.

    Used when no agent-specific discovery implementation exists. This is the
    default for every transport whose agent CLI does not document a stable
    per-worker subagent output log path (Claude, Codex, Nanocoder, Generic,
    Agy, Pi). For those transports, real-time subagent visibility flows
    through the cross-transport subagent activity sink (see module docstring).
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return an empty mapping because no log path is documented."""
        _ = host_pid
        return {}


class RegistryBackedSubagentOutputCapture:
    """SubagentOutputCapture that reads textual descriptions from a registry record.

    OpenCode emits structured child lifecycle events on stdout that the
    ``OpenCodeExecutionStrategy`` ingests into a per-invocation
    ``ChildLivenessRegistry``. The discovery strategy returns one of these
    captures per active child; ``read_lines`` then yields a textual
    description of each registry event since the last poll so the watchdog
    can surface real-time subagent progress without depending on an
    undocumented log file path.

    Each capture tracks the last-seen progress / heartbeat timestamps so a
    second ``read_lines`` call without an intervening state advance returns
    an empty list. This bounds the rate of evidence the watchdog sees to
    the rate of real registry events -- a healthy subagent producing one
    progress event per minute produces one line per minute, not one per
    poll.

    The capture is intentionally READ-ONLY: it does not mutate the
    registry, and a missing / pruned child yields an empty list rather
    than raising (the watchdog must not crash on transient registry
    state during startup/teardown).
    """

    def __init__(
        self,
        registry: ChildLivenessRegistry,
        child_id: str,
        scope_prefix: str,
    ) -> None:
        self._registry = registry
        self._child_id = child_id
        self._scope_prefix = scope_prefix
        self._last_progress_at: float | None = None
        self._last_heartbeat_at: float | None = None
        self._last_terminal_state: str | None = None
        self._last_progress_phase: str | None = None

    def read_lines(self, worker_id: str) -> list[str]:
        """Return new lines describing registry events for this child.

        The ``worker_id`` argument matches the discovery strategy's mapping
        key. A capture only emits lines for ITS OWN child_id; an unknown
        worker_id returns an empty list (no cross-talk between captures).

        Each emitted line carries the agent's own structured event
        description so operators reading the watchdog's per-channel log
        can see what the subagent was doing at the moment of the poll.

        Returns:
            List of new textual lines, or empty list when the registry has
            no new events for this child.
        """
        if worker_id != self._child_id:
            return []

        record = self._registry._records.get(self._child_id)
        if record is None:
            return []

        lines: list[str] = []

        if (
            record.last_progress_at != self._last_progress_at
            or record.last_known_phase != self._last_progress_phase
        ):
            phase = record.last_known_phase or "progress"
            lines.append(f"[subagent] progress: phase={phase}")
            self._last_progress_at = record.last_progress_at
            self._last_progress_phase = record.last_known_phase

        if record.last_heartbeat_at != self._last_heartbeat_at:
            lines.append("[subagent] heartbeat")
            self._last_heartbeat_at = record.last_heartbeat_at

        if (
            record.terminal_state is not None
            and record.terminal_state != self._last_terminal_state
        ):
            state = record.terminal_state
            lines.append(f"[subagent] terminal: state={state}")
            self._last_terminal_state = state

        return lines


class OpenCodeRegistryDiscoveryStrategy:
    """Discovery strategy backed by an OpenCode ``ChildLivenessRegistry``.

    OpenCode emits structured child lifecycle events (``child_started``,
    ``child_progress``, ``child_heartbeat``, ``child_complete``) on its
    stdout stream. The ``OpenCodeExecutionStrategy`` ingests those events
    into a per-invocation ``ChildLivenessRegistry``. This discovery
    strategy returns one :class:`RegistryBackedSubagentOutputCapture` per
    active child so the watchdog can surface textual descriptions of what
    each subagent is doing in real time.

    The strategy is documentation-grounded for OpenCode because the events
    arrive on the agent's own stdout (no external log path is invented).
    For every other supported transport the factory returns
    :class:`NullDiscoveryStrategy` because no equivalent structured child
    stream is documented.

    Args:
        registry: The per-invocation ``ChildLivenessRegistry`` that owns
            OpenCode's child records. The strategy narrows to records
            matching ``scope_prefix`` so concurrent OpenCode invocations
            do not see each other's children.
        scope_prefix: Scope prefix used to filter registry records. For
            OpenCode this is typically ``agent:{label_scope}:``.
    """

    def __init__(
        self,
        registry: ChildLivenessRegistry,
        scope_prefix: str = "",
    ) -> None:
        self._registry = registry
        self._scope_prefix = scope_prefix

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return one capture per active child matching the scope prefix.

        The ``host_pid`` argument is accepted for protocol compatibility
        with :class:`DiscoveryStrategy`; this strategy narrows by scope
        prefix rather than by PID so a single registry can serve multiple
        concurrent OpenCode invocations.

        Children whose evidence is fully stale (no progress within
        ``progress_ttl`` and no heartbeat within ``heartbeat_ttl``) are
        filtered out of the returned mapping without mutating the
        registry. The discovery strategy is observation-only: it must
        not prune the registry itself because the registry's
        ``snapshot`` is the canonical owner of the prune decision
        (call order "strategy first, corroborator second" -- see
        ``test_stale_scoped_child_evidence_fires_no_output_deadline``
        and ``evaluate()``'s contract). Filtering prevents a stale
        record from re-emitting snapshot lines on the first
        ``read_lines`` call and falsely deferring a watchdog fire.

        Returns:
            A dict mapping child_id to a :class:`RegistryBackedSubagentOutputCapture`.
            Empty when the registry has no active, non-stale children
            matching the scope prefix.
        """
        _ = host_pid
        result: dict[str, SubagentOutputCapture] = {}
        now = self._registry._now()
        for child_id, record in self._registry._records.items():
            if not record.scope_prefix.startswith(self._scope_prefix):
                continue
            if record.terminal_state is not None:
                continue
            heartbeat_fresh = (
                record.last_heartbeat_at is not None
                and (now - record.last_heartbeat_at) <= self._registry._heartbeat_ttl
            )
            if heartbeat_fresh:
                pass
            elif record.last_progress_at is not None:
                progress_age = now - record.last_progress_at
                if progress_age > self._registry._progress_ttl:
                    continue
            else:
                label_age = now - record.started_at
                if label_age > self._registry._stale_label_ttl:
                    continue
            result[child_id] = RegistryBackedSubagentOutputCapture(
                self._registry,
                child_id,
                self._scope_prefix,
            )
        return result


__all__ = [
    "DiscoveryStrategy",
    "NullDiscoveryStrategy",
    "OpenCodeRegistryDiscoveryStrategy",
    "RegistryBackedSubagentOutputCapture",
]
