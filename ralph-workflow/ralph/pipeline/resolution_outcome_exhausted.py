from dataclasses import dataclass

__all__ = ["Exhausted"]


@dataclass(frozen=True, slots=True)
class Exhausted:
    agents: tuple[str, ...]
