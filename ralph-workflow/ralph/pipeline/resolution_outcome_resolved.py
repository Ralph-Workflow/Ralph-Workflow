from dataclasses import dataclass

__all__ = ["Resolved"]


@dataclass(frozen=True, slots=True)
class Resolved:
    pass
