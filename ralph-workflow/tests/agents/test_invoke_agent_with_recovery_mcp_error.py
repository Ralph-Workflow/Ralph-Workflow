"""S2 regression: McpServerError must propagate from recovery loops without retry.

The recovery loops must treat ``McpServerError`` as an infrastructure
failure (like ``SupervisionInfrastructureError``) and re-raise it
immediately, so a broken MCP server never masquerades as an agent
failure and never triggers ``reset_tool_registry`` as a recovery step.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import (
    iter_with_direct_mcp_recovery,
    run_with_direct_mcp_recovery,
)
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


def test_mcp_server_error_propagates_after_one_attempt_no_registry_call() -> None:
    attempts = 0
    registry_calls = 0
    original_error = McpServerError("mcp server failed", restart_count=3)

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise original_error

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(McpServerError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_tool_registry,
        )

    assert attempts == 1
    assert registry_calls == 0
    assert exc_info.value is original_error
    assert type(exc_info.value) is McpServerError
    assert exc_info.value.restart_count == 3


def test_iter_mcp_server_error_propagates_after_one_attempt_no_registry_call() -> None:
    attempts = 0
    registry_calls = 0
    original_error = McpServerError("mcp server failed", restart_count=3)

    def _run_attempt(_session_id: str | None) -> Iterable[str]:
        nonlocal attempts
        attempts += 1
        yield "stream-line-1"
        yield "stream-line-2"
        raise original_error

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(McpServerError) as exc_info:
        list(
            iter_with_direct_mcp_recovery(
                _run_attempt,
                max_retries=5,
                reset_tool_registry=_reset_tool_registry,
            )
        )

    assert attempts == 1
    assert registry_calls == 0
    assert exc_info.value is original_error
    assert type(exc_info.value) is McpServerError
    assert exc_info.value.restart_count == 3


def _reset_raises_mcp_server_error(calls: list[int]) -> None:
    def _reset_tool_registry() -> None:
        calls.append(1)
        raise McpServerError(
            "budget exhausted",
            restart_count=4,
            last_stage="binding_tools",
            elapsed_s=12.5,
        )

    return _reset_tool_registry


def test_run_reset_callback_mcp_error_preserves_identity() -> None:
    attempts = 0
    reset_calls: list[int] = []

    def run_attempt(_session_id: str | None, capture: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            capture("sess-run")
            raise AgentInvocationError(
                "claude",
                1,
                "Model returned an empty response with no tool calls",
                parsed_output=['{"type":"tool_result","content":"ok"}'],
            )
        return "should-not-reach"

    with pytest.raises(McpServerError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_raises_mcp_server_error(reset_calls),
            sleep=lambda _seconds: None,
        )

    error = exc_info.value
    assert type(error) is McpServerError
    assert error.restart_count == 4
    assert error.last_stage == "binding_tools"
    assert error.elapsed_s == 12.5
    assert attempts == 1
    assert reset_calls == [1]


def test_iter_reset_callback_mcp_error_preserves_identity() -> None:
    attempts = 0
    reset_calls: list[int] = []

    def _run_attempt(_session_id: str | None) -> Iterable[str]:
        nonlocal attempts
        attempts += 1
        yield '{"type":"session","session_id":"sess-iter"}'
        raise AgentInvocationError(
            "claude",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","content":"ok"}'],
        )

    def _consume() -> Iterable[str]:
        yield from iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_raises_mcp_server_error(reset_calls),
            sleep=lambda _seconds: None,
        )

    with pytest.raises(McpServerError) as exc_info:
        list(_consume())

    error = exc_info.value
    assert type(error) is McpServerError
    assert error.restart_count == 4
    assert error.last_stage == "binding_tools"
    assert error.elapsed_s == 12.5
    assert attempts == 1
    assert reset_calls == [1]


def test_run_reset_callback_generic_error_still_converts_to_invocation_error() -> None:
    """Non-McpServerError reset-callback failures keep the converted shape."""
    reset_calls: list[int] = []

    def run_attempt(_session_id: str | None, capture: object) -> str:
        capture("sess-run")
        raise AgentInvocationError(
            "claude",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","content":"ok"}'],
        )

    def _reset_tool_registry() -> None:
        reset_calls.append(1)
        raise RuntimeError("bridge gone")

    with pytest.raises(AgentInvocationError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_tool_registry,
            sleep=lambda _seconds: None,
        )

    error = exc_info.value
    assert type(error) is AgentInvocationError
    assert error.failure_origin == "mcp_operation"
    assert error.issuer == "direct_mcp_recovery.reset_tool_registry"
    assert reset_calls == [1]


class _FakeBridge:
    def shutdown(self) -> None:
        pass


def _make_config(agent_idle_timeout_seconds: float) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    )


def _registry_factory(config: object) -> object:
    del config
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    return _Registry()


def test_invoke_agent_with_recovery_propagates_health_check_mcp_server_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_invoke_agent_with_recovery propagates a health-check ``McpServerError`` as a
    typed exception (the new contract: McpServerError is re-raised by
    ``execute_agent_effect`` so the classifier sees it as
    ``FailureCategory.ENVIRONMENTAL, counted=False`` and the agent is
    not charged). The old swallow-to-AGENT_FAILURE path is gone.
    """
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    def fake_materialize_master_prompt(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return str(tmp_path / "MASTER_PROMPT.md")

    original_error = McpServerError("budget exhausted", restart_count=3)

    def fake_check_mcp_bridge_health(_bridge: object) -> None:
        raise original_error

    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=fake_materialize_master_prompt,
        registry_factory=_registry_factory,
        check_mcp_bridge_health_fn=fake_check_mcp_bridge_health,
    )

    exception_calls: list[str] = []

    def _spy_exception(message: object, *args: object) -> None:
        exception_calls.append(str(message))

    monkeypatch.setattr(effect_executor_module.logger, "exception", _spy_exception)

    with pytest.raises(McpServerError) as exc_info:
        effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=lambda *_a, **_kw: [],
            agent_invocation_error=RuntimeError,
        )

    # The typed exception is the same instance the health check raised.
    assert exc_info.value is original_error
    # The bare ``except Exception`` branch was not taken, so
    # ``logger.exception`` was never called.
    assert exception_calls == []
