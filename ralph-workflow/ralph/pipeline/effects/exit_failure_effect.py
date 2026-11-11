
from __future__ import annotations

from dataclasses import dataclass

_FORBIDDEN_SENTINELS: frozenset[str] = frozenset(
    {
        "Unknown failure",
        "unknown failure",
        "None",
        "null",
    }
)


def _contains_forbidden_sentinel(reason: str) -> tuple[bool, str | None]:
    for sentinel in _FORBIDDEN_SENTINELS:
        if sentinel in reason:
            return True, sentinel
    return False, None


@dataclass(frozen=True)
class ExitFailureEffect:

    reason: str

    def __post_init__(self) -> None:
        stripped = self.reason.strip()
        if stripped == "":
            raise ValueError(
                f"ExitFailureEffect.reason must be descriptive and cannot be empty or whitespace; "
                f"got: {self.reason!r} (whitespace stripped: {stripped!r})"
            )

        is_forbidden, matched = _contains_forbidden_sentinel(self.reason)
        if is_forbidden:
            raise ValueError(
                "ExitFailureEffect.reason must be descriptive and cannot contain "
                f"a forbidden sentinel; matched sentinel: {matched!r} "
                f"in reason: {self.reason!r}"
            )
