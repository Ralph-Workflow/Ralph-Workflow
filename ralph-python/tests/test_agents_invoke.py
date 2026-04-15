"""Tests for agent command construction."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    UnsupportedMcpTransportError,
    _build_command,
    _BuildCommandOptions,
    _command_for_log,
    invoke_agent,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.tool_names import RALPH_MCP_SERVER_NAME, claude_allowed_tool_names


def test_build_command_includes_print_streaming_and_session_flags() -> None:
    config = AgentConfig(
        cmd="ccs work",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(
            model_flag="--model claude-sonnet-4",
            session_id="abc123",
            verbose=True,
        ),
    )

    assert cmd == [
        "ccs",
        "work",
        "--output-format=stream-json",
        "--print",
        "--include-partial-messages",
        "--resume",
        "abc123",
        "--dangerously-skip-permissions",
        "--verbose",
        "--model",
        "claude-sonnet-4",
        "PROMPT.md",
    ]


def test_build_command_splits_multi_part_claude_permission_mode_flag() -> None:
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(verbose=False),
    )

    assert cmd == [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--print",
        "--include-partial-messages",
        "--permission-mode",
        "auto",
        "PROMPT.md",
    ]


def test_build_command_omits_optional_flags_when_not_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("plain prompt", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(session_id="abc123", verbose=False),
    )

    assert cmd == ["opencode", "run", "--format", "json", "plain prompt"]


def test_build_command_injects_claude_mcp_config_for_remote_endpoint() -> None:
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
        ),
    )

    assert "--strict-mcp-config" not in cmd
    mcp_index = cmd.index("--mcp-config")
    assert cmd[mcp_index + 1] == (
        '{"mcpServers":{"ralph":{"type":"http","url":"http://127.0.0.1:9999/mcp"}}}'
    )
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == claude_allowed_tool_names()
    assert cmd[-2:] == ["--", "PROMPT.md"]


def test_build_command_uses_transport_metadata_not_command_name_for_claude_mcp() -> None:
    config = AgentConfig(
        cmd="custom-claude-wrapper --json",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(mcp_endpoint="http://127.0.0.1:9999/mcp"),
    )

    assert "--mcp-config" in cmd
    assert cmd[-2:] == ["--", "PROMPT.md"]


def test_build_command_uses_opencode_run_json_with_prompt_contents(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("say hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        model_flag="-m minimax/MiniMax-M2.7-highspeed",
    )

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(session_id="abc123", verbose=False),
    )

    assert cmd == [
        "opencode",
        "run",
        "--format",
        "json",
        "-m",
        "minimax/MiniMax-M2.7-highspeed",
        "say hello",
    ]


def test_build_command_uses_opencode_pure_mode_when_requested(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("say hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(verbose=False, pure=True),
    )

    assert cmd == [
        "opencode",
        "run",
        "--pure",
        "--format",
        "json",
        "say hello",
    ]


def test_build_command_uses_codex_exec_json_with_prompt_contents(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("fix the planner", encoding="utf-8")
    config = AgentConfig(
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        json_parser=JsonParserType.CODEX,
        transport=AgentTransport.CODEX,
    )

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(verbose=False),
    )

    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "fix the planner",
    ]


def test_command_for_log_redacts_opencode_inline_prompt_and_shows_prompt_file(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("super secret prompt body", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(session_id="abc123", verbose=False),
    )
    logged = _command_for_log(config, cmd, str(prompt_file))

    assert "super secret prompt body" not in logged
    assert str(prompt_file) in logged


def test_command_for_log_redacts_codex_inline_prompt_and_shows_prompt_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "planning_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("top secret planning prompt", encoding="utf-8")
    config = AgentConfig(cmd="codex exec", output_flag="--json", transport=AgentTransport.CODEX)

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(verbose=False),
    )
    logged = _command_for_log(config, cmd, str(prompt_file))

    assert "top secret planning prompt" not in logged
    assert str(prompt_file) in logged


def test_invoke_agent_does_not_reexecute_command_after_stream_finishes(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, cmd: list[str]) -> None:
            self.stdout = iter(["line-one\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0
            popen_calls.append(cmd)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(args[0]),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.run",
        lambda cmd, **kwargs: run_calls.append(cmd),
    )

    lines = list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

    assert lines == ["line-one\n"]
    assert len(popen_calls) == 1
    assert run_calls == []


def test_invoke_agent_passes_extra_env_to_subprocess(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False, extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"}
            ),
        )
    )

    assert seen_env
    assert seen_env[0]["RALPH_MCP_ENDPOINT"] == "http://127.0.0.1:9999/mcp"


def test_invoke_agent_passes_claude_mcp_separator_in_subprocess_argv(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    seen_cmds: list[list[str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        seen_cmds.append(args[0])
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                session_id="abc123",
                verbose=True,
                model_flag="--model claude-sonnet-4",
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_cmds == [
        [
            "claude",
            "-p",
            "--output-format=stream-json",
            "--print",
            "--include-partial-messages",
            "--resume",
            "abc123",
            "--dangerously-skip-permissions",
            "--verbose",
            "--mcp-config",
            '{"mcpServers":{"ralph":{"type":"http","url":"http://127.0.0.1:9999/mcp"}}}',
            "--allowedTools",
            claude_allowed_tool_names(),
            "--model",
            "claude-sonnet-4",
            "--",
            str(prompt_file),
        ]
    ]


def test_claude_builtin_command_preserves_login_capable_mode() -> None:
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(mcp_endpoint="http://127.0.0.1:9999/mcp"),
    )

    assert "--bare" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--mcp-config" in cmd


def test_invoke_agent_surfaces_stdout_error_when_stderr_is_empty(
    monkeypatch, tmp_path: Path
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

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    with pytest.raises(AgentInvocationError) as exc_info:
        list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

    api_error = '{"type":"error","error":{"type":"api_error","message":"Internal server error"}}'
    assert "Internal server error" in str(exc_info.value)
    assert exc_info.value.parsed_output == [
        f"claude: API Error: 500 {api_error}",
        f"claude stop: result=API Error: 500 {api_error}",
    ]


def test_invoke_agent_injects_opencode_mcp_config_for_remote_endpoint(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_env
    config_content = json.loads(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    assert config_content["$schema"] == "https://opencode.ai/config.json"
    assert config_content["mcp"]["ralph"]["type"] == "remote"
    assert config_content["mcp"]["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
    assert config_content["mcp"]["ralph"]["enabled"] is True
    assert config_content["permission"]["ralph_*"] == "allow"


def test_invoke_agent_merges_existing_opencode_config_content(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"model": "anthropic/test"}))

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

    assert seen_env
    config_content = json.loads(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    assert config_content["model"] == "anthropic/test"
    assert config_content["mcp"]["ralph"]["url"] == "http://127.0.0.1:9999/mcp"


def test_invoke_agent_does_not_inject_opencode_mcp_config_without_explicit_endpoint(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv("RALPH_MCP_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"model": "anthropic/test"}))

    list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

    assert seen_env
    assert json.loads(seen_env[0]["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/test"}


def test_invoke_agent_injects_codex_mcp_config_for_remote_endpoint(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_env: list[dict[str, str]] = []
    seen_config: list[str] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_env
    assert "CODEX_HOME" in seen_env[0]
    assert len(seen_config) == 1
    expected_server = (
        f"[mcp_servers.{RALPH_MCP_SERVER_NAME}]\n"
        'url = "http://127.0.0.1:9999/mcp"\n'
        "enabled = true\n"
    )
    assert expected_server in seen_config[0]


def test_invoke_agent_preserves_existing_codex_home_state(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    copied_auth: list[str] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args, **kwargs):
        env = kwargs.get("env")
        assert isinstance(env, dict)
        codex_home = Path(env["CODEX_HOME"])
        copied_auth.append((codex_home / "auth.json").read_text(encoding="utf-8"))
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert copied_auth == ['{"token":"secret"}']


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
                    extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
                ),
            )
        )
