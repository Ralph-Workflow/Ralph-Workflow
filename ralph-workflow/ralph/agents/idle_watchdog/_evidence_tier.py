"""Evidence tier model for the activity-aware idle watchdog.

The watchdog distinguishes two tiers of evidence:

* First-party evidence is direct output the watchdog reads: agent stdout,
  MCP tool calls made by the agent, and a subagent's own output/log stream
  when it is observable.
* Side-channel evidence is inferred from consequences without reading any
  output: workspace file changes and bare subagent PID liveness.

The tier distinction drives how much weight each signal carries in the
verdict. First-party evidence is sufficient on its own to defer a
NO_OUTPUT_DEADLINE fire. Side-channel evidence can corroborate a session but
is quality-filtered and never defers indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process._alive_by import AliveBy


class EvidenceTier(StrEnum):
    """Tier of an evidence channel.

    FIRST_PARTY: direct output the watchdog reads (stdout, MCP tool calls,
        subagent output stream). Sufficient on its own to defer the verdict.
    SIDE_CHANNEL: inferred consequence without reading output (workspace file
        changes, bare PID liveness). Quality-filtered and secondary.
    """

    FIRST_PARTY = "first_party"
    SIDE_CHANNEL = "side_channel"


class ChannelName(StrEnum):
    """Canonical evidence channel names.

    STDOUT: agent stdout output (first-party).
    MCP_TOOL: MCP tool-call invocations/completions made by the agent (first-party).
    SUBAGENT_OUTPUT: a subagent's own output/log stream, read where observable (first-party).
    SUBAGENT_LIVENESS: bare subagent PID liveness when output is not observable (side-channel).
    WORKSPACE: workspace file changes (side-channel).
    """

    STDOUT = "stdout"
    MCP_TOOL = "mcp_tool"
    SUBAGENT_OUTPUT = "subagent_output"
    SUBAGENT_LIVENESS = "subagent_liveness"
    WORKSPACE = "workspace"


@dataclass(frozen=True)
class ChannelEvidenceSummary:
    """Per-channel activity evidence snapshot for the watchdog verdict.

    Each channel is a separate stream of activity evidence that the watchdog
    considers for the NO_OUTPUT_DEADLINE verdict. The channel is "fresh" when
    ``age_seconds`` is below the configured ``activity_evidence_ttl_seconds``
    TTL. A channel with ``last_at is None`` has never been observed and is
    treated as stale.

    Fields:
        channel_name: Canonical name of the channel (see ``ChannelName``).
        tier: Whether this channel is first-party or side-channel evidence.
        last_at: Monotonic clock value of the last observed activity on this
            channel, or None if the channel has never been observed.
        age_seconds: Seconds since the last observed activity; None when
            ``last_at`` is None. Always non-negative for observable channels.
        counter: Number of activity events seen on this channel, or
            None if the channel has never been observed.
        kind_breakdown: Per-kind breakdown of the channel counter. Only
            populated for the ``workspace`` channel. None when the channel has
            no kind breakdown or when no workspace activity has been observed.
        alive_by: For the ``subagent_liveness`` channel, the ``AliveBy``
            classification at the time of the summary; None otherwise.
        can_defer: Whether this channel's fresh evidence is allowed to defer
            the NO_OUTPUT_DEADLINE verdict. First-party channels and strong
            side-channel channels defer; weak side-channel channels do not.
    """

    channel_name: ChannelName
    tier: EvidenceTier
    last_at: float | None
    age_seconds: float | None
    counter: int | None = None
    kind_breakdown: dict[str, int] | None = None
    alive_by: AliveBy | None = None
    can_defer: bool = True

    def is_fresh(self, ttl: float | None) -> bool:
        """Return True when this channel is fresher than ``ttl`` seconds.

        A channel with ``last_at is None`` is never fresh. A non-positive TTL
        disables freshness, matching the existing ``activity_evidence_ttl``
        disable semantics.
        """
        if ttl is None or ttl <= 0.0:
            return False
        if self.age_seconds is None:
            return False
        return self.age_seconds < ttl

    def to_dict(self) -> dict[str, object]:
        """Render the summary as a dict for diagnostic embedding.

        Always returns a fresh dict. Keys with None values are omitted for
        backward compatibility with consumers that assert on the dict shape.
        """
        result: dict[str, object] = {
            "channel": self.channel_name.value,
            "tier": self.tier.value,
            "last_at": self.last_at,
            "age_seconds": self.age_seconds,
            "counter": self.counter,
            "can_defer": self.can_defer,
        }
        if self.kind_breakdown is not None:
            result["kind_breakdown"] = dict(self.kind_breakdown)
        if self.alive_by is not None:
            result["alive_by"] = self.alive_by.value
        return result


#: Tier assignment for each canonical channel.
CHANNEL_TIERS: dict[ChannelName, EvidenceTier] = {
    ChannelName.STDOUT: EvidenceTier.FIRST_PARTY,
    ChannelName.MCP_TOOL: EvidenceTier.FIRST_PARTY,
    ChannelName.SUBAGENT_OUTPUT: EvidenceTier.FIRST_PARTY,
    ChannelName.SUBAGENT_LIVENESS: EvidenceTier.SIDE_CHANNEL,
    ChannelName.WORKSPACE: EvidenceTier.SIDE_CHANNEL,
}

#: Whether a channel's fresh evidence is allowed to defer NO_OUTPUT_DEADLINE.
#: First-party channels always defer. Side-channel channels are gated by
#: additional quality filtering in the watchdog.
CHANNEL_DEFERS_BY_DEFAULT: dict[ChannelName, bool] = {
    # stdout is the baseline channel the watchdog is trying to judge; it must
    # NOT defer its own idle deadline.
    ChannelName.STDOUT: False,
    ChannelName.MCP_TOOL: True,
    ChannelName.SUBAGENT_OUTPUT: True,
    ChannelName.SUBAGENT_LIVENESS: False,
    ChannelName.WORKSPACE: False,
}


@dataclass(frozen=True)
class EvidenceSummary:
    """Aggregate evidence summary across all channels.

    This is a thin wrapper around a tuple of ``ChannelEvidenceSummary`` values
    that provides lookup helpers for the verdict logic and diagnostics.
    """

    channels: tuple[ChannelEvidenceSummary, ...] = field(default_factory=tuple)

    def by_name(self, name: ChannelName) -> ChannelEvidenceSummary | None:
        """Return the summary for a channel by name, or None if absent."""
        for channel in self.channels:
            if channel.channel_name == name:
                return channel
        return None

    def first_party_fresh(self, ttl: float | None) -> ChannelEvidenceSummary | None:
        """Return the freshest first-party channel that can defer, or None."""
        freshest: ChannelEvidenceSummary | None = None
        for channel in self.channels:
            if channel.tier != EvidenceTier.FIRST_PARTY:
                continue
            if not channel.can_defer:
                continue
            if not channel.is_fresh(ttl):
                continue
            if freshest is None or (channel.age_seconds or 0.0) < (freshest.age_seconds or 0.0):
                freshest = channel
        return freshest

    def side_channel_fresh(self, ttl: float | None) -> ChannelEvidenceSummary | None:
        """Return the freshest quality-filtered side-channel channel, or None."""
        freshest: ChannelEvidenceSummary | None = None
        for channel in self.channels:
            if channel.tier != EvidenceTier.SIDE_CHANNEL:
                continue
            if not channel.can_defer:
                continue
            if not channel.is_fresh(ttl):
                continue
            if freshest is None or (channel.age_seconds or 0.0) < (freshest.age_seconds or 0.0):
                freshest = channel
        return freshest

    def to_dict_list(self) -> list[dict[str, object]]:
        """Return a list of per-channel dicts for diagnostic embedding."""
        return [channel.to_dict() for channel in self.channels]


__all__ = [
    "CHANNEL_DEFERS_BY_DEFAULT",
    "CHANNEL_TIERS",
    "ChannelEvidenceSummary",
    "ChannelName",
    "EvidenceSummary",
    "EvidenceTier",
]
