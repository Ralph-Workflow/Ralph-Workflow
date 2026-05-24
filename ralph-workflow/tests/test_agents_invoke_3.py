"""Tests for agent command construction."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    invoke_agent,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    CODEX_NATIVE_FEATURES_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    RALPH_MCP_SERVER_NAME,
)
from ralph.mcp.transport.codex import prepare_codex_home
from ralph.mcp.transport.opencode import merge_opencode_config_content
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


_EXPECTED_DESCENDANT_LIVENESS_CHECKS = 2


@pytest.fixture(autouse=True)
def _disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda _path: None)


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


def test_invoke_agent_surfaces_stdout_error_when_stderr_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            api_error = (
                '{"type":"error","error":{"type":"api_error","message":"Internal server error"}}'
            )
            self.stdout = iter(
                [
                    f"claude: API Error: 500 {api_error}\n",
                    f"claude stop: result=API Error: 500 {api_error}\n",
                ]
            )
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 1

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    with pytest.raises(AgentInvocationError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False),
                _clock=FakeClock(),
            )
        )

    api_error = '{"type":"error","error":{"type":"api_error","message":"Internal server error"}}'
    assert "Internal server error" in str(exc_info.value)
    assert exc_info.value.parsed_output == [
        f"claude: API Error: 500 {api_error}",
        f"claude stop: result=API Error: 500 {api_error}",
    ]


def test_invoke_agent_injects_opencode_mcp_config_for_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    config_content = _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = cast("dict[str, object]", config_content["mcp"])
    ralph_config = cast("dict[str, object]", mcp_config["ralph"])
    permission_config = cast("dict[str, object]", config_content["permission"])
    assert config_content["$schema"] == "https://opencode.ai/config.json"
    assert ralph_config["type"] == "remote"
    assert ralph_config["url"] == "http://127.0.0.1:9999/mcp"
    assert ralph_config["enabled"] is True
    assert permission_config["ralph_*"] == "allow"


def test_invoke_agent_merges_existing_opencode_config_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "anthropic/test"}')

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    config_content = _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = cast("dict[str, object]", config_content["mcp"])
    ralph_config = cast("dict[str, object]", mcp_config["ralph"])
    assert config_content["model"] == "anthropic/test"
    assert ralph_config["url"] == "http://127.0.0.1:9999/mcp"


def test_invoke_agent_does_not_inject_opencode_mcp_config_without_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "anthropic/test"}')

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    assert _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/test"}


def test_opencode_config_disables_all_native_tools_when_mcp_wired() -> None:
    result = merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _json_object(result)
    tools = cast("dict[str, object]", parsed["tools"])
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False, f"Expected {name} to be False"


def test_opencode_config_tools_disable_overrides_user_enables() -> None:
    existing = '{"tools": {"bash": true}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    tools = cast("dict[str, object]", parsed["tools"])
    assert tools["bash"] is False, "MCP policy must override user enable"


def test_opencode_config_preserves_unrelated_user_tools_sections() -> None:
    existing = '{"tools": {"custom_plugin_tool": true}, "ui": {"theme": "dark"}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    tools = cast("dict[str, object]", parsed["tools"])
    ui = cast("dict[str, object]", parsed["ui"])
    assert tools["custom_plugin_tool"] is True
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False
    assert ui["theme"] == "dark"


def test_opencode_mode_extracts_upstream_servers_without_passing_them_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_env.append(_env_dict(kwargs))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda _path: None)
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps(
            {
                "model": "anthropic/test",
                "mcp": {
                    "angular-cli": {
                        "type": "local",
                        "command": "npx",
                        "args": ["-y", "@angular/cli", "mcp"],
                    }
                },
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
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    parsed = _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = cast("dict[str, object]", parsed["mcp"])
    assert mcp_config == {
        "ralph": {
            "type": "remote",
            "url": "http://127.0.0.1:9999/mcp",
            "enabled": True,
            "timeout": 30000,
        }
    }
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(
            name="angular-cli",
            transport="stdio",
            command="npx",
            args=("-y", "@angular/cli", "mcp"),
        ),
    )


def test_opencode_mode_rejects_duplicate_ralph_server_name() -> None:
    existing = '{"mcp": {"ralph": {"type": "remote", "url": "http://wrong.example/mcp"}}}'
    with pytest.raises(UpstreamConfigError, match="ralph"):
        merge_opencode_config_content(existing, "http://localhost:0/mcp")


def test_opencode_config_preserves_unrelated_permission_entries() -> None:
    existing = '{"permission": {"bash": "ask", "custom_tool": "allow"}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    permission = cast("dict[str, object]", parsed["permission"])
    assert permission["bash"] == "ask"
    assert permission["custom_tool"] == "allow"
    assert permission["ralph_*"] == "allow"


def test_opencode_config_allows_all_bare_ralph_mcp_tool_names() -> None:
    result = merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _json_object(result)
    permission = cast("dict[str, object]", parsed["permission"])

    for tool_name in ALL_RALPH_TOOLS:
        assert permission[str(tool_name)] == "allow"


def test_opencode_config_normalizes_non_dict_mcp_sections() -> None:
    existing = '{"mcp": "invalid", "permission": "invalid", "tools": "invalid"}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    mcp_config = cast("dict[str, object]", parsed["mcp"])
    permission = cast("dict[str, object]", parsed["permission"])
    tools = cast("dict[str, object]", parsed["tools"])
    assert mcp_config["ralph"]
    assert permission["ralph_*"] == "allow"
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False


def test_opencode_config_omits_tools_block_when_no_mcp_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "anthropic/test"}')

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    config_content = _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    assert "tools" not in config_content, "No tools block should be added without MCP endpoint"


def test_invoke_agent_injects_codex_mcp_config_for_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_env: list[dict[str, str]] = []
    seen_config: list[str] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    assert "CODEX_HOME" in seen_env[0]
    assert len(seen_config) == 1
    expected_server = (
        f'[mcp_servers.{RALPH_MCP_SERVER_NAME}]\nurl = "http://127.0.0.1:9999/mcp"\nenabled = true'
    )
    assert expected_server in seen_config[0]


def test_invoke_agent_injects_codex_system_prompt_file_via_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    system_prompt_file = tmp_path / "SYSTEM_PROMPT.md"
    system_prompt_file.write_text("unattended mode", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_config: list[str] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                system_prompt_file=str(system_prompt_file),
            ),
            _clock=FakeClock(),
        )
    )

    assert len(seen_config) == 1
    parsed = _toml_object(seen_config[0])
    assert parsed["model_instructions_file"] == str(system_prompt_file)
    features = cast("dict[str, object] | None", parsed.get("features"))
    if features is not None:
        assert "model_instructions_file" not in features


def test_invoke_agent_does_not_inject_opencode_system_prompt_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    system_prompt_file = tmp_path / "SYSTEM_PROMPT.md"
    system_prompt_file.write_text("unattended mode", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_cmds: list[list[str]] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del kwargs
        seen_cmds.append(_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                system_prompt_file=str(system_prompt_file),
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_cmds == [["opencode", "run", "--format", "json", "hello"]]


def test_invoke_agent_preserves_existing_codex_home_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    copied_auth: list[str] = []

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        codex_home = Path(env["CODEX_HOME"])
        copied_auth.append((codex_home / "auth.json").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda _path: None)
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert copied_auth == ['{"token":"secret"}']


def test_codex_config_toml_disables_all_features_when_mcp_wired(tmp_path: Path) -> None:
    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=None,
        system_prompt_file=None,
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _toml_object(config_text)
    for key, _value in CODEX_NATIVE_FEATURES_TO_DISABLE:
        if "." in key:
            section, subkey = key.split(".", 1)
            nested = cast("dict[str, object]", parsed[section])
            assert nested[subkey] is False, f"Expected {key} = false"
        else:
            assert parsed[key] == "disabled", f"Expected {key} = disabled"
    features = cast("dict[str, object]", parsed["features"])
    assert "web_search" not in features


def test_codex_config_toml_keeps_model_instructions_outside_features(tmp_path: Path) -> None:
    system_prompt_file = tmp_path / "SYSTEM_PROMPT.md"
    system_prompt_file.write_text("system", encoding="utf-8")
    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=None,
        system_prompt_file=str(system_prompt_file),
    )
    parsed = _toml_object((Path(home) / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model_instructions_file"] == str(system_prompt_file)
    features = cast("dict[str, object]", parsed["features"])
    assert "model_instructions_file" not in features


def test_codex_config_toml_preserves_existing_features_section(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake_codex"
    fake_home.mkdir()
    (fake_home / "config.toml").write_text(
        '[features]\nfoo = true\n\n[profiles.default]\nmodel = "gpt-5"\n',
        encoding="utf-8",
    )
    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=str(fake_home),
        system_prompt_file=None,
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _toml_object(config_text)
    features = cast("dict[str, object]", parsed["features"])
    assert features["foo"] is True, "Existing feature should be preserved"
    assert features["shell_tool"] is False
    assert features["multi_agent"] is False
    assert features["undo"] is False
    assert features["apps"] is False
