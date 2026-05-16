"""Exit-failure pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass

# Forbidden non-empty sentinel strings that must never appear inside
# ExitFailureEffect.reason. Empty and whitespace-only values are validated
# separately in __post_init__ so the substring check does not reject every
# possible reason via "" in reason.
_FORBIDDEN_SENTINELS: frozenset[str] = frozenset(
    {
        "Unknown failure",
        "unknown failure",
        "None",
        "null",
    }
)


def _contains_forbidden_sentinel(reason: str) -> tuple[bool, str | None]:
    """Check if reason contains any forbidden sentinel as a substring."""
    for sentinel in _FORBIDDEN_SENTINELS:
        if sentinel in reason:
            return True, sentinel
    return False, None


@dataclass(frozen=True)
class ExitFailureEffect:
    """Effect to exit with failure.

    Attributes:
        reason: Reason for the failure. Must be non-empty, non-whitespace,
            and must not contain any known non-empty sentinel that indicates
            a bug (e.g. "Unknown failure", "None", "null"). Empty and
            whitespace-only reasons are rejected separately. Sentinel checks
            are performed as substring matches to catch cases like
            "development: Unknown failure".
    """

    reason: str

    def __post_init__(self) -> None:
        """Validate that reason is non-empty, non-whitespace, and not a forbidden sentinel."""
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
