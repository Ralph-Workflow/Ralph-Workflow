from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitEffect, ExitFailureEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.workspace import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(10)

_PHASE = "development_final_commit"


def _has_work(_root: Path) -> bool:
    return True


def _submit_commit_message(root: Path) -> None:
    artifact = root / ".agent" / "artifacts" / "commit_message.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "---\ntype: commit\nsubject: test(connect): drop duplicated detection tests\n---\n\n"
        "## Body Summary\n\n- [BS-1] Remove the redundant copy of the detection suite.\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("entered_with_stale_invocation", [False, True])
def test_a_commit_phase_runs_its_commit_before_routing_onward(
    tmp_path: Path, entered_with_stale_invocation: bool
) -> None:
    bundle = cached_default_policy_bundle()
    scope = WorkspaceScope(root=tmp_path, allowed_roots=(tmp_path,))
    state = PipelineState(
        phase=_PHASE,
        phase_chains={_PHASE: AgentChainState(agents=["claude"], current_index=0, retries=0)},
        commit=CommitState(agent_invoked=entered_with_stale_invocation),
    )

    with patch("ralph.pipeline.effect_router._is_empty_commit_phase", return_value=False):
        first = determine_effect_from_policy(
            state, bundle, workspace_scope=scope, has_uncommitted_changes_fn=_has_work
        )
        assert isinstance(first, InvokeAgentEffect)
        _submit_commit_message(tmp_path)
        after_agent, _effects = reduce(state, PipelineEvent.AGENT_SUCCESS, bundle.pipeline)
        assert after_agent.phase == _PHASE
        assert after_agent.commit.agent_invoked is True
        second = determine_effect_from_policy(
            after_agent, bundle, workspace_scope=scope, has_uncommitted_changes_fn=_has_work
        )

    assert isinstance(second, CommitEffect)


def test_a_commit_phase_that_still_has_no_message_after_regeneration_fails_loudly(
    tmp_path: Path,
) -> None:
    bundle = cached_default_policy_bundle()
    scope = WorkspaceScope(root=tmp_path, allowed_roots=(tmp_path,))
    state = PipelineState(
        phase=_PHASE,
        phase_chains={_PHASE: AgentChainState(agents=["claude"], current_index=0, retries=0)},
        commit=CommitState(agent_invoked=True),
    )

    with patch("ralph.pipeline.effect_router._is_empty_commit_phase", return_value=False):
        after_agent, _effects = reduce(state, PipelineEvent.AGENT_SUCCESS, bundle.pipeline)
        effect = determine_effect_from_policy(
            after_agent, bundle, workspace_scope=scope, has_uncommitted_changes_fn=_has_work
        )

    assert after_agent.phase == _PHASE
    assert isinstance(effect, ExitFailureEffect)
    assert "refusing to route onward without committing" in effect.reason
