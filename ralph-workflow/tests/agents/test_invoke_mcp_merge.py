"""Tests for mcp.toml merge into upstream env-var flow across all three agent paths."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import patch

from loguru import logger

from ralph.agents.invoke import (
    InvokeOptions,
    _mcp_toml_as_upstreams,
    _merge_mcp_toml_into_upstreams,
    invoke_agent,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.mcp_models import McpConfig, McpServerSpec
from ralph.config.models import AgentConfig
from ralph.mcp.upstream_config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return cast("dict[str, str]", env_obj)


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = iter(["ok\n"])
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode = 0

    def __enter__(self) -> _FakeProcess:
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> Literal[False]: return False

    def wait(self) -> int:
        return self.returncode


_TOML_SERVER = UpstreamMcpServer(
    name="toml-injected",
    transport="http",
    url="http://toml.example/mcp",
)


def _fake_mcp_toml_as_upstreams(workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]:
    return (_TOML_SERVER,)


def _fake_popen_capturing(seen_env: list[dict[str, str]]) -> object:
    def fake_popen(*args: object, **kwargs: object) -> _FakeProcess:
        seen_env.append(_env_dict(kwargs))
        return _FakeProcess()

    return fake_popen


def test_merge_no_collision_preserves_all_servers() -> None:
    native = (
        UpstreamMcpServer(name="native-svc", transport="http", url="http://native.example/mcp"),
    )
    toml = (UpstreamMcpServer(name="toml-svc", transport="http", url="http://toml.example/mcp"),)

    result = _merge_mcp_toml_into_upstreams(native, toml)

    names = {s.name for s in result}
    assert names == {"native-svc", "toml-svc"}


def test_merge_collision_mcp_toml_wins() -> None:
    native = (UpstreamMcpServer(name="shared", transport="http", url="http://native.example/mcp"),)
    toml = (UpstreamMcpServer(name="shared", transport="http", url="http://toml.example/mcp"),)

    result = _merge_mcp_toml_into_upstreams(native, toml)

    winning = next(s for s in result if s.name == "shared")
    assert winning.url == "http://toml.example/mcp"


def test_merge_collision_emits_warning() -> None:
    warnings: list[str] = []
    sink_id = logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    try:
        native = (
            UpstreamMcpServer(name="shared", transport="http", url="http://native.example/mcp"),
        )
        toml = (UpstreamMcpServer(name="shared", transport="http", url="http://toml.example/mcp"),)
        _merge_mcp_toml_into_upstreams(native, toml)
    finally:
        logger.remove(sink_id)

    assert any("shared" in w and "overrides" in w for w in warnings)


def test_merge_empty_toml_is_noop() -> None:
    native = (
        UpstreamMcpServer(name="native-svc", transport="http", url="http://native.example/mcp"),
    )
    assert _merge_mcp_toml_into_upstreams(native, ()) == native


def test_merge_empty_native_returns_toml_servers() -> None:
    toml = (UpstreamMcpServer(name="toml-svc", transport="stdio", command="my-cmd"),)
    assert _merge_mcp_toml_into_upstreams((), toml) == toml


def test_mcp_toml_as_upstreams_converts_http_server(tmp_path: Path) -> None:
    spec = McpServerSpec(name="my-http-svc", transport="http", url="http://example.com/mcp")
    fake_config = McpConfig(mcp_servers={"my-http-svc": spec})

    with patch("ralph.agents.transport_emit.load_mcp_config", return_value=fake_config):
        result = _mcp_toml_as_upstreams(tmp_path)

    assert len(result) == 1
    assert result[0].name == "my-http-svc"
    assert result[0].transport == "http"
    assert result[0].url == "http://example.com/mcp"


def test_mcp_toml_as_upstreams_converts_stdio_server(tmp_path: Path) -> None:
    spec = McpServerSpec(
        name="my-stdio-svc",
        transport="stdio",
        command="my-cmd",
        args=["--flag", "val"],
        env={"MY_VAR": "my-val"},
    )
    fake_config = McpConfig(mcp_servers={"my-stdio-svc": spec})

    with patch("ralph.agents.transport_emit.load_mcp_config", return_value=fake_config):
        result = _mcp_toml_as_upstreams(tmp_path)

    assert len(result) == 1
    s = result[0]
    assert s.name == "my-stdio-svc"
    assert s.transport == "stdio"
    assert s.command == "my-cmd"
    assert s.args == ("--flag", "val")
    assert s.env == {"MY_VAR": "my-val"}


def test_mcp_toml_as_upstreams_passes_local_agent_path(tmp_path: Path) -> None:
    captured: list[Path | None] = []

    def fake_load(config_path: Path | None = None, workspace_scope: object = None) -> McpConfig:
        captured.append(config_path)
        return McpConfig()

    with patch("ralph.agents.transport_emit.load_mcp_config", side_effect=fake_load):
        _mcp_toml_as_upstreams(tmp_path)

    assert captured == [tmp_path / ".agent" / "mcp.toml"]


def test_mcp_toml_as_upstreams_none_workspace_passes_none(tmp_path: Path) -> None:
    captured: list[Path | None] = []

    def fake_load(config_path: Path | None = None, workspace_scope: object = None) -> McpConfig:
        captured.append(config_path)
        return McpConfig()

    with patch("ralph.agents.transport_emit.load_mcp_config", side_effect=fake_load):
        _mcp_toml_as_upstreams(None)

    assert captured == [None]


def test_claude_upstream_env_var_includes_mcp_toml_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    seen_env: list[dict[str, str]] = []
    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", _fake_popen_capturing(seen_env))
    monkeypatch.setattr("ralph.agents.invoke._mcp_toml_as_upstreams", _fake_mcp_toml_as_upstreams)
    monkeypatch.setattr("ralph.agents.invoke._provider_allowed_mcp_tool_names", lambda cfg, _ep: ())
    monkeypatch.setenv("HOME", str(fake_home))

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    upstreams = load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV])
    assert any(s.name == "toml-injected" for s in upstreams)


def test_opencode_upstream_env_var_includes_mcp_toml_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []
    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", _fake_popen_capturing(seen_env))
    monkeypatch.setattr("ralph.agents.invoke._mcp_toml_as_upstreams", _fake_mcp_toml_as_upstreams)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    upstreams = load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV])
    assert any(s.name == "toml-injected" for s in upstreams)


def test_codex_upstream_env_var_includes_mcp_toml_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_env: list[dict[str, str]] = []
    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", _fake_popen_capturing(seen_env))
    monkeypatch.setattr("ralph.agents.invoke._mcp_toml_as_upstreams", _fake_mcp_toml_as_upstreams)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    upstreams = load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV])
    assert any(s.name == "toml-injected" for s in upstreams)


def test_claude_collision_mcp_toml_overrides_native_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"toml-injected": {"command": "native-cmd"}}}),
        encoding="utf-8",
    )
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    seen_env: list[dict[str, str]] = []
    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", _fake_popen_capturing(seen_env))
    monkeypatch.setattr("ralph.agents.invoke._mcp_toml_as_upstreams", _fake_mcp_toml_as_upstreams)
    monkeypatch.setattr("ralph.agents.invoke._provider_allowed_mcp_tool_names", lambda cfg, _ep: ())
    monkeypatch.setenv("HOME", str(fake_home))

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    upstreams = load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV])
    winning = next(s for s in upstreams if s.name == "toml-injected")
    assert winning.url == "http://toml.example/mcp"


def test_opencode_non_colliding_native_server_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []
    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", _fake_popen_capturing(seen_env))
    monkeypatch.setattr("ralph.agents.invoke._mcp_toml_as_upstreams", _fake_mcp_toml_as_upstreams)
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps(
            {
                "mcp": {
                    "native-angular": {
                        "type": "local",
                        "command": "npx",
                        "args": ["-y", "@angular/cli", "mcp"],
                    }
                }
            }
        ),
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    upstreams = load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV])
    names = {s.name for s in upstreams}
    assert "native-angular" in names
    assert "toml-injected" in names
