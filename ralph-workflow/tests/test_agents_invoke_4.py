"""Tests for agent command construction."""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest
from loguru import logger

from ralph.agents.invoke import (
    BuildCommandOptions,
    InvokeOptions,
    UnsupportedMcpTransportError,
    build_command,
    check_agent_available,
    command_for_log,
    invoke_agent,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import (
    RALPH_MCP_SERVER_NAME,
)
from ralph.mcp.transport.codex import prepare_codex_home
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


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


def test_codex_mode_extracts_upstream_servers_without_passing_them_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        '[mcp_servers.angular-cli]\ncommand = "npx"\nargs = ["-y", "@angular/cli", "mcp"]\n',
        encoding="utf-8",
    )
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

    parsed = _toml_object(seen_config[0])
    mcp_servers = cast("dict[str, object]", parsed["mcp_servers"])
    assert list(mcp_servers.keys()) == [RALPH_MCP_SERVER_NAME]
    ralph_server = cast("dict[str, object]", mcp_servers[RALPH_MCP_SERVER_NAME])
    assert ralph_server["url"] == "http://127.0.0.1:9999/mcp"
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(
            name="angular-cli",
            transport="stdio",
            command="npx",
            args=("-y", "@angular/cli", "mcp"),
        ),
    )


def test_codex_mode_rejects_duplicate_ralph_server_name(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake_codex"
    fake_home.mkdir()
    (fake_home / "config.toml").write_text(
        '[mcp_servers.ralph]\nurl = "http://wrong.example/mcp"\nenabled = false\n',
        encoding="utf-8",
    )
    with pytest.raises(UpstreamConfigError, match="ralph"):
        prepare_codex_home(
            "http://localhost:0/mcp",
            workspace_path=tmp_path,
            existing_home=str(fake_home),
            system_prompt_file=None,
        )


def test_codex_config_toml_preserves_unrelated_top_level_sections(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake_codex"
    fake_home.mkdir()
    (fake_home / "config.toml").write_text(
        'model = "gpt-5"\napproval_policy = "never"\n',
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
    assert parsed["model"] == "gpt-5"
    assert parsed["approval_policy"] == "never"


def test_codex_config_toml_omits_features_when_no_endpoint(tmp_path: Path) -> None:
    home = prepare_codex_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
        system_prompt_file="/tmp/sp.md",
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _toml_object(config_text)
    features = cast("dict[str, object]", parsed["features"]) if "features" in parsed else {}
    assert "shell_tool" not in features, "No features disable without endpoint"


def test_invoke_agent_fails_fast_when_mcp_endpoint_has_unsupported_transport(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="some-agent",
        output_flag="--json-stream",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.GENERIC,
    )

    with pytest.raises(UnsupportedMcpTransportError):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
                ),
            )
        )


def test_codex_logs_best_effort_warning_when_mcp_endpoint_wired(tmp_path: Path) -> None:
    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="WARNING")
    try:
        prepare_codex_home(
            "http://localhost:0/mcp",
            workspace_path=tmp_path,
            existing_home=None,
            system_prompt_file=None,
        )
        output = buf.getvalue()
        assert "best-effort" in output, f"Expected 'best-effort' in warning, got: {output!r}"
        assert "Codex" in output, f"Expected 'Codex' in warning, got: {output!r}"
    finally:
        logger.remove(handler_id)


def test_codex_does_not_log_warning_when_no_endpoint(tmp_path: Path) -> None:
    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="WARNING")
    try:
        prepare_codex_home(
            None,
            workspace_path=tmp_path,
            existing_home=None,
            system_prompt_file="/tmp/sp.md",
        )
        assert "best-effort" not in buf.getvalue(), "No warning when endpoint is None"
    finally:
        logger.remove(handler_id)


def test_claude_strict_mode_only_exposes_ralph_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {"command": "mcp-server-filesystem", "args": ["/tmp"]},
                    "github": {"type": "http", "url": "https://api.github.com/mcp"},
                }
            }
        ),
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
    monkeypatch.setenv("HOME", str(fake_home))

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

    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _json_object(cmd[mcp_index + 1])
    servers = cast("dict[str, object]", config_payload["mcpServers"])
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(servers.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible MCP config, "
        f"got: {list(servers.keys())}"
    )
    ralph_entry = cast("dict[str, object]", servers[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


def test_opencode_strict_mode_only_exposes_ralph_server(
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
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps(
            {
                "model": "anthropic/test",
                "mcp": {
                    "filesystem": {
                        "type": "local",
                        "command": "mcp-server-filesystem",
                        "args": ["/tmp"],
                    },
                    "github": {"type": "remote", "url": "https://api.github.com/mcp"},
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
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(mcp_config.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible OpenCode MCP config, "
        f"got: {list(mcp_config.keys())}"
    )
    ralph_entry = cast("dict[str, object]", mcp_config[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


def test_codex_strict_mode_only_exposes_ralph_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        "[mcp_servers.filesystem]\n"
        'command = "mcp-server-filesystem"\n'
        'args = ["/tmp"]\n'
        "\n"
        "[mcp_servers.github]\n"
        'url = "https://api.github.com/mcp"\n',
        encoding="utf-8",
    )
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

    parsed = _toml_object(seen_config[0])
    mcp_servers = cast("dict[str, object]", parsed["mcp_servers"])
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(mcp_servers.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible Codex mcp_servers, "
        f"got: {list(mcp_servers.keys())}"
    )
    ralph_entry = cast("dict[str, object]", mcp_servers[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


def test_provider_strict_mode_passes_upstream_proxy_payload_to_ralph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    seen_envs: dict[str, dict[str, str]] = {}

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

    # --- Claude ---
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"upstream-server": {"command": "upstream-cmd"}}}),
        encoding="utf-8",
    )
    claude_config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    def fake_popen_claude(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_envs["claude"] = _env_dict(kwargs)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen_claude)
    monkeypatch.setenv("HOME", str(fake_home))
    list(
        invoke_agent(
            claude_config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    # --- OpenCode ---
    def fake_popen_opencode(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_envs["opencode"] = _env_dict(kwargs)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen_opencode)
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps({"mcp": {"upstream-server": {"type": "local", "command": "upstream-cmd"}}}),
    )
    opencode_config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    list(
        invoke_agent(
            opencode_config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    # --- Codex ---
    source_codex_home = tmp_path / "codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "config.toml").write_text(
        '[mcp_servers.upstream-server]\ncommand = "upstream-cmd"\n',
        encoding="utf-8",
    )

    def fake_popen_codex(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_envs["codex"] = _env_dict(kwargs)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen_codex)
    monkeypatch.setenv("CODEX_HOME", str(source_codex_home))
    codex_config = AgentConfig(
        cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX
    )
    list(
        invoke_agent(
            codex_config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    # All three transports must pass upstream proxy payload to Ralph via env
    for transport_name in ("claude", "opencode", "codex"):
        env = seen_envs[transport_name]
        assert UPSTREAM_MCP_CONFIG_ENV in env, (
            f"Transport '{transport_name}' did not set {UPSTREAM_MCP_CONFIG_ENV} "
            "for Ralph upstream proxy payload"
        )
        upstreams = load_upstream_mcp_servers(env[UPSTREAM_MCP_CONFIG_ENV])
        assert any(s.name == "upstream-server" for s in upstreams), (
            f"Transport '{transport_name}' did not include 'upstream-server' "
            "in the upstream proxy payload passed to Ralph"
        )


def test_claude_strict_mode_inlines_prompt_content_not_file_path(tmp_path: Path) -> None:
    prompt_text = "Generate a commit message for the staged diff.\n"
    prompt_file = tmp_path / "commit_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(mcp_endpoint="http://localhost:9999"),
    )

    assert cmd[-1] == prompt_text, (
        "Claude strict-mode must inline prompt content after '--', not pass the file path. "
        "Passing the path causes the model to call mcp__ralph__read_file which triggers "
        "classifier blocks and permission prompts."
    )
    assert str(prompt_file) not in cmd


def test_claude_strict_mode_command_for_log_shows_path_not_content(tmp_path: Path) -> None:
    prompt_text = "Generate a commit message.\n"
    prompt_file = tmp_path / "commit_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(mcp_endpoint="http://localhost:9999"),
    )
    log_line = command_for_log(config, cmd, str(prompt_file))

    assert str(prompt_file) in log_line
    assert prompt_text.strip() not in log_line


def test_check_agent_available_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke.shutil.which", lambda name: f"/usr/bin/{name}")
    config = AgentConfig(cmd="claude")
    assert check_agent_available(config) is True


def test_check_agent_available_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke.shutil.which", lambda name: None)
    config = AgentConfig(cmd="nonexistent-xyz")
    assert check_agent_available(config) is False


def test_check_agent_available_empty_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def recording_which(name: str) -> str | None:
        calls.append(name)
        return None

    monkeypatch.setattr("ralph.agents.invoke.shutil.which", recording_which)
    config = AgentConfig(cmd="")
    assert check_agent_available(config) is False
    assert calls == []
