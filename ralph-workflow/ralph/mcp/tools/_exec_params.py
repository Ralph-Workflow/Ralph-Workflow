"""ExecParams dataclass for exec tool parameter parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecParams:
    """Parsed parameters for the MCP exec tool."""

    command: str
    args: list[str]
    timeout_ms: int
