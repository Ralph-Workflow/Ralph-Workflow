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
            parts.append("agents cannot finish this")
        return " — ".join(parts)


def build_integration_status(
    *,
    cause: str,
    paths_with_reasons: Sequence[tuple[str, str]] = (),
    command: str = "",
    agents_can_finish: bool = True,
) -> IntegrationStatus:
    facts = tuple(paths_with_reasons)
    return IntegrationStatus(
        cause=cause,
        paths_with_reasons=facts,
        command=command,
        agents_can_finish=agents_can_finish,
    )


__all__ = ["IntegrationStatus", "build_integration_status"]
