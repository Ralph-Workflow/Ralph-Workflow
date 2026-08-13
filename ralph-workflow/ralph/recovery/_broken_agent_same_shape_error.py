"""Exception raised when a sole agent repeats the same broken-agent failure."""

from __future__ import annotations


class BrokenAgentSameShapeLimitError(RuntimeError):
    """Carry the bounded no-fallover failure evidence for operator diagnosis."""

    def __init__(
        self,
        *,
        fingerprint: tuple[str, str],
        consecutive: int,
        limit: int,
    ) -> None:
        self.fingerprint = fingerprint
        self.consecutive = consecutive
        self.limit = limit
        super().__init__(
            "BROKEN_AGENT_NO_FALLOVER: sole agent repeated broken-agent failure "
            f"{consecutive} times (reason={fingerprint[0]!r}, agent={fingerprint[1]!r}, "
            f"limit={limit})"
        )


__all__ = ["BrokenAgentSameShapeLimitError"]
