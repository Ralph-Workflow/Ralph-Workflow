from dataclasses import dataclass

__all__ = ["RetryLater"]


@dataclass(frozen=True, slots=True)
class RetryLater:
    until: float | None = None
    on_change: str | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("RetryLater requires exactly one of until or on_change")
