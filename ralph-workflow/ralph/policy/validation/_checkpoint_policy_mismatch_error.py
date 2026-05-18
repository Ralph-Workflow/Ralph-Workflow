"""CheckpointPolicyMismatchError exception."""

from __future__ import annotations


class CheckpointPolicyMismatchError(Exception):
    """Raised when a checkpoint's phase is not present in the current policy.

    Attributes:
        checkpoint_phase: Phase name stored in the checkpoint.
        valid_phases: Set of valid phase names in the current policy.
    """

    def __init__(self, checkpoint_phase: str, valid_phases: set[str]) -> None:
        self.checkpoint_phase = checkpoint_phase
        self.valid_phases = valid_phases
        msg = (
            f"Checkpoint was saved at phase '{checkpoint_phase}' which no longer "
            f"exists in pipeline.toml. Valid phases are: {sorted(valid_phases)}. "
            f"Either restore the original pipeline.toml or start fresh with --no-resume."
        )
        super().__init__(msg)
