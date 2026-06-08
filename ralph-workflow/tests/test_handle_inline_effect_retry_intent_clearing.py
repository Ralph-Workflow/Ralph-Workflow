"""Regression tests: PreparePromptEffect must not leak retry/session state.

Part of the cross-platform MCP/session/tool-boundary drift bug family: a stale
resume intent (session id + retry action) carried by ``PipelineState`` must be
cleared whenever the inline-effect handler advances to a DIFFERENT phase
(skip-invocation success route, failed-route re-entry), and must be PRESERVED
for a same-phase re-prompt (the retry-in-session resume path). Otherwise a
resume session id from one phase leaks into an unrelated phase's first attempt.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_retry_intent import resume_agent_retry_intent
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy


def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


def _scope(tmp_path: Path) -> WorkspaceScope:
    root = tmp_path / ".agent"
    root.mkdir(parents=True)
    return WorkspaceScope(root=root, allowed_roots=frozenset([root]))


def test_phase_change_clears_stale_resume_intent(tmp_path: Path) -> None:
    """A skip-materialization phase change drops the prior resume intent."""
    pipeline, artifacts = _load_default_policy_bundle()
    state = PipelineState(
        phase="planning",
        last_agent_session_id="sid-stale",
        agent_retry_intent=resume_agent_retry_intent(
            "sid-stale", failure_reason="AgentInactivityTimeoutError"
        ),
    )

    result = runner_module.handle_inline_effect(
        effect=PreparePromptEffect(phase="development", skip_materialization=True),
        state=state,
        pipeline_policy=pipeline,
        artifacts_policy=artifacts,
        workspace_scope=_scope(tmp_path),
    )

    assert isinstance(result, PipelineState)
    assert result.phase == "development"
    assert result.last_agent_session_id is None
    assert result.agent_retry_intent.action is None
    assert result.agent_retry_intent.session_id is None


def test_same_phase_reprompt_preserves_resume_intent(tmp_path: Path) -> None:
    """A same-phase re-prompt keeps the resume intent so the resume can fire."""
    pipeline, artifacts = _load_default_policy_bundle()
    intent = resume_agent_retry_intent(
        "sid-keep", failure_reason="AgentInactivityTimeoutError"
    )
    state = PipelineState(
        phase="development",
        last_agent_session_id="sid-keep",
        agent_retry_intent=intent,
    )

    result = runner_module.handle_inline_effect(
        effect=PreparePromptEffect(phase="development", skip_materialization=True),
        state=state,
        pipeline_policy=pipeline,
        artifacts_policy=artifacts,
        workspace_scope=_scope(tmp_path),
    )

    assert isinstance(result, PipelineState)
    assert result.phase == "development"
    assert result.last_agent_session_id == "sid-keep"
    assert result.agent_retry_intent.action == "resume"
    assert result.agent_retry_intent.session_id == "sid-keep"
