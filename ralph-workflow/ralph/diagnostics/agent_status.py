"""Agent status dataclass for diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentStatus:
    """Status of a single agent."""

    name: str
    display_name: str
    available: bool
    json_parser: str
    command: str
