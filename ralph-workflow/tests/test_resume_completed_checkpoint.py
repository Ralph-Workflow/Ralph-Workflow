from __future__ import annotations

import pytest

from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.policy.validation import validate_checkpoint_against_policy

pytestmark = pytest.mark.timeout_seconds(5)


def test_a_completed_checkpoint_passes_resume_preflight() -> None:
    bundle = cached_default_policy_bundle()
    state = PipelineState(
        phase=bundle.pipeline.terminal_phase,
        current_drain=bundle.pipeline.terminal_phase,
        previous_phase="development_final_commit",
        policy_format_version=2,
    )

    validate_checkpoint_against_policy(state, bundle)


def test_a_non_terminal_checkpoint_with_an_unbound_drain_still_fails_preflight() -> None:
    from ralph.policy.validation import PolicyValidationError

    bundle = cached_default_policy_bundle()
    state = PipelineState(phase="development", current_drain="complete", policy_format_version=2)

    with pytest.raises(PolicyValidationError, match="not bound in agents.toml"):
        validate_checkpoint_against_policy(state, bundle)
