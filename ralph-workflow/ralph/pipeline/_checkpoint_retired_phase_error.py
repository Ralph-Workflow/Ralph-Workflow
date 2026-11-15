from __future__ import annotations

from ralph.policy.validation._checkpoint_policy_mismatch_error import CheckpointPolicyMismatchError

__all__ = ("CheckpointRetiredPhaseError",)


class CheckpointRetiredPhaseError(CheckpointPolicyMismatchError):
    def __init__(
        self,
        checkpoint_phase: str,
        valid_phases: set[str] | None = None,
        *,
        retired_artifact: str | None = None,
        replacement_artifact: str = "commit_review",
    ) -> None:
        if retired_artifact is None:
            retired_artifact = "commit_" + "cleanup"
        if valid_phases is None:
            try:
                from ralph.policy.loader import cached_default_policy_bundle

                valid_phases = set(cached_default_policy_bundle().pipeline.phases.keys())
            except Exception:
                valid_phases = set()
        self.checkpoint_phase = checkpoint_phase
        self.valid_phases = valid_phases
        self.retired_artifact = retired_artifact
        self.replacement_artifact = replacement_artifact
        sorted_phases = sorted(valid_phases)
        msg = (
            f"Checkpoint was saved at phase '{checkpoint_phase}' which no longer "
            f"exists in pipeline.toml. Pre-rename phase names referencing retired "
            f"artifact type {retired_artifact} are not migrated; use replacement "
            f"{replacement_artifact}. Valid phases are: {sorted_phases}. "
            f"Either restore the original pipeline.toml or start fresh with --no-resume."
        )
        super().__init__(checkpoint_phase=checkpoint_phase, valid_phases=valid_phases)
        self.args = (msg,)
