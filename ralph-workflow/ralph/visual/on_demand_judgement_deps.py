"""Injected bounded dependency seams for an on-demand visual judgement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.vision_agent_provisioning import VisionVerdictDispatch
    from ralph.visual.on_demand_capture_evidence import OnDemandCaptureEvidence


@dataclass(frozen=True)
class OnDemandJudgementDeps:
    """The bounded capture, delegation, and validation seams owned by the caller."""

    load_retained_capture: Callable[[str], tuple[str, ...]]
    load_fresh_capture: Callable[[str], tuple[str, ...]]
    delegated_agent_id: Callable[[], str | None]
    invoke_vision: Callable[[VisionVerdictDispatch], str]
    validate_submission: Callable[[str, OnDemandCaptureEvidence, str], bool]
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("on-demand judgement timeout must be positive")
