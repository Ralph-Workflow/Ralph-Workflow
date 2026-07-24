"""Tests for agent command construction."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.agents import invoke as invoke_module
from ralph.agents.invoke import BuildCommandOptions, InvokeOptions, build_command
from ralph.agents.invoke._options import build_invoke_options_from_config
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pytest


_EXPECTED_DESCENDANT_LIVENESS_CHECKS = 2


def _json_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(raw))


def _toml_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", tomllib.loads(raw))


def _env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return cast("dict[str, str]", env_obj)


def _argv(args: tuple[object, ...]) -> list[str]:
    return list(cast("Iterable[str]", args[0]))


class TestResolveInvocationRuntime:
    def test_opencode_uses_config_content_from_base_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = AgentConfig(
            cmd="opencode",
            output_flag="--json-stream",
            transport=AgentTransport.OPENCODE,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
        captured: list[str | None] = []

        def fake_build(
            config_content: str | None,
            endpoint: str,
            **kwargs: object,
        ) -> tuple[str, list[object]]:
            del kwargs
            captured.append(config_content)
            return ("{}", [])

        monkeypatch.setattr(invoke_module, "build_opencode_provider_config", fake_build)
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        invoke_module.resolve_invocation_runtime(
            config,
            extra_env,
            None,
            _base_env={"OPENCODE_CONFIG_CONTENT": "injected-content"},
        )
        assert captured[0] == "injected-content"

    def test_codex_uses_home_from_base_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = AgentConfig(
            cmd="codex",
            output_flag="",
            transport=AgentTransport.CODEX,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
        captured: list[str | None] = []

        def fake_prepare(
            endpoint: str | None,
            *,
            workspace_path: object,
            existing_home: str | None,
            master_prompt_file: object,
            **kwargs: object,
        ) -> tuple[str, list[object]]:
            del kwargs
            captured.append(existing_home)
            return ("/fake/home", [])

        monkeypatch.setattr(invoke_module, "prepare_codex_home_with_upstreams", fake_prepare)
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        invoke_module.resolve_invocation_runtime(
            config, extra_env, None, _base_env={"CODEX_HOME": "/injected/home"}
        )
        assert captured[0] == "/injected/home"

    def test_agy_runtime_sets_mcp_endpoint_and_upstream_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = AgentConfig(
            cmd="agy",
            transport=AgentTransport.AGY,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}

        monkeypatch.setattr(
            invoke_module,
            "load_existing_agy_upstream_servers",
            lambda workspace_path: (),
        )
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        result = invoke_module.resolve_invocation_runtime(config, extra_env, None)
        assert result.mcp_endpoint == "http://localhost:9999"
        assert result.agent_env is not None

    def test_agy_runtime_early_exit_when_no_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = AgentConfig(
            cmd="agy",
            transport=AgentTransport.AGY,
        )
        monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)

        result = invoke_module.resolve_invocation_runtime(config, None, None)

        assert result.agent_env is None
        assert result.server_env is None
        assert result.mcp_endpoint is None

    def test_nanocoder_runtime_sets_managed_mcp_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = AgentConfig(
            cmd="nanocoder",
            transport=AgentTransport.NANOCODER,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}

        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

        result = invoke_module.resolve_invocation_runtime(config, extra_env, Path("/tmp"))

        assert result.mcp_endpoint == "http://localhost:9999/mcp"
        assert result.agent_env is not None
        assert result.agent_env["NANOCODER_TRUST_DIRECTORY"] == "1"
        payload = _json_object(result.agent_env["NANOCODER_MCPSERVERS"])
        servers = cast("dict[str, dict[str, object]]", payload["mcpServers"])
        assert servers["ralph"]["transport"] == "http"
        assert servers["ralph"]["url"] == "http://localhost:9999/mcp"

    def test_nanocoder_runtime_auto_allows_discovered_ralph_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = AgentConfig(
            cmd="nanocoder",
            transport=AgentTransport.NANOCODER,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}

        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        monkeypatch.setattr(
            invoke_module,
            "discover_http_mcp_tool_names",
            lambda endpoint: [
                "read_file",
                "mcp__ralph__read_file",
                "ralph_submit_md_artifact",
            ],
        )

        result = invoke_module.resolve_invocation_runtime(config, extra_env, Path("/tmp"))

        assert result.agent_env is not None
        payload = _json_object(result.agent_env["NANOCODER_MCPSERVERS"])
        servers = cast("dict[str, dict[str, object]]", payload["mcpServers"])
        assert servers["ralph"]["alwaysAllow"] == [
            "read_file",
            "mcp__ralph__read_file",
            "ralph_submit_md_artifact",
            "mcp__ralph__ralph_submit_md_artifact",
        ]

    def test_prepare_interactive_claude_options_preserves_new_invoke_fields(self) -> None:
        config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)

        def _permission_listener(_message: str) -> None:
            return None

        options = InvokeOptions(
            session_id="sess-existing",
            post_tool_result_progression_seconds=12.0,
            permission_prompt_listener=_permission_listener,
        )

        prepared = invoke_module._prepare_interactive_claude_options(options, config)

        assert prepared.post_tool_result_progression_seconds == 12.0
        assert prepared.permission_prompt_listener is _permission_listener
        assert prepared.session_id == "sess-existing"
        assert prepared.initial_session_id == "sess-existing"


def test_build_invoke_options_propagates_unsafe_mode_from_general_config() -> None:
    """Unsafe_mode in [general.workflow] flows into InvokeOptions."""
    cfg = GeneralConfig(workflow={"unsafe_mode": True})
    opts = build_invoke_options_from_config(cfg)
    assert opts.unsafe_mode is True

    cfg_default = GeneralConfig()
    opts_default = build_invoke_options_from_config(cfg_default)
    assert opts_default.unsafe_mode is False


def test_opencode_runtime_propagates_unsafe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to build_opencode_provider_config."""
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
    captured: dict[str, object] = {}

    def fake_build(
        config_content: str | None, endpoint: str, *, unsafe_mode: bool = False
    ) -> tuple[str, list[object]]:
        captured["unsafe_mode"] = unsafe_mode
        return ("{}", [])

    monkeypatch.setattr(invoke_module, "build_opencode_provider_config", fake_build)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=True)
    assert captured["unsafe_mode"] is True

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=False)
    assert captured["unsafe_mode"] is False


def test_nanocoder_runtime_propagates_unsafe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to build_nanocoder_mcp_config."""
    config = AgentConfig(
        cmd="nanocoder",
        transport=AgentTransport.NANOCODER,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}
    captured: dict[str, object] = {}

    def fake_build(
        existing: str | None,
        endpoint: str,
        *,
        always_allow: tuple[str, ...] = (),
        unsafe_mode: bool = False,
        workspace_path: object = None,
        env: object = None,
    ) -> tuple[str, tuple[object, ...]]:
        captured["unsafe_mode"] = unsafe_mode
        captured["workspace_path"] = workspace_path
        captured["env"] = env
        return ("{}", ())

    monkeypatch.setattr(invoke_module, "build_nanocoder_mcp_config", fake_build)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke_module.resolve_invocation_runtime(config, extra_env, workspace, unsafe_mode=True)
    assert captured["unsafe_mode"] is True
    assert captured["workspace_path"] == workspace


def test_codex_runtime_propagates_unsafe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to prepare_codex_home_with_upstreams."""
    config = AgentConfig(
        cmd="codex",
        output_flag="",
        transport=AgentTransport.CODEX,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
    captured: dict[str, object] = {}

    def fake_prepare(
        endpoint: str | None,
        *,
        workspace_path: object,
        existing_home: str | None,
        master_prompt_file: object,
        unsafe_mode: bool = False,
    ) -> tuple[str, list[object]]:
        captured["unsafe_mode"] = unsafe_mode
        return ("/fake/home", [])

    monkeypatch.setattr(invoke_module, "prepare_codex_home_with_upstreams", fake_prepare)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=True)
    assert captured["unsafe_mode"] is True


def test_claude_command_propagates_unsafe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_command passes unsafe_mode to claude_mcp_config via BuildCommandOptions."""
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE,
        output_flag="--output-format=stream-json",
    )
    captured: dict[str, object] = {}

    def fake_claude_mcp_config(
        endpoint: str,
        *,
        workspace_path: object = None,
        unsafe_mode: bool = False,
    ) -> str:
        captured["unsafe_mode"] = unsafe_mode
        captured["workspace_path"] = workspace_path
        return json.dumps({"mcpServers": {"ralph": {"url": endpoint}}})

    monkeypatch.setattr("ralph.agents.invoke._commands.claude_mcp_config", fake_claude_mcp_config)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://localhost:9999/mcp",
            unsafe_mode=True,
        ),
    )
    assert captured["unsafe_mode"] is True
    assert "--mcp-config" in cmd
    idx = cmd.index("--mcp-config")
    assert "ralph" in cmd[idx + 1]


def test_load_config_cli_override_propagates_to_invoke_options() -> None:
    """End-to-end: CLI --unsafe-mode reaches GeneralConfig and InvokeOptions."""
    cfg = load_config(
        workspace_scope=WorkspaceScope(Path("/tmp")),
        cli_overrides={"general": {"workflow": {"unsafe_mode": True}}},
    )
    assert cfg.general.workflow.unsafe_mode is True

    opts = build_invoke_options_from_config(cfg.general)
    assert opts.unsafe_mode is True


def test_load_config_absent_override_keeps_default() -> None:
    """A CLI run without --unsafe-mode keeps the default of False."""
    cfg = load_config(workspace_scope=WorkspaceScope(Path("/tmp")), cli_overrides={})
    assert cfg.general.workflow.unsafe_mode is False
