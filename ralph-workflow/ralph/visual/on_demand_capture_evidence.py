from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OnDemandCaptureEvidence:
    before_handles: tuple[str, ...]
    after_handles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.before_handles or not self.after_handles:
            raise ValueError("on-demand judgement requires retained and fresh capture evidence")
