"""Pipeline checkpoint state: construction, execution history, and size monitoring.

This package provides the building blocks for saving and inspecting pipeline
checkpoints. A checkpoint is written to disk after each phase completes so that an
interrupted run can resume from the last completed phase.

Main entry points:

- ``CheckpointBuilder`` — constructs and persists a checkpoint payload to
  ``.agent/checkpoint.json``.
- ``CheckpointPayload`` — the serialisable checkpoint data model (phase, state, metadata).
- ``RunContext`` — carries per-invocation context (workspace path, session id, config)
  used by phases and passed into ``CheckpointBuilder``.
- ``ExecutionHistory``, ``ExecutionStep``, ``StepOutcome`` — append-only log of phase
  outcomes stored inside the checkpoint; used by the recovery controller to decide
  whether to retry or escalate.
- ``CheckpointSizeMonitor``, ``SizeThresholds``, ``SizeAlert``, ``SizeCheckResult`` —
  monitors the ``.agent/`` directory size and emits alerts when thresholds are exceeded.

Use ``ralph --inspect-checkpoint`` on the CLI to display the current checkpoint.
"""

from ralph.checkpoint.builder import CheckpointBuilder
from ralph.checkpoint.checkpoint_payload import CheckpointPayload
from ralph.checkpoint.execution_history import ExecutionHistory
from ralph.checkpoint.execution_step import ExecutionStep
from ralph.checkpoint.run_context import RunContext
from ralph.checkpoint.size_alert import SizeAlert
from ralph.checkpoint.size_check_result import SizeCheckResult
from ralph.checkpoint.size_monitor import CheckpointSizeMonitor
from ralph.checkpoint.size_thresholds import SizeThresholds
from ralph.checkpoint.step_outcome import StepOutcome

__all__ = [
    "CheckpointBuilder",
    "CheckpointPayload",
    "CheckpointSizeMonitor",
    "ExecutionHistory",
    "ExecutionStep",
    "RunContext",
    "SizeAlert",
    "SizeCheckResult",
    "SizeThresholds",
    "StepOutcome",
]
