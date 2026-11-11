
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process._alive_by import AliveBy


class EvidenceTier(StrEnum):

    FIRST_PARTY = "first_party"
    SIDE_CHANNEL = "side_channel"


class ChannelName(StrEnum):

    STDOUT = "stdout"
    MCP_TOOL = "mcp_tool"
    SUBAGENT_OUTPUT = "subagent_output"
    SUBAGENT_LIVENESS = "subagent_liveness"
    WORKSPACE = "workspace"


@dataclass(frozen=True)
class ChannelEvidenceSummary:

    channel_name: ChannelName
    tier: EvidenceTier
    last_at: float | None
    age_seconds: float | None
    counter: int | None = None
    kind_breakdown: dict[str, int] | None = None
    alive_by: AliveBy | None = None
    can_defer: bool = True

    def is_fresh(self, ttl: float | None) -> bool:
        if ttl is None or ttl <= 0.0:
            return False
        if self.age_seconds is None:
            return False
        return self.age_seconds < ttl

    def to_dict(self) -> dict[str, object]:
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


CHANNEL_TIERS: dict[ChannelName, EvidenceTier] = {
    ChannelName.STDOUT: EvidenceTier.FIRST_PARTY,
    ChannelName.MCP_TOOL: EvidenceTier.FIRST_PARTY,
    ChannelName.SUBAGENT_OUTPUT: EvidenceTier.FIRST_PARTY,
    ChannelName.SUBAGENT_LIVENESS: EvidenceTier.SIDE_CHANNEL,
    ChannelName.WORKSPACE: EvidenceTier.SIDE_CHANNEL,
}

CHANNEL_DEFERS_BY_DEFAULT: dict[ChannelName, bool] = {
    ChannelName.STDOUT: False,
    ChannelName.MCP_TOOL: True,
    ChannelName.SUBAGENT_OUTPUT: True,
    ChannelName.SUBAGENT_LIVENESS: False,
    ChannelName.WORKSPACE: False,
}


@dataclass(frozen=True)
class EvidenceSummary:

    channels: tuple[ChannelEvidenceSummary, ...] = field(default_factory=tuple)

    def by_name(self, name: ChannelName) -> ChannelEvidenceSummary | None:
        for channel in self.channels:
            if channel.channel_name == name:
                return channel
        return None

    def first_party_fresh(self, ttl: float | None) -> ChannelEvidenceSummary | None:
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
        return [channel.to_dict() for channel in self.channels]


__all__ = [
    "CHANNEL_DEFERS_BY_DEFAULT",
    "CHANNEL_TIERS",
    "ChannelEvidenceSummary",
    "ChannelName",
    "EvidenceSummary",
    "EvidenceTier",
]
