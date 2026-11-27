from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.events.commit_hook_rejected_event import CommitHookRejected
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
_TREE = "8608365e4123787c992b480dece3814f6d3ecaa6"


@functools.cache
def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


def test_the_same_tree_rejected_from_either_commit_phase_counts_toward_one_bound() -> None:
    policy = _policy()
    rejection = CommitHookRejected(hook_output="hook: lint failed", tree_sha=_TREE)

    first, _ = reduce(PipelineState(phase="development_commit"), rejection, policy)
    second, _ = reduce(first.copy_with(phase="development_final_commit"), rejection, policy)

    assert first.commit_hook_rejection_count == 1
    assert second.commit_hook_rejection_count == 2
