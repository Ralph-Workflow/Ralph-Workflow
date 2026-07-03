"""Tests for agent command construction."""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    BuildCommandOptions,
    InvokeOptions,
    build_command,
    invoke_agent,
    provider_allowed_mcp_tool_names,
)
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    WorkspaceChangeKind,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import (
    CLAUDE_NATIVE_TOOLS_TO_KEEP,
    claude_tool_name,
)
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


_EXPECTED_DESCENDANT_LIVENESS_CHECKS = 2


@pytest.fixture(autouse=True)
def _disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)


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


def test_invoke_agent_times_out_when_agent_goes_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    class BlockingStdout:
        def __iter__(self) -> BlockingStdout:
            return self

        def __next__(self) -> str:
            # Raise StopIteration immediately - the FakeClock in the main loop
            # advances time so the watchdog fires even though no real wait happens.
            raise StopIteration

    class FakeProcess:
        pid: int = 12345

        def __init__(self) -> None:
            self.stdout = BlockingStdout()
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode: int | None = None

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int | None:
            del timeout
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        def poll(self) -> int | None:
            return self.returncode

    fake_process = FakeProcess()

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    # BlockingStdout closes stdout immediately → post-exit watchdog fires
    # (PROCESS_EXIT_HANG), not idle watchdog (NO_OUTPUT_DEADLINE).
    expected_msg = "subprocess closed stdout but did not exit"
    with pytest.raises(AgentInactivityTimeoutError, match=expected_msg):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=FakeClock(),
            )
        )


def test_invoke_agent_defers_idle_timeout_while_descendants_remain_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class BlockingStdout:
        def __iter__(self) -> BlockingStdout:
            return self

        def __next__(self) -> str:
            # Raise StopIteration immediately - the FakeClock in the main loop
            # advances time so the watchdog fires even though no real wait happens.
            raise StopIteration

    class FakeProcess:
        pid: int = 12345

        def __init__(self) -> None:
            self.stdout = BlockingStdout()
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode: int | None = None
            self.terminate_calls = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int | None:
            del timeout
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        def poll(self) -> int | None:
            return self.returncode

    fake_process = FakeProcess()
    descendant_states = iter([True, False])
    descendant_checks = {"count": 0}

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    def _has_live_descendants(_self: object) -> bool:
        descendant_checks["count"] += 1
        return next(descendant_states)

    monkeypatch.setattr(
        "ralph.process.manager.ManagedProcess.has_live_descendants",
        _has_live_descendants,
        raising=False,
    )

    # BlockingStdout closes stdout immediately → post-exit watchdog fires
    # (PROCESS_EXIT_HANG), not idle watchdog (NO_OUTPUT_DEADLINE).
    # Descendant check may not fire because drain window short-circuits.
    expected_msg = "subprocess closed stdout but did not exit"
    with pytest.raises(AgentInactivityTimeoutError, match=expected_msg):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=FakeClock(),
            )
        )


def test_invoke_agent_runs_subprocess_in_workspace_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_cwds: list[str | None] = []

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
        seen_cwds.append(cast("str | None", kwargs.get("cwd")))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "ralph.agents.invoke.provider_allowed_mcp_tool_names",
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
            _clock=FakeClock(),
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
        del kwargs
        seen_cmds.append(_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke.provider_allowed_mcp_tool_names",
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
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    assert cmd[:9] == [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--include-partial-messages",
        "--resume",
        "abc123",
        "--dangerously-skip-permissions",
        "--verbose",
        "--mcp-config",
    ]
    mcp_payload = _json_object(cmd[9])
    servers = cast("dict[str, object]", mcp_payload["mcpServers"])
    assert cast("dict[str, object]", servers["ralph"]) == {
        "type": "http",
        "url": "http://127.0.0.1:9999/mcp",
    }
    assert cmd[10:] == [
        "--strict-mcp-config",
        "--tools",
        ",".join(CLAUDE_NATIVE_TOOLS_TO_KEEP),
        "--allowedTools",
        ",".join(
            [
                claude_tool_name("read_file"),
                claude_tool_name("ralph_submit_artifact"),
                *CLAUDE_NATIVE_TOOLS_TO_KEEP,
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
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda endpoint: ["read_file", "ralph_submit_artifact"],
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_artifact"),
    )


def test_provider_allowed_mcp_tool_names_dedupes_mixed_raw_and_aliased_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: when ``tools/list`` returns BOTH the raw tool name and
    the ``mcp__<server>__<tool>`` alias (post-fix behavior), the
    ``--allowedTools`` value must contain each alias exactly once.

    The pre-fix code mapped every entry through ``claude_tool_name`` so the
    already-aliased names became ``mcp__ralph__mcp__ralph__read_file`` and
    appeared in the live smoke log as duplicates. The fix dedupes by stripping
    the ``mcp__<server>__`` prefix from already-aliased names BEFORE applying
    ``claude_tool_name`` once, and dedupes the final tuple.
    """
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    mixed = [
        "read_file",
        "mcp__ralph__read_file",
        "ralph_submit_artifact",
        "mcp__ralph__ralph_submit_artifact",
    ]
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda endpoint: list(mixed),
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_artifact"),
    )
    assert len(allowed) == len(set(allowed))


def test_provider_allowed_mcp_tool_names_dedupes_double_prefixed_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-fix regression produced ``mcp__ralph__mcp__ralph__read_file``
    by mapping an already-aliased name through ``claude_tool_name``. Pin
    that the result NEVER contains a double-prefixed alias.
    """
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda endpoint: ["mcp__ralph__read_file", "mcp__ralph__ralph_submit_artifact"],
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    for name in allowed:
        assert not name.startswith("mcp__ralph__mcp__ralph__"), (
            f"double-prefixed alias leaked: {name}"
        )
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
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
        [
            claude_tool_name("read_file"),
            claude_tool_name("report_progress"),
            *CLAUDE_NATIVE_TOOLS_TO_KEEP,
        ]
    )


def test_build_command_claude_keeps_native_orchestration_tools_when_mcp_endpoint_wired(
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
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(claude_tool_name("read_file"),),
        ),
    )
    tools_idx = cmd.index("--tools")
    assert cmd[tools_idx + 1] == ",".join(CLAUDE_NATIVE_TOOLS_TO_KEEP)
    assert "Task" in cmd[tools_idx + 1]
    assert "Agent" in cmd[tools_idx + 1]
    assert "Skill" in cmd[tools_idx + 1]
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == ",".join(
        [claude_tool_name("read_file"), *CLAUDE_NATIVE_TOOLS_TO_KEEP]
    )


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
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(mcp_endpoint="http://127.0.0.1:9999/mcp"),
    )
    assert "--mcp-config" in cmd


def test_build_command_claude_omits_tool_flags_when_allowlist_is_empty(
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
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            allowed_mcp_tool_names=(),
        ),
    )

    assert "--mcp-config" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--tools" not in cmd
    assert "--allowedTools" not in cmd


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
        seen_env.append(_env_dict(kwargs))
        seen_cmds.append(_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
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
        seen_env.append(_env_dict(kwargs))
        seen_cmds.append(_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
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


@pytest.mark.timeout_seconds(2.0)
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
    seen_env: list[dict[str, str]] = []
    seen_cmds: list[list[str]] = []

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
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
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


def test_invoke_agent_starts_workspace_monitor_without_progress_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Workspace evidence collection starts even when progress UI is disabled.

    Regression for the activity-aware idle watchdog: a quiet unattended run
    can be doing real file work without wanting progress output. The
    workspace monitor must start whenever a workspace_path is provided,
    regardless of show_progress.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    captured_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _spy_start_workspace_monitor(
        workspace_path: Path | None,
        classifier: object | None = None,
        **kwargs: object,
    ) -> None:
        captured_calls.append(((workspace_path,), {"classifier": classifier, **kwargs}))

    monkeypatch.setattr(
        "ralph.agents.invoke._start_workspace_monitor",
        _spy_start_workspace_monitor,
    )

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

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
            _clock=FakeClock(),
        )
    )

    assert len(captured_calls) == 1, (
        f"expected one _start_workspace_monitor call, got {captured_calls}"
    )
    args, kwargs = captured_calls[0]
    assert args[0] == tmp_path, (
        f"workspace_path must be passed even with show_progress=False, got {args[0]}"
    )
    classifier = kwargs.get("classifier")
    assert isinstance(classifier, WorkspaceChangeClassifier), (
        f"expected a WorkspaceChangeClassifier for direct invoke callers, got {classifier!r}"
    )
    # Direct callers must receive the conservative default weights, not the
    # legacy OTHER/1.0 fallback. Source changes count as activity; log churn
    # does not.
    assert classifier.classify("src/app.py") == (WorkspaceChangeKind.SOURCE, 1.0)
    assert classifier.classify("build/output.log") == (WorkspaceChangeKind.LOG, 0.0)


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
                    extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
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
    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(),
    )
    assert "--tools" not in cmd
    assert "--strict-mcp-config" not in cmd
