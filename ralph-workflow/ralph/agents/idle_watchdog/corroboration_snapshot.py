"""Corroboration snapshot for idle watchdog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ralph.process.child_liveness import AliveBy


ChannelName = Literal["stdout", "mcp_tool", "subagent", "workspace"]


@dataclass(frozen=True)
class ChannelEvidenceSummary:
    """Per-channel activity evidence snapshot for the watchdog verdict.

    Each channel is a separate stream of activity evidence that the watchdog
    considers for the NO_OUTPUT_DEADLINE verdict. The channel is "fresh" when
    ``age_seconds`` is below the configured ``activity_evidence_ttl_seconds``
    TTL. A channel with ``last_at is None`` has never been observed and is
    treated as stale (no activity to defer the verdict on).

    Fields:
        channel_name: Canonical name of the channel:
            - "stdout" — the agent's stdout output (the baseline channel).
            - "mcp_tool" — an MCP tools/call invocation/completion.
            - "subagent" — subagent progress / heartbeat / signal.
            - "workspace" — a workspace file change event.
        last_at: Monotonic clock value of the last observed activity on this
            channel, or None if the channel has never been observed.
        age_seconds: Seconds since the last observed activity, computed as
            ``now - last_at``; None when ``last_at`` is None. Always
            non-negative for observable channels.
        counter: Number of activity events seen on this channel, or None if
            the channel has never been observed. Counters give operators
            (and post-mortems) a coarse confidence signal: a channel with
            counter=10 and age=5s is more likely to be alive than a channel
            with counter=1 and age=5s.
        kind_breakdown: Per-kind breakdown of the channel counter. Only
            populated for the ``workspace`` channel (the only channel
            that classifies events by kind today). ``None`` when the
            channel has no kind breakdown (e.g. ``stdout``, ``mcp_tool``,
            ``subagent``) or when the watchdog has not yet observed any
            workspace activity. The dict is keyed by the five
            ``WorkspaceChangeKind`` string values (``source``, ``log``,
            ``cache``, ``artifact``, ``other``); kinds that have never
            been observed are absent from the dict. Omitted from
            ``to_dict()`` when ``None`` to preserve backward-compat
            with consumers that assert on the dict shape.

    The dataclass is frozen so callers cannot mutate the summary, and it
    exposes a ``to_dict()`` helper for diagnostic embedding (the watchdog
    fire diagnostic embeds each summary as a dict under the
    ``evidence_summary`` key).
    """

    channel_name: ChannelName
    last_at: float | None
    age_seconds: float | None
    counter: int | None
    kind_breakdown: dict[str, int] | None = None

    def to_dict(self) -> dict[str, object]:
        """Render the summary as a dict for diagnostic embedding.

        Always returns a fresh dict (never the frozen internal mapping) so
        callers can merge it into other dicts without aliasing concerns.
        The ``kind_breakdown`` key is omitted when ``None`` so existing
        consumers that assert on the dict shape (and do not consult the
        new field) continue to work unchanged.
        """
        result: dict[str, object] = {
            "channel": self.channel_name,
            "last_at": self.last_at,
            "age_seconds": self.age_seconds,
            "counter": self.counter,
        }
        if self.kind_breakdown is not None:
            result["kind_breakdown"] = dict(self.kind_breakdown)
        return result


@dataclass(frozen=True)
class CorroborationSnapshot:
    """Advisory snapshot of corroborating signals for WAITING_ON_CHILD diagnosis.

    All fields are Optional so callers without a given source can leave them None.
    Corroborators are advisory only; they NEVER affect WatchdogVerdict. The hard
    stop is determined solely by max_waiting_on_child_seconds and max_session_seconds.

    Per-channel activity evidence fields (mcp_tool_call_count,
    subagent_progress_count, last_mcp_tool_call_at, last_subagent_progress_at,
    last_workspace_event_at) carry the evidence the watchdog needs to defer a
    NO_OUTPUT_DEADLINE verdict while work is happening on a non-stdout
    channel. They default to None so existing construction sites remain valid;
    a fully-populated snapshot is the canonical "rich" evidence surface that
    IdleWatchdog.last_evidence_summary() reduces for the verdict hook.
    """

    workspace_event_count: int | None = None
    oldest_child_seconds: float | None = None
    scoped_child_active: bool | None = None
    scoped_child_count: int | None = None
    terminal_child_events_total: int | None = None
    last_activity_was_meaningful: bool | None = None
    alive_by: AliveBy | None = None
    mcp_tool_call_count: int | None = None
    subagent_progress_count: int | None = None
    last_mcp_tool_call_at: float | None = None
    last_subagent_progress_at: float | None = None
    last_workspace_event_at: float | None = None
    current_run_idle_elapsed_seconds: float | None = None


WaitingCorroborator = Callable[[], CorroborationSnapshot]
