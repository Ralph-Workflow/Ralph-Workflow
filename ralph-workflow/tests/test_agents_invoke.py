"""Consolidated tests from test_agents_invoke_*.py.

This module merges the following previously split test modules into a single
file to reduce per-shard collection cost. The original class names are
preserved so external references (test::TestX) still resolve.

Source files:
  - test_agents_invoke_1.py
  - test_agents_invoke_2.py
  - test_agents_invoke_3.py
  - test_agents_invoke_4.py
  - test_agents_invoke_5.py
"""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Literal,
)

import pytest
from loguru import logger

import ralph.agents.invoke as invoke_module
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    ClaudeInteractiveExecutionStrategy,
    strategy_for_transport,
)
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
    resolve_invocation_runtime,
)
from ralph.agents.invoke._options import build_invoke_options_from_config
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    WorkspaceChangeKind,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import (
    AgentTransport,
    JsonParserType,
)
from ralph.config.loader import load_config
from ralph.config.models import (
    AgentConfig,
    GeneralConfig,
    UnifiedConfig,
)
from ralph.mcp.protocol.env import (
    AGENT_LABEL_SCOPE_ENV,
    MCP_ENDPOINT_ENV,
)
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    CLAUDE_NATIVE_TOOLS_TO_KEEP,
    CODEX_NATIVE_FEATURE_OVERRIDES,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_KEEP,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.transport.codex import prepare_codex_home
from ralph.mcp.transport.opencode import merge_opencode_config_content
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
)
from ralph.process.liveness import FakeLivenessProbe
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS
from ralph.workspace.scope import WorkspaceScope
from tests._support.typed_accessors import (
    must_mapping,
    must_str,
    must_str_dict,
    must_str_list,
)

_EXPECTED_DESCENDANT_LIVENESS_CHECKS = 2




# === Helper for test_agents_invoke_1.py ===
@pytest.fixture(autouse=True)
def _agents_invoke_1_disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)


# === Helper for test_agents_invoke_1.py ===
def _agents_invoke_1_json_object(raw: str) -> dict[str, object]:
    return must_mapping(json.loads(raw))


# === Helper for test_agents_invoke_1.py ===
def _agents_invoke_1_toml_object(raw: str) -> dict[str, object]:
    return must_mapping(tomllib.loads(raw))


# === Helper for test_agents_invoke_1.py ===
def _agents_invoke_1_env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return must_str_dict(env_obj)


# === Helper for test_agents_invoke_1.py ===
def _agents_invoke_1_argv(args: tuple[object, ...]) -> list[str]:
    return list(args[0])


# === Helper for test_agents_invoke_2.py ===
@pytest.fixture(autouse=True)
def _agents_invoke_2_disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)


# === Helper for test_agents_invoke_2.py ===
def _agents_invoke_2_json_object(raw: str) -> dict[str, object]:
    return must_mapping(json.loads(raw))


# === Helper for test_agents_invoke_2.py ===
def _agents_invoke_2_toml_object(raw: str) -> dict[str, object]:
    return must_mapping(tomllib.loads(raw))


# === Helper for test_agents_invoke_2.py ===
def _agents_invoke_2_env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return must_str_dict(env_obj)


# === Helper for test_agents_invoke_2.py ===
def _agents_invoke_2_argv(args: tuple[object, ...]) -> list[str]:
    return list(args[0])


# === Helper for test_agents_invoke_3.py ===
@pytest.fixture(autouse=True)
def _agents_invoke_3_disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)


# === Helper for test_agents_invoke_3.py ===
def _agents_invoke_3_json_object(raw: str) -> dict[str, object]:
    return must_mapping(json.loads(raw))


# === Helper for test_agents_invoke_3.py ===
def _agents_invoke_3_toml_object(raw: str) -> dict[str, object]:
    return must_mapping(tomllib.loads(raw))


# === Helper for test_agents_invoke_3.py ===
def _agents_invoke_3_env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return must_str_dict(env_obj)


# === Helper for test_agents_invoke_3.py ===
def _agents_invoke_3_argv(args: tuple[object, ...]) -> list[str]:
    return list(args[0])


# === Helper for test_agents_invoke_4.py ===
@pytest.fixture(autouse=True)
def _agents_invoke_4_disable_workspace_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)


# === Helper for test_agents_invoke_4.py ===
def _agents_invoke_4_json_object(raw: str) -> dict[str, object]:
    return must_mapping(json.loads(raw))


# === Helper for test_agents_invoke_4.py ===
def _agents_invoke_4_toml_object(raw: str) -> dict[str, object]:
    return must_mapping(tomllib.loads(raw))


# === Helper for test_agents_invoke_4.py ===
def _agents_invoke_4_env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return must_str_dict(env_obj)


# === Helper for test_agents_invoke_4.py ===
def _agents_invoke_4_argv(args: tuple[object, ...]) -> list[str]:
    return list(args[0])


# === Helper for test_agents_invoke_5.py ===
def _agents_invoke_5_json_object(raw: str) -> dict[str, object]:
    return must_mapping(json.loads(raw))


# === Helper for test_agents_invoke_5.py ===
def _agents_invoke_5_toml_object(raw: str) -> dict[str, object]:
    return must_mapping(tomllib.loads(raw))


# === Helper for test_agents_invoke_5.py ===
def _agents_invoke_5_env_dict(kwargs: dict[str, object]) -> dict[str, str]:
    env_obj = kwargs.get("env")
    assert isinstance(env_obj, dict)
    return must_str_dict(env_obj)


# === Helper for test_agents_invoke_5.py ===
def _agents_invoke_5_argv(args: tuple[object, ...]) -> list[str]:
    return list(args[0])


# === Helper: _run_agy_transport_proxy_payload_check (from test_agents_invoke_4.py) ===
def _run_agy_transport_proxy_payload_check(
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    prompt_file: Path,
    seen_envs: dict[str, dict[str, str]],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    agy_transport_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    def fake_run_pty_agy(cmd: object, ctx: object, extras: object = None) -> object:
        del cmd, extras
        seen_envs["agy"] = must_str_dict(ctx.extra_env)
        yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    monkeypatch.setattr("ralph.agents.invoke.run_pty_and_read_lines", fake_run_pty_agy)
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")
    agy_config_dir = fake_home / ".gemini" / "antigravity-cli"
    agy_config_dir.mkdir(parents=True)
    (agy_config_dir / "mcp_config.json").write_text(
        json.dumps(
            {"mcpServers": {"upstream-agy-http": {"serverUrl": "http://upstream-agy:9876"}}}
        ),
        encoding="utf-8",
    )

    runtime = resolve_invocation_runtime(
        agy_transport_config,
        {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
        tmp_path,
    )
    assert runtime.agent_env is not None
    assert UPSTREAM_MCP_CONFIG_ENV in runtime.agent_env
    agy_upstream_payload = json.loads(runtime.agent_env[UPSTREAM_MCP_CONFIG_ENV])
    assert any(
        u["name"] == "upstream-agy-http" and u["transport"] == "http" for u in agy_upstream_payload
    ), (
        "AGY HTTP serverUrl upstream must appear in RALPH_UPSTREAM_MCP_CONFIG "
        "after normalization fix"
    )

    list(
        invoke_agent(
            agy_transport_config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
        )
    )
    assert "agy" in seen_envs


# === consolidated from test_agents_invoke_1.py ===
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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

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


# === consolidated from test_agents_invoke_1.py ===
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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

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

    strategy = captured["execution_strategy"]
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


# === consolidated from test_agents_invoke_1.py ===
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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

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

    strategy = captured["execution_strategy"]
    state = strategy.classify_quiet(
        _NoDescendantsHandle(),
        FakeLivenessProbe(active_labels=frozenset({"agent:run-scope-123:worker1"})),
    )
    assert state == AgentExecutionState.WAITING_ON_CHILD


# === consolidated from test_agents_invoke_1.py ===
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

    strategy = captured["execution_strategy"]
    state = strategy.classify_quiet(
        _NoDescendantsHandle(),
        FakeLivenessProbe(active_labels=frozenset({"agent:other-session:worker1"})),
    )
    assert state == AgentExecutionState.ACTIVE


# === consolidated from test_agents_invoke_1.py ===
def test_run_subprocess_and_read_lines_wraps_idle_stream_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(())
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

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


# === consolidated from test_agents_invoke_1.py ===
def test_build_command_includes_print_streaming_and_session_flags() -> None:
    """Official headless ``claude`` emits stream-json flags exactly once.

    The official Claude Code CLI requires ``--verbose`` when
    ``--output-format=stream-json`` is used; the builder must add it
    automatically and only once. Third-party wrappers such as ``ccs`` keep
    their original argv order and are covered separately.
    """
    config = AgentConfig(
        cmd="claude",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
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
        "--output-format=stream-json",
        "--verbose",
        "--print",
        "--include-partial-messages",
        "--resume",
        "abc123",
        "--dangerously-skip-permissions",
        "--model",
        "claude-sonnet-4",
        "PROMPT.md",
    ]


def test_build_command_ccs_wrapper_keeps_original_argv_order() -> None:
    """``ccs`` wrappers are not promoted to the official-claude flag order."""
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


# === consolidated from test_agents_invoke_1.py ===
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
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "auto",
        "PROMPT.md",
    ]


def test_build_command_keeps_quoted_cmd_path_containing_spaces_as_one_token() -> None:
    """A shell-quoted wrapper path with spaces stays a single argv token.

    Operators point ``[agents.claude].cmd`` at a wrapper binary whose path
    may contain spaces; the documented contract (matching the AGY / Cursor
    overrides and ``_agent_command_name``) is that the cmd string is parsed
    with shell quoting rules, so the quoted path survives as one token
    instead of being shredded into two nonexistent arguments.
    """
    config = AgentConfig(
        cmd="'/opt/my wrapper/claude-shim' claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        print_flag="--print",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )

    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(verbose=False),
    )

    assert cmd[:3] == ["/opt/my wrapper/claude-shim", "claude", "-p"]


# === consolidated from test_agents_invoke_1.py ===
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


# === consolidated from test_agents_invoke_1.py ===
def test_strategy_for_transport_returns_claude_interactive_strategy() -> None:
    assert isinstance(
        strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE),
        ClaudeInteractiveExecutionStrategy,
    )


# === consolidated from test_agents_invoke_1.py ===
def test_claude_interactive_execution_strategy_supports_session_continuation() -> None:
    assert ClaudeInteractiveExecutionStrategy().supports_session_continuation() is True


# === consolidated from test_agents_invoke_1.py ===
def test_claude_interactive_execution_strategy_classify_exit_terminal_on_completion() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()
    signals = CompletionSignals(True, False, (), completion_sentinel_present=True)

    class _FakeHandle:
        def has_live_descendants(self) -> bool:
            return False

    assert strategy.classify_exit(_FakeHandle(), signals) == AgentExecutionState.TERMINAL_COMPLETE


# === consolidated from test_agents_invoke_1.py ===
def test_claude_interactive_execution_strategy_classify_exit_resumable_without_signals() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()
    signals = CompletionSignals(False, False, ())

    class _FakeHandle:
        def has_live_descendants(self) -> bool:
            return False

    assert strategy.classify_exit(_FakeHandle(), signals) == AgentExecutionState.RESUMABLE_CONTINUE


# === consolidated from test_agents_invoke_1.py ===
def test_claude_interactive_execution_strategy_classify_quiet_ignores_os_descendants() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()

    class _FakeHandle:
        def has_live_descendants(self) -> bool:
            return True

    state = strategy.classify_quiet(_FakeHandle(), FakeLivenessProbe())

    assert state == AgentExecutionState.ACTIVE


# === consolidated from test_agents_invoke_1.py ===
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
        options=BuildCommandOptions(master_prompt_file="MASTER_PROMPT.md"),
    )

    assert cmd == [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--include-partial-messages",
        "--permission-mode",
        "auto",
        "--append-system-prompt-file",
        "MASTER_PROMPT.md",
        "PROMPT.md",
    ]


# === consolidated from test_agents_invoke_1.py ===
def test_build_command_injects_claude_interactive_session_id_and_settings() -> None:
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    # The session-resume flag is decided by the single-source-of-truth
    # helper. When `options.session_id` is set, the helper emits
    # `--resume <id>` (NOT `--session-id`); the pre-fix code emitted
    # `--session-id` for the interactive Claude path, which created a
    # new session tagged with the id instead of continuing the prior
    # session. After the fix, the only decision point is the helper.
    cmd = build_command(
        config,
        "PROMPT.md",
        options=BuildCommandOptions(
            session_id="resume-session-1",
            settings_json='{"hooks":{}}',
        ),
    )

    assert cmd == [
        "claude",
        "--dangerously-skip-permissions",
        "--resume",
        "resume-session-1",
        "--settings",
        '{"hooks":{}}',
        "PROMPT.md",
    ]


# === consolidated from test_agents_invoke_1.py ===
@pytest.mark.timeout_seconds(3)
@pytest.mark.subprocess_e2e
def test_invoke_agent_claude_interactive_default_settings_include_permission_request_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Builds and inspects the ``claude --resume`` command.

    On a loaded worker the mock setup + command build can take ~1.1 s, so a 3 s
    per-test cap is required to keep this from tripping the 1 s default and
    stalling the xdist scheduler.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    captured: dict[str, object] = {}

    def fake_run_pty_and_read_lines(
        cmd: list[str],
        ctx: object,
        extras: object = None,
    ) -> list[str]:
        del ctx, extras
        captured["cmd"] = cmd
        return ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]

    monkeypatch.setattr(invoke_module, "run_pty_and_read_lines", fake_run_pty_and_read_lines)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
        )
    )

    cmd = must_str_list(captured["cmd"])
    settings_index = cmd.index("--settings")
    settings = _agents_invoke_1_json_object(cmd[settings_index + 1])
    hooks = must_mapping(settings["hooks"])

    assert settings["skipDangerousModePermissionPrompt"] is True
    assert "Stop" in hooks
    assert "PermissionRequest" in hooks
    permission_entries = hooks["PermissionRequest"]
    permission_entry = must_mapping(permission_entries[0])
    permission_hooks = permission_entry["hooks"]
    permission_hook = must_mapping(permission_hooks[0])
    assert permission_hook["type"] == "command"
    assert "PermissionRequest" in must_str(permission_hook["command"])
    assert "allow" in must_str(permission_hook["command"])


# === consolidated from test_agents_invoke_1.py ===
@pytest.mark.timeout_seconds(3)
@pytest.mark.subprocess_e2e
def test_invoke_agent_claude_interactive_passes_permission_prompt_listener_to_pty_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercises the full ``invoke_agent`` path through a mock PTY runtime.

    On a loaded worker the mock PTY setup + invoke pipeline can take ~1.1 s,
    so a 3 s per-test cap is required to keep this from tripping the 1 s
    default and stalling the xdist scheduler.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    captured_listener: list[object | None] = []

    def fake_run_pty_and_read_lines(
        cmd: list[str],
        ctx: object,
        extras: object = None,
    ) -> list[str]:
        del cmd, ctx
        captured_listener.append(getattr(extras, "permission_prompt_listener", None))
        return ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]

    monkeypatch.setattr(invoke_module, "run_pty_and_read_lines", fake_run_pty_and_read_lines)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                permission_prompt_listener=lambda _message: None,
            ),
        )
    )

    assert len(captured_listener) == 1
    assert callable(captured_listener[0])


# === consolidated from test_agents_invoke_1.py ===
@pytest.mark.timeout_seconds(3)
@pytest.mark.subprocess_e2e
def test_invoke_agent_claude_interactive_merges_custom_settings_with_required_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Builds and inspects the merged ``claude`` settings payload.

    On a loaded worker the deep-copy + JSON-encoded settings build can take
    ~1.1 s, so a 3 s per-test cap is required to keep this from tripping the
    1 s default and stalling the xdist scheduler.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    captured: dict[str, object] = {}

    def fake_run_pty_and_read_lines(
        cmd: list[str],
        ctx: object,
        extras: object = None,
    ) -> list[str]:
        del ctx, extras
        captured["cmd"] = cmd
        return ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]

    monkeypatch.setattr(invoke_module, "run_pty_and_read_lines", fake_run_pty_and_read_lines)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                settings_json='{"tui":"fullscreen","hooks":{"Notification":[]}}',
            ),
        )
    )

    cmd = must_str_list(captured["cmd"])
    settings_index = cmd.index("--settings")
    settings = _agents_invoke_1_json_object(cmd[settings_index + 1])
    hooks = must_mapping(settings["hooks"])

    assert settings["tui"] == "fullscreen"
    assert settings["skipDangerousModePermissionPrompt"] is True
    assert "Notification" in hooks
    assert "Stop" in hooks
    assert "PermissionRequest" in hooks


# === consolidated from test_agents_invoke_1.py ===
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
        options=BuildCommandOptions(master_prompt_file="MASTER_PROMPT.md"),
    )

    assert cmd == [
        "claude",
        "--dangerously-skip-permissions",
        "--append-system-prompt-file",
        "MASTER_PROMPT.md",
        "PROMPT.md",
    ]


# === consolidated from test_agents_invoke_1.py ===
def test_builtin_claude_command_defaults_to_skip_permissions() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get("claude")

    assert config is not None
    cmd = build_command(config, "PROMPT.md")

    assert cmd[:2] == ["claude", "--dangerously-skip-permissions"]
    assert cmd[-1] == "PROMPT.md"


# === consolidated from test_agents_invoke_1.py ===
def test_build_command_omits_optional_flags_when_not_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("plain prompt", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="abc123", verbose=False),
    )

    # ``--auto`` approves permissions that are not explicitly denied: an
    # unattended run has nobody to answer a prompt, and OpenCode otherwise
    # auto-REJECTS anything it cannot match.
    assert cmd == ["opencode", "run", "--format", "json", "--auto", "plain prompt"]


# === consolidated from test_agents_invoke_1.py ===
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
    config_payload = _agents_invoke_1_json_object(cmd[mcp_index + 1])
    servers = must_mapping(config_payload["mcpServers"])
    assert must_mapping(servers["ralph"]) == {
        "type": "http",
        "url": "http://127.0.0.1:9999/mcp",
    }
    allowed_index = cmd.index("--allowedTools")
    assert cmd[allowed_index + 1] == ",".join(
        [
            claude_tool_name("read_file"),
            claude_tool_name("report_progress"),
            *CLAUDE_NATIVE_TOOLS_TO_KEEP,
        ]
    )
    assert cmd[-2:] == ["--", prompt_content]


# === consolidated from test_agents_invoke_1.py ===
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


# === consolidated from test_agents_invoke_1.py ===
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
    assert cmd[allowed_index + 1] == ",".join(
        [claude_tool_name("read_file"), *CLAUDE_NATIVE_TOOLS_TO_KEEP]
    )
    assert cmd[-2:] == ["--", prompt_content]


# === consolidated from test_agents_invoke_1.py ===
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
        "--auto",
        "-m",
        "minimax/MiniMax-M2.7-highspeed",
        "say hello",
    ]


# === consolidated from test_agents_invoke_1.py ===
def test_opencode_regression_no_pure_flag_and_no_output_flag_in_argv(
    tmp_path: Path,
) -> None:
    """Two flags that must never reach an ``opencode run`` command line.

    This test previously asserted ``--pure`` WAS emitted, which encoded a
    bug: ``--pure`` runs opencode without external plugins, so an operator
    whose model provider comes from a plugin had every run die on an
    unresolvable provider. The flag and its ``pure`` option field are gone.

    It also pins the second half: opencode 1.18.25 has no output-format flag
    beyond ``--format json``, so an operator ``[agents.opencode]
    output_flag`` is dropped -- now by an explicit
    ``honors_output_flag=False`` declaration rather than by an argv-name
    sniff that happened to swallow it.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("say hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(verbose=False),
    )

    assert cmd == [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        "say hello",
    ]


# === consolidated from test_agents_invoke_1.py ===
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


# === consolidated from test_agents_invoke_1.py ===
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


# === consolidated from test_agents_invoke_1.py ===
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


# === consolidated from test_agents_invoke_1.py ===
def test_invoke_agent_does_not_reexecute_command_after_stream_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self, cmd: list[str]) -> None:
            self.stdout = iter(["line-one\n"])
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        return FakeProcess(_agents_invoke_1_argv(args))

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


# === consolidated from test_agents_invoke_1.py ===
def test_invoke_agent_passes_extra_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        env = _agents_invoke_1_env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke.provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_md_artifact"),
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


# === consolidated from test_agents_invoke_1.py ===
def test_build_command_merges_existing_mcp_servers_when_unsafe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BuildCommandOptions(unsafe_mode=True) merges workspace .mcp.json into the Claude command."""
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE,
        output_flag="--output-format=stream-json",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing-server": {
                        "type": "http",
                        "url": "http://existing.example/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    prompt_file = workspace / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            workspace_path=workspace,
            unsafe_mode=True,
        ),
    )

    assert "--mcp-config" in cmd
    mcp_config_idx = cmd.index("--mcp-config")
    mcp_config_json = cmd[mcp_config_idx + 1]
    parsed = json.loads(mcp_config_json)
    servers = parsed["mcpServers"]
    assert "existing-server" in servers
    assert "ralph" in servers


# === consolidated from test_agents_invoke_1.py ===
def test_build_command_ralph_only_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BuildCommandOptions(unsafe_mode=False, default) returns a Ralph-only --mcp-config JSON."""
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE,
        output_flag="--output-format=stream-json",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing-server": {
                        "type": "http",
                        "url": "http://existing.example/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    prompt_file = workspace / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            workspace_path=workspace,
        ),
    )

    assert "--mcp-config" in cmd
    mcp_config_idx = cmd.index("--mcp-config")
    parsed = json.loads(cmd[mcp_config_idx + 1])
    assert list(parsed["mcpServers"].keys()) == ["ralph"]


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def __init__(self) -> None:
            self.stdout = BlockingStdout()
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
    process_teardown = _RecordingProcessTeardown()

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
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=5,
                    process_teardown=process_teardown,
                ),
                _clock=FakeClock(),
            )
        )
    assert process_teardown.calls == (fake_process.pid,)


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def __init__(self) -> None:
            self.stdout = BlockingStdout()
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
    process_teardown = _RecordingProcessTeardown()
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
                options=InvokeOptions(
                    show_progress=False,
                    idle_timeout_seconds=5,
                    process_teardown=process_teardown,
                ),
                _clock=FakeClock(),
            )
        )
    assert process_teardown.calls == (fake_process.pid,)


# === consolidated from test_agents_invoke_2.py ===
def test_invoke_agent_runs_subprocess_in_workspace_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream")
    seen_cwds: list[str | None] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_cwds.append(kwargs.get("cwd"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "ralph.agents.invoke.provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_md_artifact"),
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


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_cmds.append(_agents_invoke_2_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "ralph.agents.invoke.provider_allowed_mcp_tool_names",
        lambda config, endpoint: (
            claude_tool_name("read_file"),
            claude_tool_name("ralph_submit_md_artifact"),
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
        "--verbose",
        "--include-partial-messages",
        "--resume",
        "abc123",
        "--dangerously-skip-permissions",
        "--mcp-config",
    ]
    mcp_payload = _agents_invoke_2_json_object(cmd[9])
    servers = must_mapping(mcp_payload["mcpServers"])
    assert must_mapping(servers["ralph"]) == {
        "type": "http",
        "url": "http://127.0.0.1:9999/mcp",
    }
    # ``--strict-mcp-config`` is deliberately gone from this argv: it made
    # Ralph's config the ONLY MCP source Claude read, deleting every server
    # the operator installed in their own harness. Ralph adds its server; it
    # does not remove theirs. Its OWN --tools/--allowedTools gate stays.
    assert cmd[10:] == [
        "--tools",
        ",".join(CLAUDE_NATIVE_TOOLS_TO_KEEP),
        "--allowedTools",
        ",".join(
            [
                claude_tool_name("read_file"),
                claude_tool_name("ralph_submit_md_artifact"),
                *CLAUDE_NATIVE_TOOLS_TO_KEEP,
            ]
        ),
        "--model",
        "claude-sonnet-4",
        "--",
        "hello",
    ]


# === consolidated from test_agents_invoke_2.py ===
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
        lambda endpoint: ["read_file", "ralph_submit_md_artifact"],
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_md_artifact"),
    )


# === consolidated from test_agents_invoke_2.py ===
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
        "ralph_submit_md_artifact",
        "mcp__ralph__ralph_submit_md_artifact",
    ]
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda endpoint: list(mixed),
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_md_artifact"),
    )
    assert len(allowed) == len(set(allowed))


# === consolidated from test_agents_invoke_2.py ===
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
        lambda endpoint: [
            "mcp__ralph__read_file",
            "mcp__ralph__ralph_submit_md_artifact",
        ],
    )

    allowed = provider_allowed_mcp_tool_names(config, "http://127.0.0.1:9999/mcp")

    for name in allowed:
        assert not name.startswith("mcp__ralph__mcp__ralph__"), (
            f"double-prefixed alias leaked: {name}"
        )
    assert allowed == (
        claude_tool_name("read_file"),
        claude_tool_name("ralph_submit_md_artifact"),
    )


# === consolidated from test_agents_invoke_2.py ===
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


# === consolidated from test_agents_invoke_2.py ===
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


# === consolidated from test_agents_invoke_2.py ===
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


# === consolidated from test_agents_invoke_2.py ===
def test_build_command_claude_omits_tool_flags_when_allowlist_is_empty(
    tmp_path: Path,
) -> None:
    """``--strict-mcp-config`` is deliberately absent: Ralph adds, never removes.

    This used to assert the flag WAS emitted. Per ``claude --help`` it means
    "Only use MCP servers from --mcp-config, ignoring all other MCP
    configurations" -- it deleted every MCP server the operator installed in
    their own harness, including claude.ai account connectors that exist in
    no file and can never be proxied back. ``--mcp-config`` alone adds
    Ralph's server on top of the operator's own sources.
    """
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
    assert "--strict-mcp-config" not in cmd
    assert "--tools" not in cmd
    assert "--allowedTools" not in cmd


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_env.append(_agents_invoke_2_env_dict(kwargs))
        seen_cmds.append(_agents_invoke_2_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setenv("HOME", str(fake_home))

    # Claude's tool restriction now fails CLOSED: a `tools/list` that cannot
    # be reached aborts the launch instead of silently emitting
    # --strict-mcp-config with no --allowedTools. These tests assert on the
    # --mcp-config payload, not on discovery, so the endpoint is stubbed
    # rather than dialled (it never listened here in the first place).
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["ralph_submit_md_artifact"],
    )
    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                requires_completion_evidence=False,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _agents_invoke_2_json_object(cmd[mcp_index + 1])
    servers = must_mapping(config_payload["mcpServers"])
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


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_env.append(_agents_invoke_2_env_dict(kwargs))
        seen_cmds.append(_agents_invoke_2_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setenv("HOME", str(fake_home))

    # Claude's tool restriction now fails CLOSED: a `tools/list` that cannot
    # be reached aborts the launch instead of silently emitting
    # --strict-mcp-config with no --allowedTools. These tests assert on the
    # --mcp-config payload, not on discovery, so the endpoint is stubbed
    # rather than dialled (it never listened here in the first place).
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["ralph_submit_md_artifact"],
    )
    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                requires_completion_evidence=False,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _agents_invoke_2_json_object(cmd[mcp_index + 1])
    servers = must_mapping(config_payload["mcpServers"])
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


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_env.append(_agents_invoke_2_env_dict(kwargs))
        seen_cmds.append(_agents_invoke_2_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setenv("HOME", str(fake_home))

    # Claude's tool restriction now fails CLOSED: a `tools/list` that cannot
    # be reached aborts the launch instead of silently emitting
    # --strict-mcp-config with no --allowedTools. These tests assert on the
    # --mcp-config payload, not on discovery, so the endpoint is stubbed
    # rather than dialled (it never listened here in the first place).
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["ralph_submit_md_artifact"],
    )
    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                requires_completion_evidence=False,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_cmds
    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _agents_invoke_2_json_object(cmd[mcp_index + 1])
    servers = must_mapping(config_payload["mcpServers"])
    assert servers == {
        "ralph": {
            "type": "http",
            "url": "http://127.0.0.1:9999/mcp",
        }
    }
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(name="angular-cli", transport="stdio", command="workspace-cmd"),
    )


# === consolidated from test_agents_invoke_2.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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


# === consolidated from test_agents_invoke_2.py ===
def test_claude_mode_regression_stale_ralph_entry_is_dropped_not_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leftover `ralph` entry in the operator's config is replaced by the live endpoint.

    ``ralph`` is reserved, so the entry cannot be carried through as an
    upstream -- but it is the operator's own config, and Ralph always
    supplies its own endpoint, so a stale entry must be dropped rather
    than reported as an error that blocks every Claude invocation.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "type": "http",
                        "url": "http://wrong.example/mcp",
                    },
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    },
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

    runtime = resolve_invocation_runtime(
        config,
        {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
        tmp_path,
    )

    assert runtime.mcp_endpoint == "http://127.0.0.1:9999/mcp"
    assert runtime.agent_env is not None
    upstream_names = {
        server.name
        for server in load_upstream_mcp_servers(runtime.agent_env[UPSTREAM_MCP_CONFIG_ENV])
    }
    assert "github" in upstream_names
    assert str(RALPH_MCP_SERVER_NAME) not in upstream_names


# === consolidated from test_agents_invoke_2.py ===
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


# === consolidated from test_agents_invoke_3.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
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
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_injects_opencode_mcp_config_for_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("hello", encoding="utf-8")
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
        seen_env.append(env)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
                requires_completion_evidence=False,
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    config_content = _agents_invoke_3_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = must_mapping(config_content["mcp"])
    ralph_config = must_mapping(mcp_config["ralph"])
    permission_config = must_mapping(config_content["permission"])
    assert config_content["$schema"] == "https://opencode.ai/config.json"
    assert ralph_config["type"] == "remote"
    assert ralph_config["url"] == "http://127.0.0.1:9999/mcp"
    assert ralph_config["enabled"] is True
    assert permission_config["ralph_*"] == "allow"


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_fails_fast_when_local_opencode_does_not_support_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The preflight sees the model id verbatim, ``provider/model``.

    The fixture previously wrote the Ralph ALIAS (``-m
    opencode/minimax/MiniMax-M3``) into ``model_flag`` and relied on the
    invoke layer stripping ``opencode/`` a second time. That second strip
    was a bug -- opencode publishes a provider literally named ``opencode``
    -- so the fixture now carries what alias resolution actually emits.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
        model_flag="-m minimax/MiniMax-M3",
    )

    monkeypatch.setattr(
        "ralph.agents.invoke.validate_local_model_support",
        lambda model_id, **kwargs: f"invalid local model: {model_id}",
    )

    def fail_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("subprocess should not be launched when preflight fails")

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fail_popen)

    with pytest.raises(AgentInvocationError, match="invalid local model: minimax/MiniMax-M3"):
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=InvokeOptions(show_progress=False),
                _clock=FakeClock(),
            )
        )


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_merges_existing_opencode_config_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
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
                requires_completion_evidence=False,
            ),
            _clock=FakeClock(),
        )
    )

    assert seen_env
    config_content = _agents_invoke_3_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = must_mapping(config_content["mcp"])
    ralph_config = must_mapping(mcp_config["ralph"])
    assert config_content["model"] == "anthropic/test"
    assert ralph_config["url"] == "http://127.0.0.1:9999/mcp"


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_does_not_inject_opencode_mcp_config_without_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
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
    assert _agents_invoke_3_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/test"}


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_disables_all_native_tools_when_mcp_wired() -> None:
    result = merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    tools = must_mapping(parsed["tools"])
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False, f"Expected {name} to be False"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_keeps_orchestration_tools_enabled_when_mcp_wired() -> None:
    result = merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    tools = must_mapping(parsed["tools"])
    permission = must_mapping(parsed["permission"])
    for name in ("task", "skill", "todowrite", "webfetch", "websearch"):
        assert name in OPENCODE_NATIVE_TOOLS_TO_KEEP
        assert name not in OPENCODE_NATIVE_TOOLS_TO_DISABLE
        assert tools.get(name) is not False, f"Expected {name} to stay enabled"
        assert permission[name] == "allow", f"Expected {name} to be auto-allowed"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_keep_and_disable_lists_are_disjoint() -> None:
    assert not set(OPENCODE_NATIVE_TOOLS_TO_KEEP) & set(OPENCODE_NATIVE_TOOLS_TO_DISABLE)


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_tools_disable_overrides_user_enables() -> None:
    existing = '{"tools": {"bash": true}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    tools = must_mapping(parsed["tools"])
    assert tools["bash"] is False, "MCP policy must override user enable"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_preserves_unrelated_user_tools_sections() -> None:
    existing = '{"tools": {"custom_plugin_tool": true}, "ui": {"theme": "dark"}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    tools = must_mapping(parsed["tools"])
    ui = must_mapping(parsed["ui"])
    assert tools["custom_plugin_tool"] is True
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False
    assert ui["theme"] == "dark"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_mode_extracts_upstream_servers_without_passing_them_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        seen_env.append(_agents_invoke_3_env_dict(kwargs))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
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
                requires_completion_evidence=False,
            ),
            _clock=FakeClock(),
        )
    )

    parsed = _agents_invoke_3_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = must_mapping(parsed["mcp"])
    ralph_server = must_mapping(mcp_config["ralph"])
    # The client timeout must exceed the longest server-side tool (exec); assert
    # that property rather than a brittle literal so it cannot silently regress.
    assert isinstance(ralph_server["timeout"], int)
    assert ralph_server["timeout"] > EXEC_DEFAULT_TIMEOUT_MS
    assert {k: v for k, v in ralph_server.items() if k != "timeout"} == {
        "type": "remote",
        "url": "http://127.0.0.1:9999/mcp",
        "enabled": True,
    }
    assert set(mcp_config) == {"ralph"}
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(
            name="angular-cli",
            transport="stdio",
            command="npx",
            args=("-y", "@angular/cli", "mcp"),
        ),
    )


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_mode_regression_stale_ralph_entry_is_dropped_not_rejected() -> None:
    """A leftover `ralph` entry in the OpenCode config is replaced by the live endpoint."""
    existing = '{"mcp": {"ralph": {"type": "remote", "url": "http://wrong.example/mcp"}}}'

    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")

    mcp_config = must_mapping(_agents_invoke_3_json_object(result)["mcp"])
    assert set(mcp_config) == {"ralph"}
    assert must_mapping(mcp_config["ralph"])["url"] == "http://localhost:0/mcp"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_preserves_unrelated_permission_entries() -> None:
    existing = '{"permission": {"bash": "ask", "custom_tool": "allow"}}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    permission = must_mapping(parsed["permission"])
    assert permission["bash"] == "ask"
    assert permission["custom_tool"] == "allow"
    assert permission["ralph_*"] == "allow"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_allows_all_bare_ralph_mcp_tool_names() -> None:
    result = merge_opencode_config_content(None, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    permission = must_mapping(parsed["permission"])

    for tool_name in ALL_RALPH_TOOLS:
        assert permission[str(tool_name)] == "allow"


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_normalizes_non_dict_mcp_sections() -> None:
    existing = '{"mcp": "invalid", "permission": "invalid", "tools": "invalid"}'
    result = merge_opencode_config_content(existing, "http://localhost:0/mcp")
    parsed = _agents_invoke_3_json_object(result)
    mcp_config = must_mapping(parsed["mcp"])
    permission = must_mapping(parsed["permission"])
    tools = must_mapping(parsed["tools"])
    assert mcp_config["ralph"]
    assert permission["ralph_*"] == "allow"
    for name in OPENCODE_NATIVE_TOOLS_TO_DISABLE:
        assert tools[name] is False


# === consolidated from test_agents_invoke_3.py ===
def test_opencode_config_omits_tools_block_when_no_mcp_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
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
    config_content = _agents_invoke_3_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    assert "tools" not in config_content, "No tools block should be added without MCP endpoint"


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_injects_codex_mcp_config_for_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_env: list[dict[str, str]] = []
    seen_config: list[str] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
        seen_env.append(env)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
                requires_completion_evidence=False,
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


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_injects_codex_master_prompt_file_via_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    master_prompt_file = tmp_path / "MASTER_PROMPT.md"
    master_prompt_file.write_text("unattended mode", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)
    seen_config: list[str] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                master_prompt_file=str(master_prompt_file),
            ),
            _clock=FakeClock(),
        )
    )

    assert len(seen_config) == 1
    parsed = _agents_invoke_3_toml_object(seen_config[0])
    assert parsed["model_instructions_file"] == str(master_prompt_file)
    features = parsed.get("features")
    if features is not None:
        assert "model_instructions_file" not in features


# === consolidated from test_agents_invoke_3.py ===
def test_invoke_agent_prepends_master_prompt_for_opencode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    master_prompt_file = tmp_path / "MASTER_PROMPT.md"
    master_prompt_file.write_text("unattended mode", encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_cmds: list[list[str]] = []
    stdin_writes: list[str] = []

    class _CapturingStdin:
        """Records what Ralph writes to the child's stdin."""

        @staticmethod
        def write(text: str) -> int:
            stdin_writes.append(text)
            return len(text)

        @staticmethod
        def flush() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    class FakeProcess:
        # OpenCode's prompt is delivered on stdin, not argv: the CLI re-quotes
        # a positional message and backslash-escapes every `"` in it, which
        # corrupted the JSON examples inside Ralph's prompts.
        stdin = _CapturingStdin()
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        seen_cmds.append(_agents_invoke_3_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                master_prompt_file=str(master_prompt_file),
            ),
            _clock=FakeClock(),
        )
    )

    # The composed master+phase prompt is delivered on stdin, byte-identical,
    # instead of as an argv token the OpenCode CLI would re-quote and escape.
    assert seen_cmds == [["opencode", "run", "--format", "json", "--auto"]]
    assert stdin_writes == ["unattended mode\n\nhello"]


# === consolidated from test_agents_invoke_3.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size=-1: "")
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
        env = _agents_invoke_3_env_dict(kwargs)
        codex_home = Path(env["CODEX_HOME"])
        copied_auth.append((codex_home / "auth.json").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
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


# === consolidated from test_agents_invoke_3.py ===
def test_codex_config_toml_applies_feature_overrides_when_mcp_wired(tmp_path: Path) -> None:
    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=None,
        master_prompt_file=None,
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _agents_invoke_3_toml_object(config_text)
    features = must_mapping(parsed["features"])
    for key, value in CODEX_NATIVE_FEATURE_OVERRIDES:
        section, subkey = key.split(".", 1)
        nested = must_mapping(parsed[section])
        assert nested[subkey] is (value == "true"), f"Expected {key} = {value}"
    assert features["multi_agent"] is True, "Sub-agents must stay enabled"
    assert "web_search" not in parsed, "web_search must not be force-disabled"
    assert "web_search" not in features


# === consolidated from test_agents_invoke_3.py ===
def test_codex_config_toml_keeps_model_instructions_outside_features(tmp_path: Path) -> None:
    master_prompt_file = tmp_path / "MASTER_PROMPT.md"
    master_prompt_file.write_text("system", encoding="utf-8")
    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=None,
        master_prompt_file=str(master_prompt_file),
    )
    parsed = _agents_invoke_3_toml_object((Path(home) / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model_instructions_file"] == str(master_prompt_file)
    features = must_mapping(parsed["features"])
    assert "model_instructions_file" not in features


# === consolidated from test_agents_invoke_3.py ===
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
        master_prompt_file=None,
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _agents_invoke_3_toml_object(config_text)
    features = must_mapping(parsed["features"])
    assert features["foo"] is True, "Existing feature should be preserved"
    assert features["shell_tool"] is False
    assert features["multi_agent"] is True
    assert features["undo"] is False
    assert features["apps"] is False


# === consolidated from test_agents_invoke_4.py ===
def test_codex_mode_extracts_upstream_servers_without_passing_them_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="codex", output_flag="--json-stream", transport=AgentTransport.CODEX)

    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_: None)

    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        '[mcp_servers.angular-cli]\ncommand = "npx"\nargs = ["-y", "@angular/cli", "mcp"]\n',
        encoding="utf-8",
    )
    seen_env: list[dict[str, str]] = []
    seen_config: list[str] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        env = _agents_invoke_4_env_dict(kwargs)
        seen_env.append(env)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
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

    parsed = _agents_invoke_4_toml_object(seen_config[0])
    mcp_servers = must_mapping(parsed["mcp_servers"])
    assert list(mcp_servers.keys()) == [RALPH_MCP_SERVER_NAME]
    ralph_server = must_mapping(mcp_servers[RALPH_MCP_SERVER_NAME])
    assert ralph_server["url"] == "http://127.0.0.1:9999/mcp"
    assert load_upstream_mcp_servers(seen_env[0][UPSTREAM_MCP_CONFIG_ENV]) == (
        UpstreamMcpServer(
            name="angular-cli",
            transport="stdio",
            command="npx",
            args=("-y", "@angular/cli", "mcp"),
        ),
    )


# === consolidated from test_agents_invoke_4.py ===
def test_build_command_nanocoder_uses_run_prompt_mode(tmp_path: Path) -> None:
    """Nanocoder must use its PTY-backed Ink runtime, not JSON/plain mode."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER)

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert cmd == ["nanocoder", "--mode", "yolo", "--no-plain", "run", "hello"]


# === consolidated from test_agents_invoke_4.py ===
def test_build_command_nanocoder_passes_provider_and_model_flags(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    config = AgentConfig(
        cmd="nanocoder",
        transport=AgentTransport.NANOCODER,
        model_flag="--provider ollama --model llama3.1",
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert cmd[:5] == [
        "nanocoder",
        "--mode",
        "yolo",
        "--provider",
        "ollama",
    ]
    assert cmd[-5:] == ["--model", "llama3.1", "--no-plain", "run", "hello"]


# === consolidated from test_agents_invoke_4.py ===
def test_build_command_nanocoder_keeps_spaced_provider_as_single_argument(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get("nanocoder/MiniMax Coding/MiniMax-M3")

    assert config is not None

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert cmd[:5] == [
        "nanocoder",
        "--mode",
        "yolo",
        "--provider",
        "MiniMax Coding",
    ]
    assert cmd[-5:] == ["--model", "MiniMax-M3", "--no-plain", "run", "hello"]


# === consolidated from test_agents_invoke_4.py ===
def test_codex_mode_regression_stale_ralph_entry_is_dropped_not_rejected(tmp_path: Path) -> None:
    """A leftover `[mcp_servers.ralph]` is replaced by the live endpoint, not rejected."""
    fake_home = tmp_path / "fake_codex"
    fake_home.mkdir()
    (fake_home / "config.toml").write_text(
        '[mcp_servers.ralph]\nurl = "http://wrong.example/mcp"\nenabled = false\n',
        encoding="utf-8",
    )

    home = prepare_codex_home(
        "http://localhost:0/mcp",
        workspace_path=tmp_path,
        existing_home=str(fake_home),
        master_prompt_file=None,
    )

    parsed = _agents_invoke_4_toml_object((Path(home) / "config.toml").read_text(encoding="utf-8"))
    ralph_server = must_mapping(must_mapping(parsed["mcp_servers"])["ralph"])
    assert ralph_server["url"] == "http://localhost:0/mcp"
    assert ralph_server["enabled"] is True


# === consolidated from test_agents_invoke_4.py ===
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
        master_prompt_file=None,
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _agents_invoke_4_toml_object(config_text)
    assert parsed["model"] == "gpt-5"
    assert parsed["approval_policy"] == "never"


# === consolidated from test_agents_invoke_4.py ===
def test_codex_config_toml_omits_features_when_no_endpoint(tmp_path: Path) -> None:
    home = prepare_codex_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
        master_prompt_file="/tmp/sp.md",
    )
    config_text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    parsed = _agents_invoke_4_toml_object(config_text)
    features = must_mapping(parsed["features"]) if "features" in parsed else {}
    assert "shell_tool" not in features, "No features disable without endpoint"


# === consolidated from test_agents_invoke_4.py ===
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


# === consolidated from test_agents_invoke_4.py ===
def test_codex_logs_best_effort_warning_when_mcp_endpoint_wired(tmp_path: Path) -> None:
    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="WARNING")
    try:
        prepare_codex_home(
            "http://localhost:0/mcp",
            workspace_path=tmp_path,
            existing_home=None,
            master_prompt_file=None,
        )
        output = buf.getvalue()
        assert "best-effort" in output, f"Expected 'best-effort' in warning, got: {output!r}"
        assert "Codex" in output, f"Expected 'Codex' in warning, got: {output!r}"
    finally:
        logger.remove(handler_id)


# === consolidated from test_agents_invoke_4.py ===
def test_codex_does_not_log_warning_when_no_endpoint(tmp_path: Path) -> None:
    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="WARNING")
    try:
        prepare_codex_home(
            None,
            workspace_path=tmp_path,
            existing_home=None,
            master_prompt_file="/tmp/sp.md",
        )
        assert "best-effort" not in buf.getvalue(), "No warning when endpoint is None"
    finally:
        logger.remove(handler_id)


# === consolidated from test_agents_invoke_4.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_cmds.append(_agents_invoke_4_argv(args))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setenv("HOME", str(fake_home))

    # Claude's tool restriction now fails CLOSED: a `tools/list` that cannot
    # be reached aborts the launch instead of silently emitting
    # --strict-mcp-config with no --allowedTools. These tests assert on the
    # --mcp-config payload, not on discovery, so the endpoint is stubbed
    # rather than dialled (it never listened here in the first place).
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["ralph_submit_md_artifact"],
    )
    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
                requires_completion_evidence=False,
            ),
            _clock=FakeClock(),
        )
    )

    cmd = seen_cmds[0]
    mcp_index = cmd.index("--mcp-config")
    config_payload = _agents_invoke_4_json_object(cmd[mcp_index + 1])
    servers = must_mapping(config_payload["mcpServers"])
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(servers.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible MCP config, "
        f"got: {list(servers.keys())}"
    )
    ralph_entry = must_mapping(servers[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


# === consolidated from test_agents_invoke_4.py ===
def test_opencode_strict_mode_only_exposes_ralph_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    seen_env: list[dict[str, str]] = []

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_env.append(_agents_invoke_4_env_dict(kwargs))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
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
                requires_completion_evidence=False,
            ),
            _clock=FakeClock(),
        )
    )

    parsed = _agents_invoke_4_json_object(seen_env[0]["OPENCODE_CONFIG_CONTENT"])
    mcp_config = must_mapping(parsed["mcp"])
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(mcp_config.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible OpenCode MCP config, "
        f"got: {list(mcp_config.keys())}"
    )
    ralph_entry = must_mapping(mcp_config[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


# === consolidated from test_agents_invoke_4.py ===
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
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(["ok\n"])
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        env = _agents_invoke_4_env_dict(kwargs)
        seen_env.append(env)
        codex_home = Path(env["CODEX_HOME"])
        seen_config.append((codex_home / "config.toml").read_text(encoding="utf-8"))
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen)
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
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

    parsed = _agents_invoke_4_toml_object(seen_config[0])
    mcp_servers = must_mapping(parsed["mcp_servers"])
    # Strict mode: ONLY Ralph is visible to the provider; user servers are NOT passed through
    assert list(mcp_servers.keys()) == [RALPH_MCP_SERVER_NAME], (
        f"Expected only '{RALPH_MCP_SERVER_NAME}' in provider-visible Codex mcp_servers, "
        f"got: {list(mcp_servers.keys())}"
    )
    ralph_entry = must_mapping(mcp_servers[RALPH_MCP_SERVER_NAME])
    assert ralph_entry["url"] == "http://127.0.0.1:9999/mcp"


# === consolidated from test_agents_invoke_4.py ===
def test_provider_strict_mode_passes_upstream_proxy_payload_to_ralph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    sentinel = tmp_path / ".agent" / "completion_seen_test.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "test"}', encoding="utf-8")

    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)
    seen_envs: dict[str, dict[str, str]] = {}

    class FakeProcess:
        # Ralph delivers OpenCode's prompt on stdin (the CLI re-quotes a
        # positional message), so a process fake must satisfy the
        # ``_SyncProcessLike`` protocol's ``stdin`` member like the real one.
        stdin = None
        pid: int = 12345

        def poll(self) -> int | None:
            return self.returncode

        def __init__(self) -> None:
            self.stdout = iter(
                ["Task declared complete: session_id=test, summary=done, timestamp=1\n"]
            )
            self.stderr = SimpleNamespace(read=lambda _size: "")
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
        seen_envs["claude"] = _agents_invoke_4_env_dict(kwargs)
        return FakeProcess()

    monkeypatch.setattr("ralph.agents.invoke.subprocess.Popen", fake_popen_claude)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setenv("HOME", str(fake_home))
    # Claude's tool restriction now fails CLOSED: a `tools/list` that cannot
    # be reached aborts the launch instead of silently emitting
    # --strict-mcp-config with no --allowedTools. These tests assert on the
    # --mcp-config payload, not on discovery, so the endpoint is stubbed
    # rather than dialled (it never listened here in the first place).
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["ralph_submit_md_artifact"],
    )
    list(
        invoke_agent(
            claude_config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
                requires_completion_evidence=False,
            ),
        )
    )

    # --- OpenCode ---
    def fake_popen_opencode(*args: object, **kwargs: object) -> FakeProcess:
        del args
        seen_envs["opencode"] = _agents_invoke_4_env_dict(kwargs)
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
                requires_completion_evidence=False,
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
        seen_envs["codex"] = _agents_invoke_4_env_dict(kwargs)
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

    # --- AGY ---
    _run_agy_transport_proxy_payload_check(monkeypatch, fake_home, prompt_file, seen_envs, tmp_path)

    # All three baseline transports must pass upstream proxy payload to Ralph via env
    for transport_name in ("claude", "opencode", "codex"):
        env = seen_envs[transport_name]
        assert UPSTREAM_MCP_CONFIG_ENV in env, (
            f"Transport '{transport_name}' did not set {UPSTREAM_MCP_CONFIG_ENV} "
            "for Ralph upstream proxy payload"
        )
        upstreams = load_upstream_mcp_servers(env[UPSTREAM_MCP_CONFIG_ENV])
        assert any(s.name in {"upstream-server", "upstream-agy-http"} for s in upstreams), (
            f"Transport '{transport_name}' did not include expected upstream server "
            "in the upstream proxy payload passed to Ralph"
        )

    agy_upstream_payload = json.loads(seen_envs["agy"][UPSTREAM_MCP_CONFIG_ENV])
    assert any(
        u["name"] == "upstream-agy-http" and u["transport"] == "http" for u in agy_upstream_payload
    ), (
        "AGY HTTP serverUrl upstream must appear in RALPH_UPSTREAM_MCP_CONFIG "
        "after normalization fix"
    )


# === consolidated from test_agents_invoke_4.py ===
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


# === consolidated from test_agents_invoke_4.py ===
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


# === consolidated from test_agents_invoke_4.py ===
def test_nanocoder_command_for_log_redacts_run_prompt_text(tmp_path: Path) -> None:
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text("Build the feature.\n", encoding="utf-8")
    config = AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER)

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    log_line = command_for_log(config, cmd, str(prompt_file))

    assert log_line == f"nanocoder --mode yolo --no-plain run {prompt_file}"
    assert "Build the feature." not in log_line


# === consolidated from test_agents_invoke_4.py ===
@pytest.mark.parametrize(
    ("transport", "command"),
    (
        (AgentTransport.PI, "pi"),
        (AgentTransport.CURSOR, "agent"),
        (AgentTransport.GENERIC, "custom-agent"),
    ),
)
def test_command_for_log_redacts_inline_prompt_for_every_remaining_positional_transport(
    tmp_path: Path,
    transport: AgentTransport,
    command: str,
) -> None:
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text("operator secret", encoding="utf-8")
    config = AgentConfig(cmd=command, transport=transport)

    log_line = command_for_log(
        config,
        [command, "durable secret\n\noperator secret"],
        str(prompt_file),
    )

    assert log_line == f"{command} {prompt_file}"
    assert "durable secret" not in log_line
    assert "operator secret" not in log_line


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_inlines_prompt_content_not_file_path(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert cmd[-1] == prompt_text
    assert str(prompt_file) not in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_includes_print_flag(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(
        cmd="agy",
        transport=AgentTransport.AGY,
        print_flag="--print",
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert "--print" in cmd
    assert cmd.index("--print") < len(cmd) - 1
    assert cmd[-1] == prompt_text


# === consolidated from test_agents_invoke_4.py ===
def test_build_agy_command_all_flags_precede_print_and_prompt(tmp_path: Path) -> None:
    prompt_text = "hello"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        session_flag="--conversation {}",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        can_commit=False,
        transport=AgentTransport.AGY,
    )

    result = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="sess-1", verbose=True),
    )

    assert result == [
        "agy",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--conversation",
        "sess-1",
        "--verbose",
        "--print",
        "hello",
    ]


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_appends_yolo_flag(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(
        cmd="agy",
        transport=AgentTransport.AGY,
        yolo_flag="--dangerously-skip-permissions",
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert "--dangerously-skip-permissions" in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_appends_session_flag(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(
        cmd="agy",
        transport=AgentTransport.AGY,
        session_flag="--conversation {}",
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="test-session-123"),
    )

    assert "--conversation" in cmd
    assert "test-session-123" in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_claude_interactive_command_uses_config_session_flag(tmp_path: Path) -> None:
    """The claude-interactive builder emits the resume flag from config.session_flag.

    Regression for the cross-transport drift: the resume flag SYNTAX has exactly
    one source (config.session_flag), shared by every builder. The interactive
    builder must not hardcode its own --resume string.
    """
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text("Build the feature.\n", encoding="utf-8")
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        session_flag="--resume {}",
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="sess-abc"),
    )

    assert "--resume" in cmd
    assert "sess-abc" in cmd
    assert "--session-id" not in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_claude_interactive_command_honors_custom_session_flag(tmp_path: Path) -> None:
    """A custom session_flag is honored by the interactive builder, not ignored.

    Before the fix the interactive builder ignored config.session_flag and
    hardcoded --resume, so a custom flag silently diverged from the other
    transports. config.session_flag is now the single syntax source.
    """
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text("Build the feature.\n", encoding="utf-8")
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        session_flag="--continue {}",
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(session_id="sess-xyz"),
    )

    assert "--continue" in cmd
    assert "sess-xyz" in cmd
    assert "--resume" not in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_appends_verbose_flag(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(
        cmd="agy",
        transport=AgentTransport.AGY,
        verbose_flag="--verbose",
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions(verbose=True))

    assert "--verbose" in cmd


# === consolidated from test_agents_invoke_4.py ===
def test_agy_command_appends_multimodal_sidecar_content(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    sidecar_file = tmp_path / "task_multimodal_handoff.json"
    sidecar_file.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "modality": "image",
                        "title": "Screenshot",
                        "uri": "ralph://media/abc123",
                        "delivery": "resource_reference_replay",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert "## Multimodal Artifacts" in cmd[-1]
    assert "ralph://media/abc123" in cmd[-1]
    assert cmd[-1].startswith(prompt_text)


# === consolidated from test_agents_invoke_4.py ===
def test_check_agent_available_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke.shutil.which", lambda name: f"/usr/bin/{name}")
    config = AgentConfig(cmd="claude")
    assert check_agent_available(config) is True


# === consolidated from test_agents_invoke_4.py ===
def test_check_agent_available_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke.shutil.which", lambda name: None)
    config = AgentConfig(cmd="nonexistent-xyz")
    assert check_agent_available(config) is False


# === consolidated from test_agents_invoke_4.py ===
def test_check_agent_available_empty_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def recording_which(name: str) -> str | None:
        calls.append(name)
        return None

    monkeypatch.setattr("ralph.agents.invoke.shutil.which", recording_which)
    config = AgentConfig(cmd="")
    assert check_agent_available(config) is False
    assert calls == []


# === consolidated from test_agents_invoke_5.py ===
def test_build_invoke_options_propagates_unsafe_mode_from_general_config() -> None:
    """Unsafe_mode in [general.workflow] flows into InvokeOptions."""
    cfg = GeneralConfig(workflow={"unsafe_mode": True})
    opts = build_invoke_options_from_config(cfg)
    assert opts.unsafe_mode is True

    cfg_default = GeneralConfig()
    opts_default = build_invoke_options_from_config(cfg_default)
    assert opts_default.unsafe_mode is False


# === consolidated from test_agents_invoke_5.py ===
def test_opencode_runtime_propagates_unsafe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to build_opencode_provider_config."""
    config = AgentConfig(
        cmd="opencode",
        output_flag="--json-stream",
        transport=AgentTransport.OPENCODE,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
    captured: dict[str, object] = {}

    def fake_build(
        config_content: str | None,
        endpoint: str,
        *,
        unsafe_mode: bool = False,
        workspace_path: Path | None = None,
    ) -> tuple[str, list[object]]:
        del config_content, endpoint, workspace_path
        captured["unsafe_mode"] = unsafe_mode
        return ("{}", [])

    monkeypatch.setattr(invoke_module, "build_opencode_provider_config", fake_build)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=True)
    assert captured["unsafe_mode"] is True

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=False)
    assert captured["unsafe_mode"] is False


# === consolidated from test_agents_invoke_5.py ===
def test_nanocoder_runtime_propagates_unsafe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to build_nanocoder_mcp_config."""
    config = AgentConfig(
        cmd="nanocoder",
        transport=AgentTransport.NANOCODER,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}
    captured: dict[str, object] = {}

    def fake_build(
        existing: str | None,
        endpoint: str,
        *,
        always_allow: tuple[str, ...] = (),
        unsafe_mode: bool = False,
        workspace_path: object = None,
        env: object = None,
    ) -> tuple[str, tuple[object, ...]]:
        captured["unsafe_mode"] = unsafe_mode
        captured["workspace_path"] = workspace_path
        captured["env"] = env
        return ("{}", ())

    monkeypatch.setattr(invoke_module, "build_nanocoder_mcp_config", fake_build)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke_module.resolve_invocation_runtime(config, extra_env, workspace, unsafe_mode=True)
    assert captured["unsafe_mode"] is True
    assert captured["workspace_path"] == workspace


# === consolidated from test_agents_invoke_5.py ===
def test_codex_runtime_propagates_unsafe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_invocation_runtime forwards unsafe_mode to prepare_codex_home_with_upstreams."""
    config = AgentConfig(
        cmd="codex",
        output_flag="",
        transport=AgentTransport.CODEX,
    )
    extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}
    captured: dict[str, object] = {}

    def fake_prepare(
        endpoint: str | None,
        *,
        workspace_path: object,
        existing_home: str | None,
        master_prompt_file: object,
        unsafe_mode: bool = False,
    ) -> tuple[str, list[object]]:
        captured["unsafe_mode"] = unsafe_mode
        return ("/fake/home", [])

    monkeypatch.setattr(invoke_module, "prepare_codex_home_with_upstreams", fake_prepare)
    monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
    monkeypatch.setattr(invoke_module, "merge_mcp_toml_into_upstreams", lambda u, m: u)
    monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

    invoke_module.resolve_invocation_runtime(config, extra_env, None, unsafe_mode=True)
    assert captured["unsafe_mode"] is True


# === consolidated from test_agents_invoke_5.py ===
def test_claude_command_propagates_unsafe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_command passes unsafe_mode to claude_mcp_config via BuildCommandOptions."""
    config = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE,
        output_flag="--output-format=stream-json",
    )
    captured: dict[str, object] = {}

    def fake_claude_mcp_config(
        endpoint: str,
        *,
        workspace_path: object = None,
        unsafe_mode: bool = False,
    ) -> str:
        captured["unsafe_mode"] = unsafe_mode
        captured["workspace_path"] = workspace_path
        return json.dumps({"mcpServers": {"ralph": {"url": endpoint}}})

    monkeypatch.setattr(
        "ralph.agents.invoke._command_builders.claude_mcp_config",
        fake_claude_mcp_config,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")
    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://localhost:9999/mcp",
            unsafe_mode=True,
        ),
    )
    assert captured["unsafe_mode"] is True
    assert "--mcp-config" in cmd
    idx = cmd.index("--mcp-config")
    assert "ralph" in cmd[idx + 1]


# === consolidated from test_agents_invoke_5.py ===
def test_load_config_cli_override_propagates_to_invoke_options() -> None:
    """End-to-end: CLI --unsafe-mode reaches GeneralConfig and InvokeOptions."""
    cfg = load_config(
        workspace_scope=WorkspaceScope(Path("/tmp")),
        cli_overrides={"general": {"workflow": {"unsafe_mode": True}}},
    )
    assert cfg.general.workflow.unsafe_mode is True

    opts = build_invoke_options_from_config(cfg.general)
    assert opts.unsafe_mode is True


# === consolidated from test_agents_invoke_5.py ===
def test_load_config_absent_override_keeps_default() -> None:
    """A CLI run without --unsafe-mode keeps the default of False."""
    cfg = load_config(workspace_scope=WorkspaceScope(Path("/tmp")), cli_overrides={})
    assert cfg.general.workflow.unsafe_mode is False


# === consolidated from test_agents_invoke_5.py ===
def test_agy_command_includes_add_dir_workspace_path(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY, print_flag="--print")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    add_dir_index = cmd.index("--add-dir")
    print_index = cmd.index("--print")
    assert add_dir_index < print_index
    assert cmd[add_dir_index + 1] == str(tmp_path)
    assert cmd[-1] == prompt_text


# === consolidated from test_agents_invoke_2.py ===
class _RecordingProcessTeardown:
    """Record requested process-subtree teardowns without touching the OS."""

    def __init__(self) -> None:
        self.calls: tuple[int, ...] = ()

    def teardown_subtree(self, host_pid: int) -> None:
        self.calls = (*self.calls, host_pid)


# === consolidated from test_agents_invoke_5.py ===
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

        def fake_build(
            config_content: str | None,
            endpoint: str,
            **kwargs: object,
        ) -> tuple[str, list[object]]:
            del kwargs
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
            master_prompt_file: object,
            **kwargs: object,
        ) -> tuple[str, list[object]]:
            del kwargs
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

    def test_agy_runtime_sets_mcp_endpoint_and_upstream_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = AgentConfig(
            cmd="agy",
            transport=AgentTransport.AGY,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999"}

        monkeypatch.setattr(
            invoke_module,
            "load_existing_agy_upstream_servers",
            lambda workspace_path: (),
        )
        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        result = invoke_module.resolve_invocation_runtime(config, extra_env, None)
        assert result.mcp_endpoint == "http://localhost:9999"
        assert result.agent_env is not None

    def test_agy_runtime_early_exit_when_no_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = AgentConfig(
            cmd="agy",
            transport=AgentTransport.AGY,
        )
        monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)

        result = invoke_module.resolve_invocation_runtime(config, None, None)

        assert result.agent_env is None
        assert result.server_env is None
        assert result.mcp_endpoint is None

    def test_nanocoder_runtime_sets_managed_mcp_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = AgentConfig(
            cmd="nanocoder",
            transport=AgentTransport.NANOCODER,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}

        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)

        result = invoke_module.resolve_invocation_runtime(config, extra_env, Path("/tmp"))

        assert result.mcp_endpoint == "http://localhost:9999/mcp"
        assert result.agent_env is not None
        assert result.agent_env["NANOCODER_TRUST_DIRECTORY"] == "1"
        payload = _agents_invoke_5_json_object(result.agent_env["NANOCODER_MCPSERVERS"])
        servers = payload["mcpServers"]
        assert servers["ralph"]["transport"] == "http"
        assert servers["ralph"]["url"] == "http://localhost:9999/mcp"

    def test_nanocoder_runtime_auto_allows_discovered_ralph_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = AgentConfig(
            cmd="nanocoder",
            transport=AgentTransport.NANOCODER,
        )
        extra_env = {str(MCP_ENDPOINT_ENV): "http://localhost:9999/mcp"}

        monkeypatch.setattr(invoke_module, "mcp_toml_as_upstreams", lambda p: [])
        monkeypatch.setattr(invoke_module, "set_upstream_mcp_config", lambda e, u: None)
        monkeypatch.setattr(
            invoke_module,
            "discover_http_mcp_tool_names",
            lambda endpoint: [
                "read_file",
                "mcp__ralph__read_file",
                "ralph_submit_md_artifact",
            ],
        )

        result = invoke_module.resolve_invocation_runtime(config, extra_env, Path("/tmp"))

        assert result.agent_env is not None
        payload = _agents_invoke_5_json_object(result.agent_env["NANOCODER_MCPSERVERS"])
        servers = payload["mcpServers"]
        assert servers["ralph"]["alwaysAllow"] == [
            "read_file",
            "mcp__ralph__read_file",
            "ralph_submit_md_artifact",
            "mcp__ralph__ralph_submit_md_artifact",
        ]

    def test_prepare_interactive_claude_options_preserves_new_invoke_fields(self) -> None:
        config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)

        def _permission_listener(_message: str) -> None:
            return None

        options = InvokeOptions(
            session_id="sess-existing",
            post_tool_result_progression_seconds=12.0,
            permission_prompt_listener=_permission_listener,
        )

        prepared = invoke_module._prepare_interactive_claude_options(options, config)

        assert prepared.post_tool_result_progression_seconds == 12.0
        assert prepared.permission_prompt_listener is _permission_listener
        assert prepared.session_id == "sess-existing"
        assert prepared.initial_session_id == "sess-existing"
