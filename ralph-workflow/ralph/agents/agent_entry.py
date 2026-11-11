
from typing import Protocol


class AgentEntry(Protocol):

    cmd: str
    display_name: str | None
