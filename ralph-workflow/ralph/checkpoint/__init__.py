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


from .builder import CheckpointBuilder, CheckpointPayload
from .execution_history import ExecutionHistory, ExecutionStep, StepOutcome
from .run_context import RunContext
from .size_monitor import CheckpointSizeMonitor, SizeAlert, SizeCheckResult, SizeThresholds

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
