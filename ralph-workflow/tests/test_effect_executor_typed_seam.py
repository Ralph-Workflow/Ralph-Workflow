from __future__ import annotations

import inspect

from ralph.pipeline.effect_executor import (
    execute_agent_effect,
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.events import PipelineEvent


def _param(name: str) -> inspect.Parameter:
    return inspect.signature(execute_agent_effect).parameters[name]


def test_display_context_is_required_keyword_parameter() -> None:
    param = _param("display_context")
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_display_and_injected_runner_dependencies_are_typed() -> None:
    parameters = inspect.signature(execute_agent_effect).parameters
    for name in ("display", "verbosity", "state", "policy_bundle"):
        assert name in parameters, f"{name} must be an explicit parameter"
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    positional: tuple[object, ...] = ()
    keywords: dict[str, object] = {}

    def runner(*args: object, **kwargs: object) -> PipelineEvent:
        nonlocal positional, keywords
        positional = args
        keywords = kwargs
        return PipelineEvent.AGENT_SUCCESS

    effect = object()
    config = object()
    pipeline_deps = object()
    workspace_scope = object()
    token = install_agent_effect_dependencies(agent_runner=runner)
    try:
        result = execute_agent_effect(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            display_context=None,
            injected_option="preserved",
        )
    finally:
        reset_agent_effect_dependencies(token)

    assert result is PipelineEvent.AGENT_SUCCESS
    assert positional == (effect, config, pipeline_deps, workspace_scope)
    assert list(keywords) == [
        "display_context",
        "display",
        "verbosity",
        "state",
        "policy_bundle",
        "bridge",
        "raw_output_sink",
        "rendered_output_sink",
        "raw_line_sink",
        "run_id",
        "required_artifact",
        "session_id",
        "extra_env",
        "raise_resumable_exit",
        "agent_invocation_error_sink",
        "attempt_finalizer",
        "injected_option",
    ]
    assert keywords["display_context"] is None
    assert keywords["injected_option"] == "preserved"
