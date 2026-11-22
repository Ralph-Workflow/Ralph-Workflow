from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.checked_accessors import as_mapping
from tests.scenarios._run_soak_interrupt import Interrupt
from tests.scenarios._run_soak_violation import SoakViolation

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from tests.scenarios._run_soak import SoakRun
    from tests.scenarios._run_soak_agent_call import AgentCall

_REPEAT_LIMIT = 5
_STALL_LIMIT = 40
_STEP_LIMIT = 300
_VOLATILE_STATE_KEYS = frozenset(
    {
        "metrics",
        "recovery_epoch",
        "ledger_snapshot",
        "agent_cooldowns",
        "phase_timings",
        "dev_timebox_consumed_seconds",
        "dev_timebox_started_at",
        "cycle_timebox_consumed_seconds",
        "cycle_timebox_started_at",
    }
)


@dataclass(frozen=True)
class _Progress:
    phase: str
    cycle: int
    target_sha: str
    head_sha: str


def _state_signature(state: PipelineState) -> str:
    payload: object = state.model_dump(mode="json")
    mapping = as_mapping(payload, field="pipeline state")
    stable: dict[str, object] = dict(mapping)
    for key in _VOLATILE_STATE_KEYS:
        stable.pop(key, None)
    return json.dumps(stable, sort_keys=True, default=str)


def _cycle(state: PipelineState) -> int:
    return sum(int(value) for value in state.outer_progress.values())


@dataclass
class RunObserver:
    soak: SoakRun
    interrupt_at: Interrupt | None = None
    steps: int = 0
    waits: int = 0
    interrupted_by: str | None = None
    violation: str | None = None
    last_state: PipelineState | None = None
    trail: list[str] = field(default_factory=list)
    signatures: Counter[tuple[str, str]] = field(default_factory=Counter)
    phase_entries: Counter[str] = field(default_factory=Counter)
    progress: _Progress | None = None
    since_progress: int = 0

    @property
    def interrupted(self) -> bool:
        return self.interrupted_by is not None

    @property
    def persisted_phase(self) -> str | None:
        if self.last_state is None or self.interrupted_by == "step":
            return None
        return str(self.last_state.phase)

    def notify(self, state: PipelineState) -> None:
        self._raise_if_violated()
        self.steps += 1
        phase = str(state.phase)
        previous = self.last_state
        self.last_state = state
        self.phase_entries[phase] += 1
        self.trail.append(f"step {self.steps}: {phase} {state.last_error or ''}".rstrip())
        key = (phase, _state_signature(state))
        self.signatures[key] += 1
        if self.signatures[key] > _REPEAT_LIMIT:
            self._violate(
                f"phase {phase} repeated one state {self.signatures[key]} times without an "
                "agent invocation or a state change"
            )
        if previous is None or str(previous.phase) != phase or _cycle(previous) != _cycle(state):
            self._record_progress(None)
        else:
            from tests.scenarios._run_soak import git

            self._record_progress(
                _Progress(
                    phase,
                    _cycle(state),
                    self.soak.target_sha(),
                    git(self.soak.root, "rev-parse", "HEAD"),
                )
            )
        self._tick(phase)
        self._maybe_interrupt("step", phase, self.phase_entries[phase])

    def sleep(self, seconds: float) -> None:
        self._raise_if_violated()
        self.soak.clock.advance(seconds)
        self.waits += 1
        self.trail.append(f"wait {seconds:.1f}s")
        self._tick(f"wait {seconds:.1f}s")
        self._maybe_interrupt("sleep", "*", self.waits)

    def agent(self, call: AgentCall) -> None:
        self._raise_if_violated()
        self.trail.append(f"agent {call.phase} #{call.nth}")
        self.signatures.clear()
        self._maybe_interrupt("agent", call.phase, call.nth)

    def log(self, message: str) -> None:
        self.trail.append(message.strip())

    def _record_progress(self, progress: _Progress | None) -> None:
        if progress is None or progress != self.progress:
            self.progress = progress
            self.since_progress = 0

    def _maybe_interrupt(self, kind: str, phase: str, nth: int) -> None:
        if self.interrupt_at != Interrupt(kind, phase, nth):
            return
        self.interrupt_at = None
        self.interrupted_by = kind
        self.trail.append(f"interrupt during {kind} {phase} #{nth}")
        raise KeyboardInterrupt

    def _tick(self, label: str) -> None:
        self.since_progress += 1
        if self.since_progress > _STALL_LIMIT:
            self._violate(
                f"no phase, cycle, commit or landing progress within {_STALL_LIMIT} loop "
                f"steps (last: {label})"
            )
        if self.steps + self.waits > _STEP_LIMIT:
            self._violate(f"the run exceeded {_STEP_LIMIT} loop steps")

    def _violate(self, message: str) -> None:
        self.violation = message
        raise SoakViolation(message)

    def _raise_if_violated(self) -> None:
        if self.violation is not None:
            raise SoakViolation(self.violation)
