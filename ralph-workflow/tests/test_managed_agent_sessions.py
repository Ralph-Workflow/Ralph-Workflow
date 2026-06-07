"""Behavior tests for Ralph's public managed agent-session runtime."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.mcp.session_plan import SessionMcpPlan
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.models import UnifiedConfig


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


def _make_fake_bridge() -> tuple[object, dict[str, int]]:
    state = {"shutdown_calls": 0}
    bridge = SimpleNamespace(
        agent_endpoint_uri=lambda: "http://127.0.0.1:9999/mcp",
        shutdown=lambda: state.__setitem__("shutdown_calls", state["shutdown_calls"] + 1),
    )
    return bridge, state


def test_invoke_prompt_file_uses_managed_runtime_contract(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Describe a notes app.", encoding="utf-8")

    bridge, bridge_state = _make_fake_bridge()
    captured: dict[str, object] = {}

    def fake_start_mcp_server(*args: object) -> object:
        captured["session"] = args[0]
        captured["workspace"] = args[1]
        captured["extras"] = args[2]
        return bridge

    def fake_invoke_agent(
        agent_config: object,
        prompt_file_path: str,
        options: object,
    ) -> Iterator[str]:
        captured["agent_config"] = agent_config
        captured["prompt_file"] = prompt_file_path
        captured["options"] = options
        return iter(["hello", "world"])

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=fake_invoke_agent,
        materialize_system_prompt=lambda *args: str(tmp_path / "system.md"),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
        config=config_with_helper_agent,
        workspace_root=tmp_path,
        agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
        request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
            session_id_prefix="prompt-helper",
            drain="standalone",
            capabilities=frozenset({"workspace.read", "artifact.submit"}),
            system_prompt_name="prompt-helper",
        ),
        deps=deps,
    ) as runtime:
        result = list(runtime.invoke_prompt_file(prompt_file))

    assert result == ["hello", "world"]
    session = cast("Any", captured["session"])
    assert session.drain == "standalone"
    assert set(session.capabilities) == {"workspace.read", "artifact.submit"}
    options = cast("Any", captured["options"])
    assert options.system_prompt_file == str(tmp_path / "system.md")
    assert options.workspace_path == tmp_path
    extra_env = options.extra_env
    assert extra_env["RALPH_MCP_ENDPOINT"] == "http://127.0.0.1:9999/mcp"
    assert extra_env["RALPH_MCP_RUN_ID"] == session.run_id
    assert extra_env["RALPH_AGENT_LABEL_SCOPE"] == session.run_id
    assert bridge_state["shutdown_calls"] == 1


def test_runtime_builds_session_plan_when_request_omits_explicit_capabilities(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Describe a notes app.", encoding="utf-8")

    bridge, bridge_state = _make_fake_bridge()
    captured: dict[str, object] = {}
    session_plan = SessionMcpPlan(
        capabilities=frozenset({"workspace.read", "artifact.submit", "git.diff_read"}),
        server_env={"UPSTREAM": "1"},
    )

    def fake_start_mcp_server(*args: object) -> object:
        captured["session"] = args[0]
        captured["extras"] = args[2]
        return bridge

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        build_session_mcp_plan=lambda *args: session_plan,
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=lambda *args, **kwargs: iter(()),
        materialize_system_prompt=lambda *args: "",
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
        config=config_with_helper_agent,
        workspace_root=tmp_path,
        agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
        request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
            session_id_prefix="prompt-helper",
            drain="standalone",
        ),
        deps=deps,
    ) as runtime:
        list(runtime.invoke_prompt_file(prompt_file))

    session = cast("Any", captured["session"])
    assert set(session.capabilities) == set(session_plan.capabilities)
    extras = cast("Any", captured["extras"])
    assert extras.extra_env == {"UPSTREAM": "1"}
    assert bridge_state["shutdown_calls"] == 1


def test_invoke_prompt_file_does_not_allow_reserved_runtime_env_overrides(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Describe a notes app.", encoding="utf-8")

    bridge, _bridge_state = _make_fake_bridge()
    captured: dict[str, object] = {}

    def fake_start_mcp_server(*args: object) -> object:
        captured["session"] = args[0]
        return bridge

    def fake_invoke_agent(
        agent_config: object,
        prompt_file_path: str,
        options: object,
    ) -> Iterator[str]:
        del agent_config, prompt_file_path
        captured["options"] = options
        return iter(())

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=fake_invoke_agent,
        materialize_system_prompt=lambda *args: str(tmp_path / "system.md"),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
        config=config_with_helper_agent,
        workspace_root=tmp_path,
        agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
        request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
            session_id_prefix="prompt-helper",
            drain="standalone",
            capabilities=frozenset({"workspace.read"}),
            system_prompt_name="prompt-helper",
        ),
        deps=deps,
    ) as runtime:
        list(
            runtime.invoke_prompt_file(
                prompt_file,
                extra_env={
                    "RALPH_MCP_ENDPOINT": "http://malicious.invalid/mcp",
                    "RALPH_MCP_RUN_ID": "bad-run-id",
                    "RALPH_AGENT_LABEL_SCOPE": "bad-scope",
                    "SAFE_EXTRA": "kept",
                },
            )
        )

    options = cast("Any", captured["options"])
    session = cast("Any", captured["session"])
    extra_env = options.extra_env
    assert extra_env["RALPH_MCP_ENDPOINT"] == "http://127.0.0.1:9999/mcp"
    assert extra_env["RALPH_MCP_RUN_ID"] == session.run_id
    assert extra_env["RALPH_AGENT_LABEL_SCOPE"] == session.run_id
    assert extra_env["SAFE_EXTRA"] == "kept"


def test_runtime_open_shuts_down_bridge_when_system_prompt_setup_fails(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    bridge, bridge_state = _make_fake_bridge()

    def fake_start_mcp_server(*args: object) -> object:
        del args
        return bridge

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=lambda *args, **kwargs: iter(()),
        materialize_system_prompt=lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with pytest.raises(RuntimeError, match="boom"):
        cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
            config=config_with_helper_agent,
            workspace_root=tmp_path,
            agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
            request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
                session_id_prefix="prompt-helper",
                drain="standalone",
                capabilities=frozenset({"workspace.read"}),
                system_prompt_name="prompt-helper",
            ),
            deps=deps,
        )

    assert bridge_state["shutdown_calls"] == 1


def test_managed_runtime_retries_post_tool_empty_response_with_same_session(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Describe a notes app.", encoding="utf-8")

    bridge_state = {"shutdown_calls": 0, "reset_calls": 0}
    bridge = SimpleNamespace(
        agent_endpoint_uri=lambda: "http://127.0.0.1:9999/mcp",
        shutdown=lambda: bridge_state.__setitem__(
            "shutdown_calls", bridge_state["shutdown_calls"] + 1
        ),
        reset_tool_registry=lambda: bridge_state.__setitem__(
            "reset_calls", bridge_state["reset_calls"] + 1
        ),
    )
    calls: list[object | None] = []

    failure = AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"session_id":"sess-managed"}',
            '{"type":"tool_result","tool":"read_file"}',
        ],
    )

    def fake_start_mcp_server(*args: object) -> object:
        del args
        return bridge

    def fake_invoke_agent(
        _agent_config: object,
        _prompt_file_path: str,
        options: object,
    ) -> Iterator[str]:
        calls.append(getattr(options, "session_id", None))
        if len(calls) == 1:
            raise failure
        return iter(["Recovered managed session."])

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=fake_invoke_agent,
        materialize_system_prompt=lambda *args: str(tmp_path / "system.md"),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
        config=config_with_helper_agent,
        workspace_root=tmp_path,
        agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
        request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
            session_id_prefix="prompt-helper",
            drain="standalone",
            capabilities=frozenset({"workspace.read", "artifact.submit"}),
            system_prompt_name="prompt-helper",
        ),
        deps=deps,
    ) as runtime:
        result = list(runtime.invoke_prompt_file(prompt_file))

    assert result == ["Recovered managed session."]
    assert calls == [None, "sess-managed"]
    assert bridge_state["reset_calls"] == 1
    assert bridge_state["shutdown_calls"] == 1


def test_managed_runtime_preserves_supplied_session_id_on_first_attempt(
    tmp_path: Path,
    config_with_helper_agent: UnifiedConfig,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Describe a notes app.", encoding="utf-8")

    bridge, bridge_state = _make_fake_bridge()
    calls: list[object | None] = []

    def fake_start_mcp_server(*args: object) -> object:
        del args
        return bridge

    def fake_invoke_agent(
        _agent_config: object,
        _prompt_file_path: str,
        options: object,
    ) -> Iterator[str]:
        calls.append(getattr(options, "session_id", None))
        return iter(["ok"])

    deps = cast("Any", _session_runtime()).ManagedAgentSessionDeps(
        start_mcp_server=fake_start_mcp_server,
        invoke_agent=fake_invoke_agent,
        materialize_system_prompt=lambda *args: str(tmp_path / "system.md"),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with cast("Any", _session_runtime()).ManagedAgentSessionRuntime.open(
        config=config_with_helper_agent,
        workspace_root=tmp_path,
        agent_config=config_with_helper_agent.agents["prompt-helper-agent"],
        request=cast("Any", _session_runtime()).ManagedAgentSessionRequest(
            session_id_prefix="prompt-helper",
            drain="standalone",
            capabilities=frozenset({"workspace.read", "artifact.submit"}),
            system_prompt_name="prompt-helper",
        ),
        deps=deps,
    ) as runtime:
        result = list(runtime.invoke_prompt_file(prompt_file, session_id="sess-existing"))

    assert result == ["ok"]
    assert calls == ["sess-existing"]
    assert bridge_state["shutdown_calls"] == 1
