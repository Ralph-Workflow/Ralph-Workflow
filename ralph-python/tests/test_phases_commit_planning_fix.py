import pytest

from ralph.config.enums import (
    PHASE_DEVELOPMENT_COMMIT,
    PHASE_FIX,
    PHASE_PLANNING,
    PHASE_REVIEW_COMMIT,
)
from ralph.phases import (
    HANDLERS,
    PhaseContext,
    PhaseHandlerNotFoundError,
    handle_phase,
    register_handler,
)
from ralph.phases.commit import (
    handle_commit,
    handle_development_commit,
    handle_review_commit,
)
from ralph.phases.fix import handle_fix
from ralph.phases.planning import handle_planning
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import PipelineEvent


def _stub_context() -> PhaseContext:
    return PhaseContext.construct(
        workspace=object(),
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def test_development_commit_only_emits_analysis_success_on_invoke_agent() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="dev-plan.txt",
    )

    assert handle_development_commit(effect, ctx) == [PipelineEvent.ANALYSIS_SUCCESS]


def test_development_commit_ignores_prepare_prompt_effect() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(
        phase=PHASE_DEVELOPMENT_COMMIT,
        iteration=1,
    )

    assert handle_development_commit(effect, ctx) == []


def test_review_commit_only_emits_analysis_success_on_invoke_agent() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="review",
        phase=PHASE_REVIEW_COMMIT,
        prompt_file="review-plan.txt",
    )

    assert handle_review_commit(effect, ctx) == [PipelineEvent.ANALYSIS_SUCCESS]


def test_handle_commit_delegates_based_on_phase() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="plan.md",
    )

    assert handle_commit(effect, ctx) == [PipelineEvent.ANALYSIS_SUCCESS]


def test_handle_commit_returns_empty_for_prepare_prompt_and_review_phase() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(
        phase=PHASE_REVIEW_COMMIT,
        iteration=2,
    )

    assert handle_commit(effect, ctx) == []


def test_handle_commit_returns_empty_for_unknown_phase() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase="custom", iteration=0)

    assert handle_commit(effect, ctx) == []


def test_handle_planning_prepares_prompt_and_advances() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase=PHASE_PLANNING, iteration=3)

    assert handle_planning(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]


def test_handle_planning_invokes_agent_successfully() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="planner",
        phase=PHASE_PLANNING,
        prompt_file="planning.txt",
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_planning_ignores_unrelated_effects() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="message.txt")

    assert handle_planning(effect, ctx) == []


def test_handle_fix_prepares_prompt_with_iteration_context() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase=PHASE_FIX, iteration=5)

    assert handle_fix(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]


def test_handle_fix_invokes_agent_successfully() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="fixer",
        phase=PHASE_FIX,
        prompt_file="fix.txt",
    )

    assert handle_fix(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_fix_ignores_unrelated_effects() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="irrelevant.txt")

    assert handle_fix(effect, ctx) == []


def test_handle_phase_dispatches_to_registered_handler() -> None:
    ctx = _stub_context()

    @register_handler("custom_phase")
    def _custom_handler(effect: Effect, context: PhaseContext) -> list[PipelineEvent]:
        assert effect.phase == "custom_phase"
        assert context is ctx
        return [PipelineEvent.COMPLETE]

    try:
        handler_effect = PreparePromptEffect(phase="custom_phase", iteration=1)
        assert handle_phase(handler_effect, ctx) == [PipelineEvent.COMPLETE]
    finally:
        HANDLERS.pop("custom_phase", None)


def test_handle_phase_raises_when_handler_missing() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="missing.txt")

    with pytest.raises(PhaseHandlerNotFoundError) as excinfo:
        handle_phase(effect, ctx)

    assert "unknown" in str(excinfo.value)
