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
    next_eligible_agent: str | None = None
    until_when: str | None = None

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for path, _reason in self.paths_with_reasons)

    @property
    def summary(self) -> str:
        parts = [self.cause]
        can_finish = self.agents_can_finish or bool(self.strategy)
        if self.paths:
            count = len(self.paths)
            if can_finish:
                noun = "path" if count == 1 else "paths"
                parts.append(f"{count} {noun}: {', '.join(self.paths)}")
            else:
                verb = "needs" if count == 1 else "need"
                parts.append(f"{count} {verb} operator action: {', '.join(self.paths)}")
        if self.command and not self.strategy:
            parts.append(self.command)
        if not self.agents_can_finish:
            if self.strategy:
                waiting_parts = [f"WAITING/next-strategy: {self.strategy}"]
                extra: list[str] = []
                if self.next_eligible_agent:
                    extra.append(f"next-eligible: {self.next_eligible_agent}")
                if self.until_when:
                    extra.append(f"until-when: {self.until_when}")
                if extra:
                    waiting_parts.append(f"({'; '.join(extra)})")
                parts.append(" ".join(waiting_parts))
            elif self.agent_reasons:
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
        return tuple(
            f"{path}: {reason if reason else self.cause}"
            for path, reason in self.paths_with_reasons
        )

    @property
    def progress(self) -> str:
        if self.done_count is None and self.remaining_count is None:
            return "(unknown)"
        done = self.done_count if self.done_count is not None else "unknown"
        remaining = self.remaining_count if self.remaining_count is not None else "unknown"
        return f"done={done}; remaining={remaining}"

    @property
    def scope(self) -> str:
        details = "; ".join(self.path_details)
        return f"{self.summary}; path reasons: {details}" if details else self.summary

    @property
    def render_log(self) -> str:
        parts = [self.summary]
        if self.path_details:
            parts.append(f"path reasons: {'; '.join(self.path_details)}")
        if self.build_identity:
            parts.append(f"build={self.build_identity}")
        return " — ".join(parts)

    def diagnose_rows(self) -> list[tuple[object, ...]]:
        return [
            ("Summary", self.summary),
            ("Cause", self.cause),
            ("Paths with reasons", "\n".join(self.path_details) or "(none)"),
            ("Operator command", self.command or "(none)"),
            ("Agents can finish", "yes" if self.agents_can_finish else "no"),
            ("Agent reasons", "\n".join(self.agent_reasons) or "(none)"),
            ("Progress", self.progress),
            ("Relation (feature vs target)", self.relation or "unknown"),
            ("Chosen strategy", self.strategy or "unknown"),
            (
                "Why this strategy",
                self.strategy_reason or "no strategy could be chosen from the observed relation",
            ),
            ("Done", str(self.done_count) if self.done_count is not None else "unknown"),
            (
                "Remaining",
                str(self.remaining_count) if self.remaining_count is not None else "unknown",
            ),
            ("Human action if stopped", self.command or "none"),
            ("At risk of loss", "\n".join(self.at_risk) or "nothing"),
            ("Build", self.build_identity),
        ]


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
    next_eligible_agent: str | None = None,
    until_when: str | None = None,
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
        next_eligible_agent=next_eligible_agent,
        until_when=until_when,
    )


__all__ = ["IntegrationStatus", "build_integration_status"]
