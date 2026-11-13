from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke.attempt_supervision.attempt_observation import AttemptObservation


@dataclass(frozen=True)
class ObservationRecord:
    observation: AttemptObservation
    acted: bool
