from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentStatus:
    name: str
    display_name: str
    available: bool
    json_parser: str
    command: str
