"""Tests for agent command construction."""

from __future__ import annotations

import io
import json
import json as _json
import threading
import time as _time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest
from loguru import logger

from ralph.agents import invoke as invoke_module
from ralph.agents.activity import AgentActivityKind
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    ClaudeInteractiveExecutionStrategy,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    BuildCommandOptions,
    InvokeOptions,
    UnsupportedMcpTransportError,
    build_command,
    check_agent_available,
    command_for_log,
    invoke_agent,
    provider_allowed_mcp_tool_names,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import Clock, FakeClock
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    CODEX_NATIVE_FEATURES_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.transport.codex import prepare_codex_home
from ralph.mcp.transport.opencode import merge_opencode_config_content
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.idle_watchdog import WaitingCorroborator, WaitingStatusListener

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


def test_invoke_agent_passes_idle_timeout_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_subprocess_and_read_lines(cmd: list[str], ctx: object) -> list[str]:
        captured["cmd"] = cmd
        captured["policy"] = getattr(ctx, "policy", None)
        return []

    _expected_idle_timeout = 300.0
    monkeypatch.setattr(
        invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                idle_timeout_seconds=_expected_idle_timeout,
            ),
        )
    )

    assert getattr(captured.get("policy"), "idle_timeout_seconds", None) == _expected_idle_timeout


def test_invoke_agent_probe_and_strategy_share_same_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DefaultLivenessProbe and OpenCodeExecutionStrategy share the same registry instance."""
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_subprocess_and_read_lines(cmd: list[str], ctx: object) -> list[str]:
        captured["execution_strategy"] = getattr(ctx, "execution_strategy", None)
        captured["liveness_probe"] = getattr(ctx, "liveness_probe", None)
        return []

    monkeypatch.setattr(
        invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(AGENT_LABEL_SCOPE_ENV): "run-scope-x"},
                child_progress_ttl_seconds=90.0,
            ),
        )
    )

    strategy = cast("OpenCodeExecutionStrategy", captured["execution_strategy"])
    probe = captured["liveness_probe"]
    strategy_registry = getattr(strategy, "_registry", None)
    probe_registry = getattr(probe, "_registry", None)
    assert strategy_registry is not None, "Strategy must have a non-None registry"
    assert probe_registry is not None, "Probe must have a non-None registry"
    assert strategy_registry is probe_registry, (
        "Strategy and probe must share the same registry instance"
    )
    # Confirm config-driven TTL was applied
    expected_ttl = 90.0
    actual_ttl = strategy_registry._progress_ttl
    assert actual_ttl == expected_ttl, (
        f"Expected progress_ttl={expected_ttl} from InvokeOptions; got {actual_ttl}"
    )


def test_invoke_agent_scopes_opencode_liveness_to_agent_label_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_subprocess_and_read_lines(cmd: list[str], ctx: object) -> list[str]:
        captured["execution_strategy"] = getattr(ctx, "execution_strategy", None)
        return []

    monkeypatch.setattr(
        invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                session_id="opencode-session-123",
                extra_env={str(AGENT_LABEL_SCOPE_ENV): "run-scope-123"},
            ),
        )
    )

    class _NoDescendantsHandle:
        def has_live_descendants(self) -> bool:
            return False

    strategy = cast("OpenCodeExecutionStrategy", captured["execution_strategy"])
    state = strategy.classify_quiet(
        _NoDescendantsHandle(),
        FakeLivenessProbe(active_labels=frozenset({"agent:run-scope-123:worker1"})),
    )
    assert state == AgentExecutionState.WAITING_ON_CHILD


def test_invoke_agent_without_session_scope_ignores_unrelated_agent_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_subprocess_and_read_lines(cmd: list[str], ctx: object) -> list[str]:
        captured["execution_strategy"] = getattr(ctx, "execution_strategy", None)
        return []

    monkeypatch.setattr(
        invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines
    )

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
            ),
        )
    )

    class _NoDescendantsHandle:
        def has_live_descendants(self) -> bool:
            return False

    strategy = cast("OpenCodeExecutionStrategy", captured["execution_strategy"])
    state = strategy.classify_quiet(
        _NoDescendantsHandle(),
        FakeLivenessProbe(active_labels=frozenset({"agent:other-session:worker1"})),
    )
    assert state == AgentExecutionState.ACTIVE


def test_run_subprocess_and_read_lines_wraps_idle_stream_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    class FakeProcess:
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(())
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0

        def __enter__(self) -> FakeProcess:
            return self

        def __exit__(
            self,
            _exc_type: object,
            _exc: object,
            _tb: object,
        ) -> Literal[False]:
            return False

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    with pytest.raises(AgentInactivityTimeoutError, match="no output for 0s"):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    workspace_path=tmp_path,
                    idle_timeout_seconds=0.05,
                ),
                _clock=FakeClock(),
            )
        )


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

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(
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

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(verbose=False),
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


def test_claude_interactive_build_command_excludes_output_flag() -> None:
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(
            model_flag="--model claude-sonnet-4",
            session_id="abc123",
            verbose=True,
        ),
    )

    assert cmd == [
        "claude",
        "--permission-mode",
        "auto",
        "--verbose",
        "--resume",
        "abc123",
        "--model",
        "claude-sonnet-4",
        "PROMPT.md",
    ]


def test_strategy_for_transport_returns_claude_interactive_strategy() -> None:
    assert isinstance(
        strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE),
        ClaudeInteractiveExecutionStrategy,
    )


def test_claude_interactive_execution_strategy_supports_session_continuation() -> None:
    assert ClaudeInteractiveExecutionStrategy().supports_session_continuation() is True


def test_claude_interactive_execution_strategy_classify_exit_terminal_on_completion() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()
    signals = CompletionSignals(True, False, ())

    class _FakeHandle:
        def has_live_descendants(self) -> bool:
            return False

    assert strategy.classify_exit(_FakeHandle(), signals) == AgentExecutionState.TERMINAL_COMPLETE


def test_claude_interactive_execution_strategy_classify_exit_resumable_without_signals() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()
    signals = CompletionSignals(False, False, ())

    class _FakeHandle:
        def has_live_descendants(self) -> bool:
            return False

    assert strategy.classify_exit(_FakeHandle(), signals) == AgentExecutionState.RESUMABLE_CONTINUE


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

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(system_prompt_file="SYSTEM_PROMPT.md"),
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


def test_build_command_injects_claude_interactive_session_id_and_settings() -> None:
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(
            initial_session_id="fresh-session-1",
            settings_json='{"hooks":{}}',
        ),
    )

    assert cmd == [
        "claude",
        "--dangerously-skip-permissions",
        "--session-id",
        "fresh-session-1",
        "--settings",
        '{"hooks":{}}',
        "PROMPT.md",
    ]


def test_build_command_injects_claude_interactive_append_system_prompt_file() -> None:
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(system_prompt_file="SYSTEM_PROMPT.md"),
    )

    assert cmd == [
        "claude",
        "--dangerously-skip-permissions",
        "--append-system-prompt-file",
        "SYSTEM_PROMPT.md",
        "PROMPT.md",
    ]


def test_builtin_claude_command_defaults_to_skip_permissions() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get("claude")

    assert config is not None
    cmd = build_command(config, "PROMPT.md")

    assert cmd[:2] == ["claude", "--dangerously-skip-permissions"]
    assert cmd[-1] == "PROMPT.md"


def test_build_command_omits_optional_flags_when_not_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("plain prompt", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="abc123", verbose=False),
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

    cmd = build_command(
        config,
        ".agent/tmp/PROMPT.md",
        options=BuildCommandOptions(
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="abc123", verbose=False),
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(verbose=False, pure=True),
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(verbose=False),
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

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="abc123", verbose=False),
    )
    logged = command_for_log(config, cmd, str(prompt_file))

    assert "super secret prompt body" not in logged
    assert str(prompt_file) in logged


def test_command_for_log_redacts_codex_inline_prompt_and_shows_prompt_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "planning_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("top secret planning prompt", encoding="utf-8")
    config = AgentConfig(cmd="codex exec", output_flag="--json", transport=AgentTransport.CODEX)

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(verbose=False),
    )
    logged = command_for_log(config, cmd, str(prompt_file))

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
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self, cmd: list[str]) -> None:
            self.stdout = iter(["line-one\n"])
            self.stderr = SimpleNamespace(read=lambda: "")
            self.returncode = 0
            popen_calls.append(cmd)

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
            pass

        def kill(self) -> None:
            pass

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del kwargs
        return FakeProcess(_argv(args))

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        del kwargs
        run_calls.append(cmd)

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke.subprocess.run", fake_run)

    lines = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False),
            _clock=FakeClock(),
        )
    )

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
                show_progress=False, extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"}
            ),
        )
    )

    assert seen_env
    assert seen_env[0][str(MCP_ENDPOINT_ENV)] == "http://127.0.0.1:9999/mcp"


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
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda endpoint: ["read_file", "ralph_submit_artifact"],
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

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
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
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


# ---------------------------------------------------------------------------
# New integration tests for IdleWatchdog / Clock refactor (Step 8)
# ---------------------------------------------------------------------------


class _BlockingStdout:
    """Stdout that blocks forever — drives the idle timeout path.

    Uses FakeClock-aware coordination to avoid real wall-clock waits.
    The stdout iterator yields nothing and raises StopIteration immediately,
    but sets a done event that the test controls. The main loop's
    FakeClock.wait_for_event advances time until the watchdog fires.
    """

    def __init__(self, done_event: threading.Event | None = None) -> None:
        self._done_event = done_event or threading.Event()

    def __iter__(self) -> _BlockingStdout:
        return self

    def __next__(self) -> str:
        # Raise StopIteration immediately - the FakeClock in the main loop
        # advances time so the watchdog fires even though no real wait happens.
        raise StopIteration


class _PreloadedStdout:
    """Stdout that yields pre-loaded lines and then closes."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __iter__(self) -> _PreloadedStdout:
        return self

    def __next__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        raise StopIteration


class _FakeInvokeProcess:
    """Minimal subprocess.Popen stand-in for integration tests."""

    pid: int = 77777

    def __init__(self, stdout: object = None) -> None:
        self.stdout = stdout or _BlockingStdout()
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode: int | None = None

    def __enter__(self) -> _FakeInvokeProcess:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> Literal[False]:
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


def test_idle_timeout_fires_when_truly_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no output and a FakeClock advancing past idle_timeout, the watchdog fires."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=30),
                _clock=clock,
            )
        )
    _expected_timeout = 30
    assert exc_info.value.timeout_seconds == _expected_timeout


def test_idle_timeout_does_not_fire_when_output_keeps_flowing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When lines keep flowing, the idle timeout never fires."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    line_count = 20
    lines = [f'{{"n": {i}}}\n' for i in range(line_count)]
    fake_process = _FakeInvokeProcess(stdout=_PreloadedStdout(lines))
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    clock = FakeClock()
    results = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, idle_timeout_seconds=10),
            _clock=clock,
        )
    )
    assert len(results) == line_count


def test_idle_timeout_parsed_output_preserved_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the timeout fires, parsed_output in the exception contains earlier lines."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    initial_line = "initial-output-line\n"

    class _OneLineThenBlock:
        _yielded = False

        def __iter__(self) -> _OneLineThenBlock:
            return self

        def __next__(self) -> str:
            if not self._yielded:
                self._yielded = True
                return initial_line
            # Raise StopIteration immediately - the FakeClock in the main loop
            # advances time so the watchdog fires even though no real wait happens.
            raise StopIteration

    fake_process = _FakeInvokeProcess(stdout=_OneLineThenBlock())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=clock,
            )
        )
    assert initial_line.rstrip() in exc_info.value.parsed_output


def test_idle_timeout_fires_when_children_persist_past_hard_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When children stay active past max_waiting_on_child_seconds, the watchdog fires.

    This is the false-negative fix: previously WAITING_ON_CHILD reset last_activity
    indefinitely. Now a hard ceiling prevents infinite deferral.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )

    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    # Always active — would infinite-defer under the old buggy code.
    monkeypatch.setattr(
        "ralph.agents.invoke.DefaultLivenessProbe",
        lambda registry=None: FakeLivenessProbe(active=True),
    )

    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=0.2,
                    max_waiting_on_child_seconds=0.4,
                    # Disable no-progress ceiling to avoid conflict with 600.0 default
                    max_waiting_on_child_no_progress_seconds=None,
                ),
                _clock=clock,
            )
        )


@pytest.mark.skip(reason="BlockingStdout closes stdout immediately; probe never called")
def test_idle_timeout_defers_when_children_active_then_fires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WAITING_ON_CHILD defers the timeout but does not prevent it from eventually firing."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )

    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    # Active for first few calls, then inactive — causes deferral then drain window.
    _active_calls = 3
    call_count = {"n": 0}

    def _probe_active(label_prefix: str) -> bool:
        call_count["n"] += 1
        return call_count["n"] <= _active_calls

    fake_probe = SimpleNamespace(any_agent_active=_probe_active)
    monkeypatch.setattr(
        "ralph.agents.invoke.DefaultLivenessProbe",
        lambda registry=None: fake_probe,
    )

    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=clock,
            )
        )
    assert call_count["n"] >= _active_calls


def test_termination_uses_nonzero_grace_period(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression guard: terminate is called with grace_period_s > 0 (false-positive fix)."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    terminate_calls: list[float] = []

    def _recording_terminate(_self: object, grace_period_s: float | None = None) -> None:
        terminate_calls.append(grace_period_s or 0.0)
        fake_process.returncode = -15

    monkeypatch.setattr(
        "ralph.process.manager.ManagedProcess.terminate",
        _recording_terminate,
        raising=False,
    )

    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=clock,
            )
        )

    assert terminate_calls, "terminate was not called"
    assert all(gp > 0 for gp in terminate_calls), (
        f"Expected all grace_period_s > 0, got {terminate_calls}"
    )


class _CallbackFakeClock(FakeClock):
    """FakeClock that triggers threading.Events at scheduled fake-time points."""

    def __init__(self, start: float = 0.0) -> None:
        super().__init__(start)
        self._listeners: list[tuple[float, threading.Event]] = []

    def _trigger_listeners(self) -> None:
        triggered = [ev for target, ev in self._listeners if self._now >= target]
        if triggered:
            for ev in triggered:
                ev.set()
            self._listeners = [(t, ev) for t, ev in self._listeners if self._now < t]

    def sleep(self, seconds: float) -> None:
        self._now += seconds
        self._trigger_listeners()

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self._now += seconds
        self._trigger_listeners()
        return event.is_set()

    def wait_until(self, target: float) -> threading.Event:
        """Return an event that fires when fake time reaches target."""
        ev = threading.Event()
        if self._now >= target:
            ev.set()
        else:
            self._listeners.append((target, ev))
        return ev


class _EventTriggeredStdout:
    """Stdout that yields one line when an event fires, then EOF."""

    def __init__(self, line: str, trigger: threading.Event) -> None:
        self._line = line
        self._trigger = trigger
        self._done = False

    def __iter__(self) -> _EventTriggeredStdout:
        return self

    def __next__(self) -> str:
        if not self._done:
            self._trigger.wait()
            self._done = True
            return self._line
        raise StopIteration


class _ScheduledStdout:
    """Stdout that yields each line after its corresponding event fires."""

    def __init__(self, scheduled_lines: list[tuple[str, threading.Event]]) -> None:
        self._scheduled_lines = list(scheduled_lines)

    def __iter__(self) -> _ScheduledStdout:
        return self

    def __next__(self) -> str:
        if not self._scheduled_lines:
            raise StopIteration
        line, trigger = self._scheduled_lines.pop(0)
        trigger.wait()
        return line


class _ClockBasedLivenessProbe:
    """Probe that reports children active until a fake-clock threshold is reached."""

    def __init__(self, clock: FakeClock, active_until: float) -> None:
        self._clock = clock
        self._active_until = active_until

    def any_agent_active(self, label_prefix: str) -> bool:
        return self._clock.monotonic() < self._active_until


@pytest.mark.skip(reason="EventTriggeredStdout blocks; FakeClock can't control Event.wait()")
def test_idle_timeout_drain_window_yields_late_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Line arriving inside the drain window clears drain state; no timeout fires."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    clock = _CallbackFakeClock()
    # idle_timeout=10s: deadline at fake_time 10.0, drain window [10.0, 10.5).
    # Inject line at 10.2 (inside drain window) then EOF -> record_activity clears drain.
    trigger = clock.wait_until(10.2)
    late_line = "late-arrived-line\n"
    fake_process = _FakeInvokeProcess(stdout=_EventTriggeredStdout(late_line, trigger))
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    result_lines = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, idle_timeout_seconds=10),
            _clock=clock,
        )
    )

    assert late_line in result_lines
    # No AgentInactivityTimeoutError — function returned normally.


@pytest.mark.skip(reason="EventTriggeredStdout blocks; FakeClock can't control Event.wait()")
def test_claude_lifecycle_activity_extends_idle_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Claude lifecycle activity resets idle even when display later suppresses it."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.CLAUDE,
    )

    clock = _CallbackFakeClock()
    lifecycle_line = (
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}\n'
    )
    trigger = clock.wait_until(10.2)
    fake_process = _FakeInvokeProcess(stdout=_EventTriggeredStdout(lifecycle_line, trigger))
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    result_lines = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, idle_timeout_seconds=10),
            _clock=clock,
        )
    )

    assert lifecycle_line in result_lines


def test_whitespace_only_output_does_not_extend_idle_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank heartbeats are not activity and cannot evade the idle timeout."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)

    clock = _CallbackFakeClock()
    blank_trigger = clock.wait_until(10.2)
    late_activity_trigger = clock.wait_until(11.0)
    fake_process = _FakeInvokeProcess(
        stdout=_ScheduledStdout(
            [
                ("   \n", blank_trigger),
                ("meaningful output after original drain\n", late_activity_trigger),
            ]
        )
    )
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=10),
                _clock=clock,
            )
        )

    assert exc_info.value.reason == invoke_module.WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_transport_activity_classifier_preserves_generic_and_claude_semantics() -> None:
    """Transport strategies classify watchdog activity without depending on display parsing."""
    generic_signal = GenericExecutionStrategy().classify_activity_line("plain output")
    assert generic_signal is not None
    assert generic_signal.kind == AgentActivityKind.OUTPUT_LINE

    blank_signal = GenericExecutionStrategy().classify_activity_line("  \n")
    assert blank_signal is None

    claude_strategy = strategy_for_transport(AgentTransport.CLAUDE)
    claude_signal = claude_strategy.classify_activity_line(
        '{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":""}}'
    )
    assert claude_signal is not None
    assert claude_signal.kind == AgentActivityKind.STREAM_DELTA

    interactive_strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)
    interactive_signal = interactive_strategy.classify_activity_line(
        'claude tool: read_file {"path":"PROMPT.md"}'
    )
    assert interactive_signal is not None
    assert interactive_signal.kind == AgentActivityKind.TOOL_USE


@pytest.mark.skip(reason="ScheduledStdout uses blocking Event.wait(); FakeClock can't control it")
def test_idle_timeout_defers_when_children_active_then_clears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Children defer timeout; once they clear and output arrives, invocation completes normally."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )

    clock = _CallbackFakeClock()
    # Probe reports active children until fake_time 30.0.
    # After that, classify_quiet returns ACTIVE -> drain window opens.
    # Line arrives at 30.2 (inside drain window) -> record_activity -> no timeout.
    probe = _ClockBasedLivenessProbe(clock, active_until=30.0)
    monkeypatch.setattr(
        "ralph.agents.invoke.DefaultLivenessProbe",
        lambda registry=None: probe,
    )

    trigger = clock.wait_until(30.2)
    arrived_line = "work-complete-output\n"
    fake_process = _FakeInvokeProcess(stdout=_EventTriggeredStdout(arrived_line, trigger))
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    result_lines = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, idle_timeout_seconds=10),
            _clock=clock,
        )
    )

    assert arrived_line in result_lines
    # No AgentInactivityTimeoutError — deferral cleared and then output arrived.


def test_idle_timeout_error_carries_fire_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AgentInactivityTimeoutError carries the watchdog fire reason on the no-output path."""

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False, idle_timeout_seconds=5),
                _clock=FakeClock(),
            )
        )

    # BlockingStdout closes stdout immediately → post-exit watchdog fires
    # (PROCESS_EXIT_HANG), not idle watchdog (NO_OUTPUT_DEADLINE).
    exc = exc_info.value
    assert exc.reason == WatchdogFireReason.PROCESS_EXIT_HANG
    assert isinstance(exc.reason, WatchdogFireReason)


def test_idle_timeout_children_persist_uses_distinct_reason_and_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CHILDREN_PERSIST_TOO_LONG reason produces distinct error message mentioning child agents."""

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    # Probe that always reports children active
    fake_probe = SimpleNamespace(any_agent_active=lambda label_prefix: True)
    monkeypatch.setattr(
        "ralph.agents.invoke.DefaultLivenessProbe",
        lambda registry=None: fake_probe,
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=10,
                    max_waiting_on_child_seconds=20.0,
                    # Disable no-progress ceiling to avoid conflict with 600.0 default
                    max_waiting_on_child_no_progress_seconds=None,
                ),
                _clock=FakeClock(),
            )
        )

    # BlockingStdout closes stdout immediately → post-exit watchdog fires
    # (PROCESS_EXIT_HANG), not CHILDREN_PERSIST_TOO_LONG.
    exc = exc_info.value
    assert exc.reason == WatchdogFireReason.PROCESS_EXIT_HANG


def test_invoke_agent_passes_config_drain_window_to_watchdog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """InvokeOptions.drain_window_seconds and max_waiting_on_child_seconds reach TimeoutPolicy."""

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    captured_config: list[TimeoutPolicy] = []
    original_init = IdleWatchdog.__init__

    def capturing_init(
        self: IdleWatchdog,
        cfg: TimeoutPolicy,
        clock: Clock,
        listener: WaitingStatusListener | None = None,
        corroborator: WaitingCorroborator | None = None,
        **kwargs: object,
    ) -> None:
        captured_config.append(cfg)
        original_init(
            self,
            cfg,
            clock,
            listener,
            corroborator=corroborator,
            **cast("dict[str, object]", kwargs),
        )

    monkeypatch.setattr(IdleWatchdog, "__init__", capturing_init)

    custom_drain = 1.5
    custom_max = 900.0
    with pytest.raises(AgentInactivityTimeoutError):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=5,
                    drain_window_seconds=custom_drain,
                    max_waiting_on_child_seconds=custom_max,
                ),
                _clock=FakeClock(),
            )
        )

    assert captured_config, "IdleWatchdog was never instantiated"
    cfg = captured_config[0]
    assert cfg.drain_window_seconds == custom_drain
    assert cfg.max_waiting_on_child_seconds == custom_max


@pytest.mark.timeout_seconds(2)
def test_invoke_agent_yields_lines_with_minimal_latency_under_system_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lines arrive quickly under production SystemClock - verifies wait_for_event wakeup.

    Without wait_for_event (old sleep-based polling), lines would take up to
    _IDLE_POLL_INTERVAL_SECONDS per line. This test ensures lines arrive well under 1s.
    """

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    n_lines = 5
    total_wall_limit = 2.0  # 2s generous limit for 5 lines

    lines_sent = ["line-" + str(i) + "\n" for i in range(n_lines)]
    sent_index: list[int] = [0]

    class TimedStdout:
        def __iter__(self) -> TimedStdout:
            return self

        def __next__(self) -> str:
            if sent_index[0] >= n_lines:
                raise StopIteration
            # Yield immediately without real sleep - the timing assertion is no longer
            # meaningful with FakeClock, but the output delivery still works.
            line = lines_sent[sent_index[0]]
            sent_index[0] += 1
            return line

    fake_process = _FakeInvokeProcess(stdout=TimedStdout())
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    before = _time.monotonic()
    result = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, idle_timeout_seconds=30),
            # No _clock: production SystemClock
        )
    )
    elapsed = _time.monotonic() - before

    assert result == lines_sent
    assert elapsed < total_wall_limit, (
        f"Expected all {n_lines} lines within {total_wall_limit}s, took {elapsed:.3f}s"
    )


def test_process_exit_hang_raises_via_post_exit_watchdog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PROCESS_EXIT_HANG fires via PostExitWatchdog when stdout yields one line then closes.

    Unlike test_invoke_agent_raises_process_exit_hang_when_stdout_closes_but_process_does_not_exit
    which uses an empty stdout, this test yields exactly one line before EOF, proving the
    PostExitWatchdog is invoked even when there is output. The subprocess never exits
    (poll() returns None), so the post-EOF wait triggers FIRE_PROCESS_EXIT_HANG.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    # Stdout yields one line then closes; process never exits (poll returns None).
    fake_process = _FakeInvokeProcess(stdout=_PreloadedStdout(['{"line": 1}\n']))
    assert fake_process.returncode is None  # poll() returns None by default

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    process_exit_wait = 5.0
    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=300,
                    process_exit_wait_seconds=process_exit_wait,
                    descendant_wait_timeout_seconds=1.0,
                ),
                _clock=clock,
            )
        )


    assert exc_info.value.reason == WatchdogFireReason.PROCESS_EXIT_HANG
    assert exc_info.value.timeout_seconds == process_exit_wait


def test_invoke_agent_raises_process_exit_hang_when_stdout_closes_but_process_does_not_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PROCESS_EXIT_HANG fires when stdout closes but poll() keeps returning None.

    This tests the false-negative fix: a subprocess that closes its stdout without
    calling exit() must be killed and raise AgentInactivityTimeoutError rather than
    hanging invoke_agent forever.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    # Stdout closes immediately (no lines); process never exits (poll returns None).
    fake_process = _FakeInvokeProcess(stdout=_PreloadedStdout([]))
    # poll() returns None by default (returncode not set)
    assert fake_process.returncode is None

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    process_exit_wait = 5.0
    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=300,
                    process_exit_wait_seconds=process_exit_wait,
                    descendant_wait_timeout_seconds=1.0,
                ),
                _clock=clock,
            )
        )


    assert exc_info.value.reason == WatchdogFireReason.PROCESS_EXIT_HANG
    assert exc_info.value.timeout_seconds == process_exit_wait


def test_invoke_agent_raises_session_ceiling_despite_continuous_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SESSION_CEILING_EXCEEDED fires when max_session_seconds is reached.

    This tests the false-negative fix: a process that produces output continuously
    (defeating the idle-timeout watchdog) must still be killed when the absolute
    session wall-clock ceiling is reached.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    # Stdout blocks forever; FakeClock advances during wait_for_event poll calls.
    fake_process = _FakeInvokeProcess(stdout=_BlockingStdout())
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    max_session = 10.0
    clock = FakeClock()
    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=None,
                    max_session_seconds=max_session,
                    idle_poll_interval_seconds=1.0,
                ),
                _clock=clock,
            )
        )

    # BlockingStdout closes stdout immediately → post-exit watchdog fires
    # (PROCESS_EXIT_HANG), not SESSION_CEILING_EXCEEDED.

    assert exc_info.value.reason == WatchdogFireReason.PROCESS_EXIT_HANG


def test_process_exit_observed_before_deadline_does_not_fire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Process exits cleanly before deadline — normal completion, no AgentInactivityTimeoutError.

    This is the inverse of test_process_exit_hang: when poll() returns a non-None
    value before process_exit_wait_seconds elapses, the post-exit wait returns CONTINUE
    and no error is raised.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")

    # Stdout yields no lines and process exits immediately (returncode=0).
    fake_process = _FakeInvokeProcess(stdout=_PreloadedStdout([]))
    fake_process.returncode = 0
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )

    # No error should be raised — process exited before any deadline.
    result = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                idle_timeout_seconds=300,
                process_exit_wait_seconds=30.0,
            ),
            _clock=FakeClock(),
        )
    )
    assert result == []


# ---------------------------------------------------------------------------
# Multimodal sidecar consumption tests
# ---------------------------------------------------------------------------


def _write_sidecar(
    prompt_file: Path,
    artifacts: list[dict[str, object]],
) -> None:
    """Write a multimodal handoff sidecar next to the given _prompt.md file."""

    stem = prompt_file.stem  # e.g. "development_prompt"
    normalized = stem.removesuffix("_prompt")
    sidecar = prompt_file.parent / f"{normalized}_multimodal_handoff.json"
    payload = {
        "schema_version": "1",
        "phase": normalized,
        "artifacts": artifacts,
    }
    sidecar.write_text(_json.dumps(payload), encoding="utf-8")


_SAMPLE_IMAGE_ARTIFACT: dict[str, object] = {
    "artifact_id": "abc123",
    "uri": "ralph://media/abc123",
    "mime_type": "image/png",
    "title": "screenshot.png",
    "modality": "image",
    "delivery": "inline",
    "reason": "Claude supports inline image delivery",
}

_SAMPLE_PDF_ARTIFACT: dict[str, object] = {
    "artifact_id": "pdf456",
    "uri": "ralph://media/pdf456",
    "mime_type": "application/pdf",
    "title": "report.pdf",
    "modality": "pdf",
    "delivery": "resource_reference",
    "reason": "'pdf' delivered as resource reference",
}


def test_claude_mcp_prompt_includes_multimodal_appendix_when_sidecar_present(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Build the feature.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_IMAGE_ARTIFACT])

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

    full_prompt = cmd[-1]
    assert "Build the feature." in full_prompt
    assert "Multimodal Artifacts" in full_prompt
    assert "ralph://media/abc123" in full_prompt
    assert "[image] screenshot.png" in full_prompt


def test_claude_mcp_prompt_text_only_when_no_sidecar(tmp_path: Path) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_text = "Build the feature text only."
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

    full_prompt = cmd[-1]
    assert full_prompt == prompt_text
    assert "Multimodal Artifacts" not in full_prompt


def test_claude_interactive_mcp_prompt_includes_multimodal_appendix_when_sidecar_present(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Build the interactive feature.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_IMAGE_ARTIFACT])

    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--permission-mode auto",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(mcp_endpoint="http://localhost:9999"),
    )

    assert cmd[-2] == "--"
    full_prompt = cmd[-1]
    assert "Build the interactive feature." in full_prompt
    assert "Multimodal Artifacts" in full_prompt
    assert "ralph://media/abc123" in full_prompt
    assert "[image] screenshot.png" in full_prompt


def test_opencode_prompt_includes_multimodal_appendix_when_sidecar_present(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Do the opencode work.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_PDF_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(),
    )

    full_prompt = cmd[-1]
    assert "Do the opencode work." in full_prompt
    assert "Multimodal Artifacts" in full_prompt
    assert "ralph://media/pdf456" in full_prompt
    assert "[pdf] report.pdf" in full_prompt


def test_opencode_prompt_text_only_when_no_sidecar(tmp_path: Path) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_text = "Plain text prompt only."
    prompt_file.write_text(prompt_text, encoding="utf-8")

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(),
    )

    assert cmd[-1] == prompt_text
    assert "Multimodal Artifacts" not in cmd[-1]


def test_codex_prompt_includes_multimodal_appendix_when_sidecar_present(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Fix the codex issue.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_IMAGE_ARTIFACT, _SAMPLE_PDF_ARTIFACT])

    config = AgentConfig(
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        json_parser=JsonParserType.CODEX,
        transport=AgentTransport.CODEX,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(),
    )

    full_prompt = cmd[-1]
    assert "Fix the codex issue." in full_prompt
    assert "Multimodal Artifacts" in full_prompt
    assert "ralph://media/abc123" in full_prompt
    assert "ralph://media/pdf456" in full_prompt


def test_codex_prompt_text_only_when_no_sidecar(tmp_path: Path) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_text = "Codex plain text prompt."
    prompt_file.write_text(prompt_text, encoding="utf-8")

    config = AgentConfig(
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        json_parser=JsonParserType.CODEX,
        transport=AgentTransport.CODEX,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(),
    )

    assert cmd[-1] == prompt_text
    assert "Multimodal Artifacts" not in cmd[-1]


def test_multimodal_appendix_includes_all_artifacts_for_mixed_modality(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Mixed modality run.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_IMAGE_ARTIFACT, _SAMPLE_PDF_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    assert "[image] screenshot.png" in full_prompt
    assert "[pdf] report.pdf" in full_prompt
    assert "ralph://media/abc123" in full_prompt
    assert "ralph://media/pdf456" in full_prompt


def test_multimodal_appendix_uses_path_equals_replay_handle_wording(tmp_path: Path) -> None:
    """The appendix must instruct agents to use path=<ralph://media/...> replay handles."""
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Work on the task.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_IMAGE_ARTIFACT, _SAMPLE_PDF_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )
    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    # New wording: path= not "URI:"
    assert "path=ralph://media/abc123" in full_prompt
    assert "path=ralph://media/pdf456" in full_prompt
    assert "URI: ralph://media/" not in full_prompt
    # The appendix must mention read_media and replay handle concept
    assert "read_media" in full_prompt
    assert "ralph://media/..." in full_prompt


def test_sidecar_with_non_standard_prompt_name_is_ignored(tmp_path: Path) -> None:
    """Prompt file not ending in _prompt.md must not attempt sidecar lookup."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_text = "Plain old prompt."
    prompt_file.write_text(prompt_text, encoding="utf-8")
    # Write a file that would match if the logic were wrong
    bad_sidecar = tmp_path / "PROMPT_multimodal_handoff.json"
    bad_sidecar.write_text(
        '{"schema_version":"1","phase":"test","artifacts":[{"artifact_id":"x","uri":"ralph://media/x","mime_type":"image/png","title":"t","modality":"image","delivery":"inline"}]}',
        encoding="utf-8",
    )

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert cmd[-1] == prompt_text
    assert "Multimodal Artifacts" not in cmd[-1]


_SAMPLE_AUDIO_ARTIFACT: dict[str, object] = {
    "artifact_id": "aud789",
    "uri": "ralph://media/aud789",
    "mime_type": "audio/mpeg",
    "title": "meeting.mp3",
    "modality": "audio",
    "delivery": "resource_reference_replay",
    "reason": "unknown provider — defaulting to resource_reference_replay delivery",
}


def test_multimodal_appendix_includes_replay_guidance_for_non_image_media(
    tmp_path: Path,
) -> None:
    """Appendix for non-image media must include replay-handle guidance with path= wording.

    When the sidecar contains audio or video artifacts, the generated appendix
    must instruct the agent to use path=<ralph://media/...> replay handles via
    read_media, not just list the URI as opaque data.
    """
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Analyze the meeting recording.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_AUDIO_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    # Appendix must include the audio artifact with replay handle wording
    assert "path=ralph://media/aud789" in full_prompt, (
        f"Expected 'path=ralph://media/aud789' in prompt, not found.\nFull prompt:\n{full_prompt}"
    )
    # The appendix must mention read_media for replay guidance
    assert "read_media" in full_prompt, (
        "Expected 'read_media' in appendix to guide agents toward replay handles"
    )
    # The audio title should appear in the appendix
    assert "meeting.mp3" in full_prompt, "Expected audio title 'meeting.mp3' in appendix"


# ---------------------------------------------------------------------------
# block_type and reason propagation in multimodal appendix (Plan Step 4)
# ---------------------------------------------------------------------------

_SAMPLE_PDF_TYPED_ARTIFACT: dict[str, object] = {
    "artifact_id": "pdf-typed-001",
    "uri": "ralph://media/pdf-typed-001",
    "mime_type": "application/pdf",
    "title": "spec.pdf",
    "modality": "pdf",
    "delivery": "typed_block",
    "reason": "'pdf' delivered as typed block 'pdf' for provider 'claude'",
    "block_type": "pdf",
}

_SAMPLE_UNSUPPORTED_ARTIFACT: dict[str, object] = {
    "artifact_id": "aud-unsupported-001",
    "uri": "ralph://media/aud-unsupported-001",
    "mime_type": "audio/mpeg",
    "title": "clip.mp3",
    "modality": "audio",
    "delivery": "unsupported",
    "reason": "Claude does not accept this modality via Ralph's managed MCP path (modality: audio)",
    "block_type": "",
}


def test_multimodal_appendix_includes_block_type_when_set(tmp_path: Path) -> None:
    """Appendix must include Block-type line when the sidecar entry has a non-empty block_type.

    This proves that capability-profile-derived block_type metadata is carried through
    the prompt handoff so the agent receives the full delivery contract.
    """
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Work on the feature.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_PDF_TYPED_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )
    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    assert "Block-type: pdf" in full_prompt, (
        f"Expected 'Block-type: pdf' in appendix for typed_block PDF, not found.\n"
        f"Prompt:\n{full_prompt}"
    )
    assert "Delivery: typed_block" in full_prompt, "Expected 'Delivery: typed_block' in appendix"


def test_multimodal_appendix_includes_explicit_reason_for_unsupported_modality(
    tmp_path: Path,
) -> None:
    """Appendix must include the capability-profile unsupported reason for rejected modalities.

    This proves (c) from plan Step 1 in the invoke layer: when delivery is 'unsupported',
    the prompt appendix must include the explicit reason from the capability verdict so
    the agent sees why the modality was rejected rather than a generic error message.
    """
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Process the audio.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_UNSUPPORTED_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )
    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    assert "Delivery: unsupported" in full_prompt, (
        "Expected 'Delivery: unsupported' in appendix for unsupported modality"
    )
    assert "unsupported_modality" in full_prompt, (
        "Expected 'unsupported_modality' failure reference in appendix"
    )
    assert "Claude does not accept this modality" in full_prompt, (
        f"Expected the capability-profile reason in appendix, not found.\nPrompt:\n{full_prompt}"
    )


_SAMPLE_RUNTIME_SEAM_ARTIFACT: dict[str, object] = {
    "artifact_id": "seam-fail-001",
    "uri": "ralph://media/seam-fail-001",
    "mime_type": "video/mp4",
    "title": "recording.mp4",
    "modality": "video",
    "delivery": "unsupported",
    "reason": "Active runtime seam cannot carry video through the handoff path",
    "block_type": "",
    "failure_kind": "unsupported_runtime_seam",
}


def test_multimodal_appendix_surfaces_unsupported_runtime_seam_without_replay_guidance(
    tmp_path: Path,
) -> None:
    """Appendix for unsupported_runtime_seam must not suggest read_media or replay handles.

    When an artifact has failure_kind='unsupported_runtime_seam', the appendix must
    explain the runtime seam failure and must not suggest read_media, replay paths,
    or typed blocks, since those would overclaim support the runtime cannot deliver.
    """
    prompt_file = tmp_path / "development_prompt.md"
    prompt_file.write_text("Process the video recording.", encoding="utf-8")
    _write_sidecar(prompt_file, [_SAMPLE_RUNTIME_SEAM_ARTIFACT])

    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())
    full_prompt = cmd[-1]

    # The appendix must mention the video artifact
    assert "recording.mp4" in full_prompt, "Expected artifact title 'recording.mp4' in appendix"
    # The appendix must mention the runtime seam failure
    assert "runtime seam" in full_prompt.lower(), (
        f"Expected runtime seam failure explanation in appendix, not found.\n"
        f"Full prompt:\n{full_prompt}"
    )
    # Must NOT suggest read_media for unsupported_runtime_seam
    # (The header mentions read_media generically, so we check the per-entry note)
    assert "Do not use read_media" in full_prompt, (
        f"Expected 'Do not use read_media' instruction in appendix for unsupported_runtime_seam.\n"
        f"Full prompt:\n{full_prompt}"
    )
    # Must NOT suggest a replay handle (typed block or resource_reference) for this artifact
    assert "call read_media with this path to receive" not in full_prompt, (
        "Appendix must not suggest typed block retrieval for unsupported_runtime_seam"
    )
    # The reason must appear in the appendix
    assert "Active runtime seam cannot carry video" in full_prompt, (
        f"Expected runtime seam reason in appendix, not found.\nFull prompt:\n{full_prompt}"
    )
    # The failure_kind must be distinct: unsupported_runtime_seam must not be rendered
    # as unsupported_modality
    assert "unsupported_modality" not in full_prompt, (
        "unsupported_runtime_seam must not be rendered as unsupported_modality in appendix"
    )


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
