"""Minimal agent config protocol for availability checks."""

from typing import Protocol


class AgentEntry(Protocol):
    """Minimal agent config interface for availability checks."""

    cmd: str
    display_name: str | None
