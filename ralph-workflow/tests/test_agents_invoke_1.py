"""Tests for agent command construction."""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    ClaudeInteractiveExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    BuildCommandOptions,
    InvokeOptions,
    build_command,
    command_for_log,
    invoke_agent,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import (
    claude_tool_name,
)
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda _path: None)

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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda _path: None)

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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda _path: None)

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


@pytest.mark.timeout_seconds(2.0)
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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda _path: None)

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


def test_build_command_does_not_duplicate_print_flag_when_claude_cmd_already_uses_p() -> None:
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
