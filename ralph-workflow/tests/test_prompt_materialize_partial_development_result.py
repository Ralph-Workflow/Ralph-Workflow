"""Focused prompt tests for partial development-result continuation handoff."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock

from pytest import MonkeyPatch

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import prompt_prep
from ralph.pipeline import runner as runner_module
from ralph.pipeline._runner_session import set_last_captured_session_id
from ralph.pipeline.effects import CommitEffect, ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import ExecutionResultEvent, PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PolicyBundle,
    PipelinePolicy,
)
from ralph.prompts import materialize as materialize_module
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


def _policy() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    pipeline = PipelinePolicy(
        phases={
            "build_work": PhaseDefinition(
                drain="implementation_output",
                role="execution",
                prompt_template="developer_iteration.jinja",
                continuation_template="developer_iteration_continuation.jinja",
                transitions=PhaseTransition(on_success="polish_changes"),
                result_status_post_commit={"partial": "build_work"},
            ),
            "polish_changes": PhaseDefinition(
                drain="change_cleanup",
                role="commit_cleanup",
                prompt_template="commit_cleanup.jinja",
                transitions=PhaseTransition(on_success="sync_changes"),
            ),
            "sync_changes": PhaseDefinition(
                drain="change_record",
                role="commit",
                prompt_template="commit_message.jinja",
                transitions=PhaseTransition(on_success="inspect_result"),
                commit_policy=PhaseCommitPolicy(requires_artifact=True),
            ),
            "inspect_result": PhaseDefinition(
                drain="result_analysis",
                role="analysis",
                prompt_template="development_analysis.jinja",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="build_work",
        terminal_phase="done",
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "implementation_output": ArtifactContract(
                drain="implementation_output",
                artifact_type="development_result",
            ),
            "result_analysis": ArtifactContract(
                drain="result_analysis",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed"],
            ),
        }
    )
    return pipeline, artifacts


def _render_after_commit(
    tmp_path: Path,
    result_document: str,
    *,
    stale_context_document: str | None = None,
) -> str:
    pipeline, artifacts = _policy()
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Finish the policy-selected implementation.")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Finish the implementation.\n")
    workspace.write(".agent/artifacts/development_result.md", result_document)
    if stale_context_document is not None:
        workspace.write(
            ".agent/tmp/prompt_payloads/development_result_continuation.md",
            stale_context_document,
        )

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="polish_changes",
            workspace=workspace,
            pipeline_policy=pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts,
            previous_phase="build_work",
        ),
    )
    workspace.remove(".agent/artifacts/development_result.md")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="build_work",
            workspace=workspace,
            pipeline_policy=pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts,
            previous_phase="sync_changes",
        ),
    )
    return workspace.read(prompt_path)


def test_partial_result_regression_starts_new_session_with_complete_handoff(
    tmp_path: Path,
) -> None:
    """Regression for the partial-result continuation prompt requested by this task."""
    rendered = _render_after_commit(
        tmp_path,
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Implemented the parser but the prompt integration remains.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
## Next Steps
- [NEXT-1] Wire the partial result into the continuation prompt.
## Continuation
- [CONT-1] prior-session-42
""",
    )

    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "genuinely new agent session" in rendered
    assert "PARTIAL — NOT COMPLETE" in rendered
    assert "Implemented the parser but the prompt integration remains." in rendered
    assert "Wire the partial result into the continuation prompt." in rendered
    assert "prior-session-42" in rendered


def test_completed_result_keeps_normal_development_prompt(tmp_path: Path) -> None:
    rendered = _render_after_commit(
        tmp_path,
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Finished the implementation.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
""",
        stale_context_document="""---
type: development_result
status: partial
---
## Summary
- [SUM-1] Stale incomplete work.
## Files Changed
- [FC-1] stale.py
## Next Steps
- [NEXT-1] This stale continuation must be discarded.
## Continuation
- [CONT-1] stale-session
""",
    )

    assert "You are in IMPLEMENTATION MODE" in rendered
    assert "continuing a DEVELOPMENT iteration" not in rendered
    assert "PRIOR DEVELOPMENT RESULT" not in rendered
    assert "Finished the implementation." not in rendered
    assert "Stale incomplete work." not in rendered


def test_partial_pipeline_regression_commits_integrates_and_restarts_before_analysis(
    monkeypatch: MonkeyPatch,
) -> None:
    """A partial result takes the full commit path before a fresh development session."""
    pipeline, artifacts = _policy()
    agents = AgentsPolicy(
        agent_chains={"main": AgentChainConfig(agents=["fake-agent"])},
        agent_drains={
            drain: AgentDrainConfig(chain="main")
            for drain in (
                "implementation_output",
                "change_cleanup",
                "change_record",
                "result_analysis",
            )
        },
    )
    bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
    workspace_root = Path("/in-memory-partial-pipeline")
    workspace = MemoryWorkspace(root=str(workspace_root))
    workspace.write("PROMPT.md", "Finish the policy-selected implementation.")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Finish the implementation.\n")
    partial_result = """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Implemented the parser but the prompt integration remains.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
## Next Steps
- [NEXT-1] Wire the partial result into the continuation prompt.
## Continuation
- [CONT-1] prior-session-42
"""
    completed_result = """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Finished the continuation work.
## Files Changed
- [FC-1] ralph/prompts/materialize.py
"""
    sequence: list[str] = []
    routed_effects: list[str] = []
    development_calls: list[tuple[str | None, str]] = []
    development_attempt = 0

    def fake_execute_effect(
        effect: object,
        _config: object,
        _workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        nonlocal development_attempt
        state = kwargs["state"]
        assert isinstance(state, PipelineState)
        if isinstance(effect, CommitEffect):
            sequence.append("commit")
            return PipelineEvent.COMMIT_SUCCESS
        assert isinstance(effect, InvokeAgentEffect)
        sequence.append(effect.phase)
        if effect.phase == "build_work":
            development_attempt += 1
            prompt = workspace.read(effect.prompt_file)
            development_calls.append((state.last_agent_session_id, prompt))
            workspace.write(
                ".agent/artifacts/development_result.md",
                partial_result if development_attempt == 1 else completed_result,
            )
            set_last_captured_session_id(
                "prior-session-42" if development_attempt == 1 else "completed-session"
            )
        return PipelineEvent.AGENT_SUCCESS

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        **_kwargs: object,
    ) -> PipelineEvent | ExecutionResultEvent:
        if effect.phase == "build_work":
            status = "partial" if development_attempt == 1 else "completed"
            return ExecutionResultEvent(phase=effect.phase, status=status)
        if effect.phase == "inspect_result":
            return PipelineEvent.ANALYSIS_SUCCESS
        return PipelineEvent.AGENT_SUCCESS

    def fake_auto_integrate(
        *_args: object,
        **_kwargs: object,
    ) -> RebaseState:
        sequence.append("auto-integrate")
        return RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)

    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(workspace_root),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "FsWorkspace", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(prompt_prep, "FsWorkspace", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(
        materialize_module,
        "_persist_current_prompt",
        lambda *_args, **_kwargs: ".agent/CURRENT_PROMPT.md",
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda _root: None)
    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        fake_phase_event_after_agent_run,
    )
    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", fake_auto_integrate)
    monkeypatch.setattr(
        runner_module,
        "auto_integrate_on_phase_transition",
        lambda *_args, **_kwargs: None,
    )
    determine_effect = runner_module.call_determine_effect_from_policy

    def bounded_determine_effect(*args: object, **kwargs: object) -> object:
        effect = determine_effect(*args, **kwargs)
        routed_effects.append(type(effect).__name__)
        if len(routed_effects) >= 30:
            return ExitSuccessEffect()
        return effect

    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        bounded_determine_effect,
    )

    display_context = make_display_context(force_width=120)
    deps = dataclasses.replace(
        make_test_pipeline_deps(
            display_context,
            policy_bundle=bundle,
            registry_factory=lambda _config: MagicMock(),
            phase_prompt_materializer=materialize_prompt_for_phase,
        ),
        auto_integrate_resolver=None,
        commit_effect_executor=None,
    )
    phase_chains = {
        phase: AgentChainState(agents=["fake-agent"])
        for phase in ("build_work", "polish_changes", "sync_changes", "inspect_result")
    }

    exit_code = runner_module.run(
        UnifiedConfig(),
        initial_state=PipelineState(
            phase="build_work",
            phase_chains=phase_chains,
            policy_entry_phase="build_work",
        ),
        verbosity=Verbosity.QUIET,
        pipeline_deps=deps,
    )

    assert exit_code == 0
    assert len(routed_effects) < 30, routed_effects
    assert sequence[:5] == [
        "build_work",
        "polish_changes",
        "sync_changes",
        "commit",
        "auto-integrate",
    ]
    assert sequence[5] == "build_work"
    assert sequence.index("inspect_result") > sequence.index("build_work", 1)
    assert development_calls[0][0] is None
    assert development_calls[1][0] is None
    continuation_prompt = development_calls[1][1]
    assert "PARTIAL — NOT COMPLETE" in continuation_prompt
    assert "Implemented the parser but the prompt integration remains." in continuation_prompt
    assert "Wire the partial result into the continuation prompt." in continuation_prompt
    assert "prior-session-42" in continuation_prompt
