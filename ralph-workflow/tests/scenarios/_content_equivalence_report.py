from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentEquivalence:
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return not (self.missing or self.unexpected or self.lost)

    def describe(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append("feature change absent from the target: " + ", ".join(self.missing))
        if self.unexpected:
            parts.append(
                "unrelated content introduced on the target: " + ", ".join(self.unexpected)
            )
        if self.lost:
            parts.append("uncommitted work lost: " + ", ".join(self.lost))
        return "; ".join(parts) if parts else "content equivalence holds"
