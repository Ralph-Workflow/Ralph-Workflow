"""Direct behavior tests for ``KimiCommandBuilder`` and ``KimiRuntimeResolver``.

The dispatch-table guard at
``tests/agents/invoke/test_dispatch_table_covers_every_transport.py``
asserts that ``COMMAND_BUILDERS[AgentTransport.KIMI]`` and
``RUNTIME_RESOLVERS[AgentTransport.KIMI]`` are populated, but does not
exercise their actual behavior.  This module pins the observable
behavior of both classes for the kimi transport so the wire format and
MCP-closure rules cannot silently regress.

The argv contract is the ADAPTED S-5 shape, re-derived from the live
kimi-code v0.36.1 binary (the plan's original ``kimi --print -p ...
--afk`` shape came from the stale kimi-cli documentation and is
rejected by the live binary):

    kimi --output-format=stream-json [-r <session>] [-m <model>] -p <prompt>

with the prompt text as exactly one argv value.

The MCP closure is the ADAPTED S-6/S-7 shape: the documented Kimi Code
config surface is the user-global ``$KIMI_CODE_HOME/mcp.json``
(defaulting to ``~/.kimi-code/mcp.json``) plus the project-local
``<workspace>/.kimi-code/mcp.json``; the legacy ``~/.kimi/mcp.json``
path from the kimi-cli docs is deliberately never touched.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import CompletionEnforcingStrategy, strategy_for_transport
from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import KimiCommandBuilder
from ralph.agents.invoke._runtime_resolvers import (
    RUNTIME_RESOLVERS,
    KimiRuntimeResolver,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.kimi import (
    _kimi_global_config_path,
    kimi_mcp_config,
    load_existing_kimi_upstream_servers,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _make_prompt(tmp_path: Path) -> str:
    p = tmp_path / "PROMPT.md"
    p.write_text("hello world", encoding="utf-8")
    return str(p)


def _kimi_config(**overrides: object) -> AgentConfig:
    config: dict[str, object] = {
        "cmd": "kimi",
        "output_flag": "--output-format=stream-json",
        "yolo_flag": None,
        "print_flag": "-p",
        "session_flag": "-r {}",
        "transport": AgentTransport.KIMI,
    }
    config.update(overrides)
    return AgentConfig.model_validate(config)


class TestKimiCommandBuilder:
    """Pin the measured ``kimi --output-format=stream-json -p <prompt>`` argv shape."""

    def test_minimal_argv_is_kimi_output_format_print_prompt(self, tmp_path: Path) -> None:
        """No session, no model, no yolo: argv is exactly the headless shape."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(), prompt_file, options=BuildCommandOptions(workspace_path=tmp_path)
        )

        assert cmd == ["kimi", "--output-format=stream-json", "-p", "hello world"]

    def test_legacy_stale_doc_tokens_are_not_emitted(self, tmp_path: Path) -> None:
        """The stale kimi-cli flags the live binary rejects must never appear."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(), prompt_file, options=BuildCommandOptions(workspace_path=tmp_path)
        )

        assert "--print" not in cmd
        assert "--afk" not in cmd
        assert "--yolo" not in cmd
        assert "--auto" not in cmd
        assert "--plan" not in cmd

    def test_session_flag_is_dash_r_with_value(self, tmp_path: Path) -> None:
        """The documented resume flag ``-r <id>`` lands before the model flag."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(),
            prompt_file,
            options=BuildCommandOptions(session_id="sess-1", workspace_path=tmp_path),
        )

        assert cmd == [
            "kimi",
            "--output-format=stream-json",
            "-r",
            "sess-1",
            "-p",
            "hello world",
        ]

    def test_session_id_with_spaces_stays_one_argv_element(self, tmp_path: Path) -> None:
        """A session id containing spaces must stay as a single argv value."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(),
            prompt_file,
            options=BuildCommandOptions(session_id="abc def", workspace_path=tmp_path),
        )

        r_index = cmd.index("-r")
        assert cmd[r_index + 1] == "abc def"
        # The session id must not be tokenized into separate elements.
        assert "abc" not in cmd[:r_index] + cmd[r_index + 2 :]

    def test_model_flag_is_dash_m_with_value(self, tmp_path: Path) -> None:
        """The model template wraps a bare id into ``-m <model>``."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(),
            prompt_file,
            options=BuildCommandOptions(
                model_flag="kimi/kimi-for-coding", workspace_path=tmp_path
            ),
        )

        assert cmd == [
            "kimi",
            "--output-format=stream-json",
            "-m",
            "kimi/kimi-for-coding",
            "-p",
            "hello world",
        ]

    def test_full_argv_layout(self, tmp_path: Path) -> None:
        """Documented ``kimi --output-format=stream-json -r ID -m M -p P`` layout."""
        prompt_file = _make_prompt(tmp_path)
        cmd = KimiCommandBuilder().build(
            _kimi_config(),
            prompt_file,
            options=BuildCommandOptions(
                session_id="sess-1",
                model_flag="kimi/kimi-for-coding",
                workspace_path=tmp_path,
            ),
        )

        assert cmd == [
            "kimi",
            "--output-format=stream-json",
            "-r",
            "sess-1",
            "-m",
            "kimi/kimi-for-coding",
            "-p",
            "hello world",
        ]

    def test_prepends_master_prompt(self, tmp_path: Path) -> None:
        """The kimi transport is not CODEX, so the master prompt is concatenated."""
        prompt_file = _make_prompt(tmp_path)
        master = tmp_path / "MASTER_PROMPT.md"
        master.write_text("durable rules", encoding="utf-8")

        cmd = KimiCommandBuilder().build(
            _kimi_config(),
            prompt_file,
            options=BuildCommandOptions(
                workspace_path=tmp_path,
                master_prompt_file=str(master),
            ),
        )

        assert cmd[-1] == "durable rules\n\nhello world"
        assert cmd[-2] == "-p"

    def test_operator_cmd_override_preserves_wrapper_tokens(self, tmp_path: Path) -> None:
        """A multi-token ``cmd`` override keeps the wrapper path AND its flags."""
        prompt_file = _make_prompt(tmp_path)
        config = _kimi_config(cmd="/opt/wrapper/kimi --telemetry-flag")

        cmd = KimiCommandBuilder().build(
            config, prompt_file, options=BuildCommandOptions(workspace_path=tmp_path)
        )

        assert cmd[:3] == ["/opt/wrapper/kimi", "--telemetry-flag", "--output-format=stream-json"]
        assert cmd[-2:] == ["-p", "hello world"]

    def test_prompt_is_exactly_one_argv_element(self, tmp_path: Path) -> None:
        """A prompt with newlines and quotes stays one argv value."""
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("line one\nline two 'quoted'", encoding="utf-8")

        cmd = KimiCommandBuilder().build(
            _kimi_config(), str(prompt_file), options=BuildCommandOptions(workspace_path=tmp_path)
        )

        assert cmd[-1] == "line one\nline two 'quoted'"
        assert cmd[-2] == "-p"


class TestKimiRuntimeResolverNoEndpoint:
    """Without an MCP endpoint the resolver returns the minimal runtime."""

    def test_registered_in_runtime_resolvers(self) -> None:
        assert RUNTIME_RESOLVERS[AgentTransport.KIMI] is KimiRuntimeResolver

    def test_no_mcp_endpoint_returns_minimal_runtime(self, tmp_path: Path) -> None:
        runtime = KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={"FOO": "bar"},
            workspace_path=tmp_path,
            base_env={},
        )
        assert runtime.agent_env == {"FOO": "bar"}
        assert runtime.server_env is None
        assert runtime.mcp_endpoint is None
        assert runtime.cleanup is None or callable(runtime.cleanup) is True

    def test_no_config_files_written_without_endpoint(self, tmp_path: Path) -> None:
        KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={"FOO": "bar"},
            workspace_path=tmp_path,
            base_env={},
        )
        assert not (tmp_path / ".kimi-code").exists()


class TestKimiRuntimeResolverMcpWiring:
    """With an MCP endpoint the resolver writes and restores Kimi's config paths."""

    ENDPOINT = "http://127.0.0.1:54321/mcp"

    def test_resolve_writes_workspace_and_global_configs(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))

        runtime = KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={str(MCP_ENDPOINT_ENV): self.ENDPOINT},
            workspace_path=tmp_path,
            base_env={},
        )

        try:
            assert runtime.mcp_endpoint == self.ENDPOINT

            # Capture the on-disk state BEFORE cleanup (the restore only
            # happens via runtime.cleanup(); the finally guarantees a
            # failed assertion cannot strand the held kimi MCP lock).
            workspace_config = tmp_path / ".kimi-code" / "mcp.json"
            global_config = kimi_home / "mcp.json"
            captured = {
                config_path: (
                    config_path.read_bytes() if config_path.is_file() else None
                )
                for config_path in (workspace_config, global_config)
            }
        finally:
            runtime.cleanup()

        for config_path, payload_bytes in captured.items():
            assert payload_bytes is not None, f"{config_path} was not written"
            payload = json.loads(payload_bytes)
            assert payload["mcpServers"][RALPH_MCP_SERVER_NAME] == {"url": self.ENDPOINT}

        # Cleanup restores both paths (they did not exist before).
        assert not workspace_config.exists()
        assert not global_config.exists()
        # The .kimi-code directory itself may remain; only the file is
        # removed (mirroring the cursor contract).
        assert not kimi_home.joinpath("mcp.json").exists()

    def test_cleanup_restores_pre_existing_global_bytes(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
        kimi_home.mkdir()
        original_bytes = json.dumps(
            {"mcpServers": {"operator-managed": {"url": "http://example.invalid/sse"}}},
            indent=4,
        ).encode("utf-8")
        global_config = kimi_home / "mcp.json"
        global_config.write_bytes(original_bytes)

        runtime = KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={str(MCP_ENDPOINT_ENV): self.ENDPOINT},
            workspace_path=tmp_path,
            base_env={},
        )

        try:
            # Capture the during-run state inside try/finally so a failed
            # assertion cannot strand the held kimi MCP lock.
            during_bytes = global_config.read_bytes()
        finally:
            runtime.cleanup()

        during = json.loads(during_bytes)
        assert RALPH_MCP_SERVER_NAME in during["mcpServers"]
        assert "operator-managed" not in during["mcpServers"]  # unsafe_mode=False drops upstreams

        assert global_config.read_bytes() == original_bytes

    def test_unsafe_mode_preserves_existing_upstreams(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
        kimi_home.mkdir()
        (kimi_home / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"operator-managed": {"url": "http://example.invalid/sse"}}}
            ),
            encoding="utf-8",
        )

        runtime = KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={str(MCP_ENDPOINT_ENV): self.ENDPOINT},
            workspace_path=tmp_path,
            base_env={},
            unsafe_mode=True,
        )

        try:
            # Capture during-run state inside try/finally (lock-safety).
            during_bytes = (kimi_home / "mcp.json").read_bytes()
            server_env = runtime.server_env
        finally:
            runtime.cleanup()

        assert server_env is not None
        during = json.loads(during_bytes)
        assert during["mcpServers"][RALPH_MCP_SERVER_NAME] == {"url": self.ENDPOINT}
        # The canonical upstream dict adds name/transport keys alongside
        # the url (same shape the cursor / agy merges write).
        assert during["mcpServers"]["operator-managed"]["url"] == "http://example.invalid/sse"

        restored = json.loads((kimi_home / "mcp.json").read_text(encoding="utf-8"))
        assert set(restored["mcpServers"]) == {"operator-managed"}

    def test_stale_kimi_cli_doc_path_is_never_touched(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        """The legacy ``~/.kimi/mcp.json`` surface from the stale docs stays alone."""
        fake_home = tmp_path / "fake-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
        monkeypatch.setenv("HOME", str(fake_home))

        KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={str(MCP_ENDPOINT_ENV): self.ENDPOINT},
            workspace_path=tmp_path,
            base_env={},
        )

        assert not (fake_home / ".kimi").exists()
        assert not (fake_home / ".kimi-code").exists()

    def test_endpoint_in_base_env_also_wires_mcp(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))

        runtime = KimiRuntimeResolver().resolve(
            _kimi_config(),
            extra_env={"FOO": "bar"},
            workspace_path=tmp_path,
            base_env={str(MCP_ENDPOINT_ENV): self.ENDPOINT},
        )

        assert runtime.mcp_endpoint == self.ENDPOINT
        assert (tmp_path / ".kimi-code" / "mcp.json").is_file()
        runtime.cleanup()


class TestKimiTransportHelpers:
    """Direct helper-level pins for the kimi MCP transport module."""

    def test_kimi_mcp_config_uses_cursor_shape(self) -> None:
        assert json.loads(kimi_mcp_config("http://localhost:1234/mcp")) == {
            "mcpServers": {RALPH_MCP_SERVER_NAME: {"url": "http://localhost:1234/mcp"}}
        }

    def test_global_config_path_honors_kimi_code_home(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "custom-home"))
        assert _kimi_global_config_path() == tmp_path / "custom-home" / "mcp.json"

    def test_global_config_path_empty_env_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("KIMI_CODE_HOME", "")
        assert _kimi_global_config_path() == tmp_path / ".kimi-code" / "mcp.json"

    def test_load_existing_upstream_servers_filters_ralph_entry(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
        kimi_home.mkdir()
        (kimi_home / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        RALPH_MCP_SERVER_NAME: {"url": "http://stale.invalid/mcp"},
                        "operator-managed": {"url": "http://example.invalid/sse"},
                    }
                }
            ),
            encoding="utf-8",
        )

        upstreams = load_existing_kimi_upstream_servers(tmp_path)
        assert [u.name for u in upstreams] == ["operator-managed"]

    def test_workspace_local_config_wins_in_loader(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        """Workspace-local entries shadow user-global entries with the same name."""
        kimi_home = tmp_path / "kimi-home"
        monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
        kimi_home.mkdir()
        (kimi_home / "mcp.json").write_text(
            json.dumps({"mcpServers": {"shared": {"url": "http://global.invalid/sse"}}}),
            encoding="utf-8",
        )
        workspace_config = tmp_path / ".kimi-code" / "mcp.json"
        workspace_config.parent.mkdir()
        workspace_config.write_text(
            json.dumps({"mcpServers": {"shared": {"url": "http://workspace.invalid/sse"}}}),
            encoding="utf-8",
        )

        upstreams = load_existing_kimi_upstream_servers(tmp_path)
        assert [u.url for u in upstreams] == ["http://workspace.invalid/sse"]


class TestKimiCompletionEnforcement:
    """The kimi strategy enforces completion like the other headless session transports."""

    def test_kimi_transport_uses_completion_enforcing_strategy(self) -> None:
        strategy = strategy_for_transport(AgentTransport.KIMI)

        assert isinstance(strategy, CompletionEnforcingStrategy)
        assert strategy.supports_completion_enforcement() is True
        # Session-continuation stance mirrors the cursor / agy factories
        # (the flag is only overridden True by the pi factory, whose
        # resumable-exit contract needs it).
        cursor_strategy = strategy_for_transport(AgentTransport.CURSOR)
        assert strategy.supports_session_continuation() == (
            cursor_strategy.supports_session_continuation()
        )

    def test_strategy_classifies_tool_activity(self) -> None:
        """The kimi strategy maps tool frames onto tool activity signals."""
        strategy = strategy_for_transport(AgentTransport.KIMI)
        classify = getattr(strategy, "classify_activity_line", None)
        assert callable(classify), "kimi strategy must expose classify_activity_line"

        tool_result = json.dumps(
            {"role": "tool", "tool_call_id": "call_1", "content": "out"}
        )
        tool_use = json.dumps(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_1",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
        plain_text = json.dumps({"role": "assistant", "content": "thinking out loud"})

        result_signal = classify(tool_result)
        assert result_signal is not None
        assert result_signal.kind is AgentActivityKind.TOOL_RESULT
        use_signal = classify(tool_use)
        assert use_signal is not None
        assert use_signal.kind is AgentActivityKind.TOOL_USE
        # Plain assistant text is NOT tool activity (the generic base
        # classifies it as an ordinary output line).
        plain_signal = classify(plain_text)
        assert plain_signal is None or plain_signal.kind not in (
            AgentActivityKind.TOOL_USE,
            AgentActivityKind.TOOL_RESULT,
        )


@pytest.mark.parametrize(
    ("session_id", "expected_argv_tail"),
    [
        ("sess-abc-1", ["-r", "sess-abc-1"]),
        ("with spaces", ["-r", "with spaces"]),
    ],
)
def test_session_flag_formatting_matrix(
    tmp_path: Path, session_id: str, expected_argv_tail: list[str]
) -> None:
    prompt_file = _make_prompt(tmp_path)
    cmd = KimiCommandBuilder().build(
        _kimi_config(),
        prompt_file,
        options=BuildCommandOptions(session_id=session_id, workspace_path=tmp_path),
    )
    assert cmd[2:2 + len(expected_argv_tail)] == expected_argv_tail
