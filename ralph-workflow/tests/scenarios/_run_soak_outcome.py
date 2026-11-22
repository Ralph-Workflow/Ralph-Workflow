from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from tests.scenarios._run_soak_observer_state import RunObserver


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int | None
    error: KeyboardInterrupt | None
    observer: RunObserver
    phases: tuple[str, ...]
    final_state: PipelineState | None

    def describe(self) -> str:
        saved = self.final_state.phase if self.final_state is not None else None
        tail = "\n".join(self.observer.trail[-40:])
        return (
            f"exit={self.exit_code} error={self.error!r} violation={self.observer.violation}\n"
            f"checkpoint phase={saved} agent phases={list(self.phases)}\nlast trail:\n{tail}"
        )
