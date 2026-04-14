"""Tests for agent command construction."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.agents.invoke import InvokeOptions, _build_command, _command_for_log, invoke_agent
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path


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
        model_flag="--model claude-sonnet-4",
        session_id="abc123",
        verbose=True,
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


def test_build_command_omits_optional_flags_when_not_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("plain prompt", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(config, str(prompt_file), session_id="abc123", verbose=False)

    assert cmd == ["opencode", "run", "--format", "json", "plain prompt"]


def test_build_command_uses_opencode_run_json_with_prompt_contents(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("say hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        model_flag="-m minimax/MiniMax-M2.7-highspeed",
    )

    cmd = _build_command(config, str(prompt_file), session_id="abc123", verbose=False)

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

    cmd = _build_command(config, str(prompt_file), verbose=False, pure=True)

    assert cmd == [
        "opencode",
        "run",
        "--pure",
        "--format",
        "json",
        "say hello",
    ]


def test_command_for_log_redacts_opencode_inline_prompt_and_shows_prompt_file(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("super secret prompt body", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(config, str(prompt_file), session_id="abc123", verbose=False)
    logged = _command_for_log(config, cmd, str(prompt_file))

    assert "super secret prompt body" not in logged
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
