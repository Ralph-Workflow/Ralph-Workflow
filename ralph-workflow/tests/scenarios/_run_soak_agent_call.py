from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCall:
    phase: str
    nth: int
    prompt: str
