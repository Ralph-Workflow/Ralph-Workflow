"""Tests for agent command construction."""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from loguru import logger

from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    UnsupportedMcpTransportError,
    _build_command,
    _BuildCommandOptions,
    _command_for_log,
    _merge_opencode_config_content,
    _prepare_codex_home,
    _provider_allowed_mcp_tool_names,
    invoke_agent,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.tool_names import (
    ALL_RALPH_TOOLS,
    CODEX_NATIVE_FEATURES_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.upstream_config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)


def _json_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(raw))


def _toml_object(raw: str) -> dict[str, object]:
    return cast("dict[str, object]", tomllib.loads(raw))


def _env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return cast("dict[str, str]", env_obj)


def _argv(args: tuple[object, ...]) -> list[str]:
    return cast("list[str]", args[0])


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


def test_build_command_injects_claude_append_system_prompt_file() -> None:
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        options=_BuildCommandOptions(system_prompt_file="SYSTEM_PROMPT.md"),
    )

    assert cmd == [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--print",
        "--include-partial-messages",
        "--permission-mode",
        "auto",
        "--append-system-prompt-file",
        "SYSTEM_PROMPT.md",
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


def test_build_command_injects_claude_mcp_config_for_remote_endpoint(
    tmp_path: Path,
) -> None:
    prompt_content = "commit prompt content"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(prompt_content, encoding="utf-8")
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
        str(prompt_file),
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(
                claude_tool_name("read_file"),
                claude_tool_name("report_progress"),
            ),
        ),
    )

    assert "--mcp-config" in cmd
    mcp_index = cmd.index("--mcp-config")
    config_payload = _json_object(cmd[mcp_index + 1])
    servers = cast("dict[str, object]", config_payload["mcpServers"])
    assert cast("dict[str, object]", servers["ralph"]) == {
        "type": "http",
        "url": "http://127.0.0.1:9999/mcp",
    }
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == ",".join(
        [
            claude_tool_name("read_file"),
            claude_tool_name("report_progress"),
        ]
    )
    assert cmd[-2:] == ["--", prompt_content]


def test_build_command_resolves_relative_claude_prompt_from_workspace_path(tmp_path: Path) -> None:
    prompt_content = "commit prompt content"
    prompt_dir = tmp_path / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / "PROMPT.md"
    prompt_path.write_text(prompt_content, encoding="utf-8")
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
        ".agent/tmp/PROMPT.md",
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(claude_tool_name("read_file"),),
            workspace_path=tmp_path,
        ),
    )

    assert cmd[-2:] == ["--", prompt_content]


def test_build_command_uses_transport_metadata_not_command_name_for_claude_mcp(
    tmp_path: Path,
) -> None:
    prompt_content = "commit prompt content"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(prompt_content, encoding="utf-8")
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
        str(prompt_file),
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(claude_tool_name("read_file"),),
        ),
    )

    assert "--mcp-config" in cmd
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == claude_tool_name("read_file")
    assert cmd[-2:] == ["--", prompt_content]


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del kwargs
        return FakeProcess(_argv(args))

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        del kwargs
        run_calls.append(cmd)

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke.subprocess.run", fake_run)

    lines = list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

    assert lines == ["line-one\n"]
    assert len(popen_calls) == 1
    assert run_calls == []


def test_invoke_agent_passes_extra_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke._provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_artifact"),
        ),
    )

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


def test_invoke_agent_runs_subprocess_in_workspace_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_cwds: list[str | None] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_cwds.append(cast("str | None", kwargs.get("cwd")))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke._provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_artifact"),
        ),
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
        )
    )

    assert seen_cwds == [str(tmp_path)]


def test_invoke_agent_passes_claude_mcp_separator_in_subprocess_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del kwargs
        seen_cmds.append(_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke._provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_artifact"),
        ),
    )

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

    assert seen_cmds
    cmd = seen_cmds[0]
    assert cmd[:10] == [
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
    ]
    mcp_payload = _json_object(cmd[10])
    servers = cast("dict[str, object]", mcp_payload["mcpServers"])
    assert cast("dict[str, object]", servers["ralph"]) == {
        "type": "http",
        "url": "http://127.0.0.1:9999/mcp",
    }
    assert cmd[11:] == [
        "--strict-mcp-config",
        "--tools",
        "",
        "--allowedTools",
        ",".join(
            [
                claude_tool_name("read_file"),
                claude_tool_name("ralph_submit_artifact"),
            ]
        ),
        "--model",
        "claude-sonnet-4",
        "--",
        "hello",
    ]


def test_provider_allowed_mcp_tool_names_maps_live_ralph_endpoint_to_claude_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._discover_http_mcp_tool_names",
        lambda endpoint: ["read_file", "ralph_submit_artifact"],
    )

    allowed = _provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_artifact"),
    )


def test_claude_builtin_command_preserves_login_capable_mode(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
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
        str(prompt_file),
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(
                claude_tool_name("read_file"),
                claude_tool_name("report_progress"),
            ),
        ),
    )

    assert "--bare" not in cmd
    assert "--mcp-config" in cmd
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == ",".join(
        [claude_tool_name("read_file"), claude_tool_name("report_progress")]
    )


def test_build_command_claude_injects_empty_tools_when_mcp_endpoint_wired(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
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
        str(prompt_file),
        options=_BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(claude_tool_name("read_file"),),
        ),
    )
    tools_idx = cmd.index("--tools")
    assert cmd[tools_idx + 1] == ""
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == claude_tool_name("read_file")


def test_build_command_claude_injects_strict_mcp_config_when_mcp_endpoint_wired(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
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
        str(prompt_file),
        options=_BuildCommandOptions(mcp_endpoint="http://127.0.0.1:9999/mcp"),
    )
    assert "--mcp-config" in cmd


def test_invoke_agent_claude_extracts_existing_workspace_mcp_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "angular-cli": {
                        "command": "npx",
                        "args": ["-y", "@angular/cli", "mcp"],
                    }
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
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        seen_env.append(_env_dict(kwargs))
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _json_object(cmd[mcp_index + 1])
    servers = cast("dict[str, object]", config_payload["mcpServers"])
    assert servers == {
        "ralph": {
            "type": "http",
            "url": "http://127.0.0.1:9999/mcp",
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


def test_claude_mode_extracts_upstream_servers_without_passing_them_through(
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
                    "angular-cli": {
                        "command": "npx",
                        "args": ["-y", "@angular/cli", "mcp"],
                    }
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
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        seen_env.append(_env_dict(kwargs))
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _json_object(cmd[mcp_index + 1])
    servers = cast("dict[str, object]", config_payload["mcpServers"])
    assert servers == {
        "ralph": {
            "type": "http",
            "url": "http://127.0.0.1:9999/mcp",
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


def test_claude_mode_prefers_workspace_upstream_server_over_home_definition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "angular-cli": {
                        "command": "workspace-cmd",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"angular-cli": {"command": "home-cmd"}}}),
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
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        seen_env.append(_env_dict(kwargs))
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _json_object(cmd[mcp_index + 1])
    servers = cast("dict[str, object]", config_payload["mcpServers"])
    assert servers == {
        "ralph": {
            "type": "http",
            "url": "http://127.0.0.1:9999/mcp",
        }
    }
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(name="angular-cli", transport="stdio", command="workspace-cmd"),
    )


def test_claude_mode_rejects_duplicate_ralph_server_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "type": "http",
                        "url": "http://wrong.example/mcp",
                    }
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
    with pytest.raises(UpstreamConfigError, match="ralph"):
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


def test_build_command_claude_omits_tools_flag_when_no_mcp_endpoint() -> None:
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
        options=_BuildCommandOptions(),
    )
    assert "--tools" not in cmd
    assert "--strict-mcp-config" not in cmd


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

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    with pytest.raises(AgentInvocationError) as exc_info:
        list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv("RALPH_MCP_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "anthropic/test"}')

    list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

    assert seen_env
    assert _json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/test"}


def test_opencode_config_disables_all_native_tools_when_mcp_wired() -> None:
    result = _merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _json_object(result)
    tools = cast("dict[str, object]", parsed["tools"])
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False, f"Expected {name} to be False"


def test_opencode_config_tools_disable_overrides_user_enables() -> None:
    existing = '{"tools": {"bash": true}}'
    result = _merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    tools = cast("dict[str, object]", parsed["tools"])
    assert tools["bash"] is False, "MCP policy must override user enable"


def test_opencode_config_preserves_unrelated_user_tools_sections() -> None:
    existing = '{"tools": {"custom_plugin_tool": true}, "ui": {"theme": "dark"}}'
    result = _merge_opencode_config_content(existing, "http://localhost:0/mcp")
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        _merge_opencode_config_content(existing, "http://localhost:0/mcp")


def test_opencode_config_preserves_unrelated_permission_entries() -> None:
    existing = '{"permission": {"bash": "ask", "custom_tool": "allow"}}'
    result = _merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _json_object(result)
    permission = cast("dict[str, object]", parsed["permission"])
    assert permission["bash"] == "ask"
    assert permission["custom_tool"] == "allow"
    assert permission["ralph_*"] == "allow"


def test_opencode_config_allows_all_bare_ralph_mcp_tool_names() -> None:
    result = _merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _json_object(result)
    permission = cast("dict[str, object]", parsed["permission"])

    for tool_name in ALL_RALPH_TOOLS:
        assert permission[str(tool_name)] == "allow"


def test_opencode_config_normalizes_non_dict_mcp_sections() -> None:
    existing = '{"mcp": "invalid", "permission": "invalid", "tools": "invalid"}'
    result = _merge_opencode_config_content(existing, "http://localhost:0/mcp")
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.delenv("RALPH_MCP_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "anthropic/test"}')

    list(invoke_agent(config, str(prompt_file), options=InvokeOptions(show_progress=False)))

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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args
        env = _env_dict(kwargs)
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


def test_codex_config_toml_disables_all_features_when_mcp_wired(tmp_path: Path) -> None:
    home = _prepare_codex_home(
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
    home = _prepare_codex_home(
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
    home = _prepare_codex_home(
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        _prepare_codex_home(
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
    home = _prepare_codex_home(
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
    home = _prepare_codex_home(
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
                    extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
                ),
            )
        )


def test_codex_logs_best_effort_warning_when_mcp_endpoint_wired(tmp_path: Path) -> None:
    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="WARNING")
    try:
        _prepare_codex_home(
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
        _prepare_codex_home(
            None,
            workspace_path=tmp_path,
            existing_home=None,
            system_prompt_file="/tmp/sp.md",
        )
        assert "best-effort" not in buf.getvalue(), "No warning when endpoint is None"
    finally:
        logger.remove(handler_id)


# ---------------------------------------------------------------------------
# Task 7: Apply Ralph-only MCP visibility across all provider transports
# ---------------------------------------------------------------------------


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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
            ),
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
        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
            return False

        def wait(self) -> int:
            return self.returncode

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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
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
                extra_env={"RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp"},
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

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(mcp_endpoint="http://localhost:9999"),
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

    cmd = _build_command(
        config,
        str(prompt_file),
        options=_BuildCommandOptions(mcp_endpoint="http://localhost:9999"),
    )
    log_line = _command_for_log(config, cmd, str(prompt_file))

    assert str(prompt_file) in log_line
    assert prompt_text.strip() not in log_line
