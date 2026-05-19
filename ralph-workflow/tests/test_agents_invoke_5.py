"""Tests for agent command construction."""

from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING, cast

from ralph.agents import invoke as invoke_module
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

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

        def fake_build(config_content: str | None, endpoint: str) -> tuple[str, list[object]]:
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
            system_prompt_file: object,
        ) -> tuple[str, list[object]]:
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
