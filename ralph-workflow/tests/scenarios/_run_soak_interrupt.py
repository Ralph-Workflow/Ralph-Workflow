from dataclasses import dataclass


@dataclass(frozen=True)
class Interrupt:
    kind: str
    phase: str
    nth: int = 1
