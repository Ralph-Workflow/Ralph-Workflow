from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph._build_identity import build_identity

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    cause: str
    paths_with_reasons: tuple[tuple[str, str], ...]
    command: str
    agents_can_finish: bool
    build_identity: str = field(default_factory=build_identity)
    agent_reasons: tuple[str, ...] = ()
    done_count: int | None = None
    remaining_count: int | None = None
    relation: str | None = None
    strategy: str | None = None
    strategy_reason: str | None = None
    at_risk: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for path, _reason in self.paths_with_reasons)

    @property
    def summary(self) -> str:
        parts = [self.cause]
        if self.paths:
            count = len(self.paths)
            if self.agents_can_finish:
                noun = "path" if count == 1 else "paths"
                parts.append(f"{count} {noun}: {', '.join(self.paths)}")
            else:
                verb = "needs" if count == 1 else "need"
                parts.append(f"{count} {verb} operator action: {', '.join(self.paths)}")
        if self.command:
            parts.append(self.command)
        if not self.agents_can_finish:
            if self.agent_reasons:
                count = len(self.agent_reasons)
                parts.append(
                    f"all {count} resolver {'agent is' if count == 1 else 'agents are'} "
                    f"spent/unavailable ({'; '.join(self.agent_reasons)})"
                )
            else:
                parts.append("agents cannot finish this")
        if self.done_count is not None or self.remaining_count is not None:
            done = self.done_count if self.done_count is not None else "unknown"
            remaining = self.remaining_count if self.remaining_count is not None else "unknown"
            parts.append(f"done={done}; remaining={remaining}")
        return " — ".join(parts)

    @property
    def path_details(self) -> tuple[str, ...]:
        return tuple(f"{path}: {reason}" for path, reason in self.paths_with_reasons)

    @property
    def progress(self) -> str:
        if self.done_count is None and self.remaining_count is None:
            return "(unknown)"
        done = self.done_count if self.done_count is not None else "unknown"
        remaining = self.remaining_count if self.remaining_count is not None else "unknown"
        return f"done={done}; remaining={remaining}"


def build_integration_status(
    *,
    cause: str,
    paths_with_reasons: Sequence[tuple[str, str]] = (),
    command: str = "",
    agents_can_finish: bool = True,
    agent_reasons: Sequence[str] = (),
    done_count: int | None = None,
    remaining_count: int | None = None,
    relation: str | None = None,
    strategy: str | None = None,
    strategy_reason: str | None = None,
    at_risk: Sequence[str] = (),
) -> IntegrationStatus:
    facts = tuple(paths_with_reasons)
    return IntegrationStatus(
        cause=cause,
        paths_with_reasons=facts,
        command=command,
        agents_can_finish=agents_can_finish,
        agent_reasons=tuple(agent_reasons),
        done_count=done_count,
        remaining_count=remaining_count,
        relation=relation,
        strategy=strategy,
        strategy_reason=strategy_reason,
        at_risk=tuple(at_risk),
    )


__all__ = ["IntegrationStatus", "build_integration_status"]
