from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from ralph._build_identity import build_identity
from ralph.recovery.agent_failure_ledger import Cooldown

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.recovery.controller import RecoveryController

NEXT_ELIGIBLE_NOW: Final[str] = "now"


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
    preserved: tuple[str, ...] = ()
    next_eligible_agent: str | None = None
    until_when: str | None = None

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for path, _reason in self.paths_with_reasons)

    @property
    def next_eligible_clause(self) -> str:
        parts: list[str] = []
        if self.next_eligible_agent:
            parts.append(f"next-eligible: {self.next_eligible_agent}")
        if self.until_when:
            parts.append(f"until-when: {self.until_when}")
        return "; ".join(parts)

    @property
    def an_agent_is_ready(self) -> bool:
        return (
            self.next_eligible_agent is not None and self.until_when == NEXT_ELIGIBLE_NOW
        )

    @property
    def effective_can_finish(self) -> bool:
        return self.agents_can_finish or bool(self.strategy) or self.an_agent_is_ready

    @property
    def change_key(self) -> tuple[str, ...]:
        return (
            self.cause,
            self.command,
            self.next_eligible_agent or "",
            self.until_when or "",
            *self.paths,
        )

    @property
    def summary(self) -> str:
        parts = [self.cause]
        can_finish = self.effective_can_finish
        if self.paths:
            count = len(self.paths)
            if can_finish:
                noun = "path" if count == 1 else "paths"
                parts.append(f"{count} {noun}: {', '.join(self.paths)}")
            else:
                verb = "needs" if count == 1 else "need"
                parts.append(f"{count} {verb} operator action: {', '.join(self.paths)}")
        if self.preserved:
            parts.append(f"preserved: {', '.join(self.preserved)}")
        if self.command:
            parts.append(self.command)
        clause = self.next_eligible_clause
        if self.agent_reasons:
            count = len(self.agent_reasons)
            cooldown = (
                f"all {count} resolver {'agent is' if count == 1 else 'agents are'} "
                f"on cooldown ({'; '.join(self.agent_reasons)})"
            )
            parts.append(f"{cooldown} ({clause})" if clause else cooldown)
        elif self.an_agent_is_ready:
            parts.append(clause)
        elif not self.agents_can_finish:
            if self.strategy:
                waiting_parts = [f"WAITING/next-strategy: {self.strategy}"]
                if clause:
                    waiting_parts.append(f"({clause})")
                parts.append(" ".join(waiting_parts))
            else:
                parts.append(
                    f"agents cannot finish this ({clause})" if clause else "agents cannot finish this"
                )
        elif clause:
            parts.append(clause)
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
            ("Preserved artifacts", "\n".join(self.preserved) or "(none)"),
            ("Next eligible agent", self.next_eligible_agent or "(none)"),
            ("Next eligible when", self.until_when or "(unknown)"),
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
    unresolved_count: int | None = None,
    initial_count: int | None = None,
    relation: str | None = None,
    strategy: str | None = None,
    strategy_reason: str | None = None,
    at_risk: Sequence[str] = (),
    preserved: Sequence[str] = (),
    next_eligible_agent: str | None = None,
    until_when: str | None = None,
) -> IntegrationStatus:
    facts = tuple(paths_with_reasons)
    if unresolved_count is not None and initial_count is not None:
        if done_count is None:
            done_count = max(initial_count - unresolved_count, 0)
        if remaining_count is None:
            remaining_count = unresolved_count
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
        preserved=tuple(preserved),
        next_eligible_agent=next_eligible_agent,
        until_when=until_when,
    )


def _format_expiry(epoch_ms: int) -> str:
    moment = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).replace(microsecond=0)
    return moment.isoformat()


def _cooldown_expiry(entry: tuple[str, int]) -> int:
    return entry[1]


def cooldown_next_eligible(
    controller: RecoveryController | None,
    phase: str,
    agents: Sequence[str],
) -> tuple[str | None, str | None]:
    if controller is None or not agents:
        return (None, None)
    statuses = controller.chain_eligibility(phase, tuple(agents))
    eligible = tuple(agent for agent, status in statuses if status.is_eligible)
    if eligible:
        return (eligible[0], "now")
    cooling: list[tuple[str, int]] = []
    for agent, status in statuses:
        if isinstance(status, Cooldown):
            cooling.append((agent, status.until_ms))
    if not cooling:
        return (None, None)
    next_agent, until_ms = min(cooling, key=_cooldown_expiry)
    clock = controller.clock
    now_ms = int(clock.monotonic() * 1000)
    epoch_offset_ms = int(clock.epoch() * 1000) - now_ms
    return (next_agent, _format_expiry(until_ms + epoch_offset_ms))


__all__ = ["IntegrationStatus", "build_integration_status", "cooldown_next_eligible"]
