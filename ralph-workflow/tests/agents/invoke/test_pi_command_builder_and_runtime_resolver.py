"""Direct behavior tests for ``PiCommandBuilder`` and ``PiRuntimeResolver``.

The dispatch-table guard at
``tests/agents/invoke/test_dispatch_table_covers_every_transport.py``
asserts that ``COMMAND_BUILDERS[AgentTransport.PI]`` and
``RUNTIME_RESOLVERS[AgentTransport.PI]`` are populated, but does not
exercise their actual behavior.  This module pins the observable
behavior of both classes for the pi transport so the wire format and
MCP-closure rules cannot silently regress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import PiCommandBuilder
from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._runtime_resolvers import (
    RUNTIME_RESOLVERS,
    PiRuntimeResolver,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from pathlib import Path


def _make_prompt(tmp_path: Path) -> str:
    p = tmp_path / "PROMPT.md"
    p.write_text("hello world", encoding="utf-8")
    return str(p)


def _pi_config() -> AgentConfig:
    return AgentConfig(
        cmd="pi",
        output_flag=None,
        yolo_flag="--approve",
        session_flag="--session {}",
        transport=AgentTransport.PI,
    )


class TestPiCommandBuilder:
    """Pin the documented ``pi --mode json <prompt>`` argv shape."""

    def test_minimal_argv_is_pi_mode_json_prompt(self, tmp_path: Path) -> None:
        """No session, no model, no yolo: argv is exactly ``pi --mode json --approve <prompt>``."""
        prompt_file = _make_prompt(tmp_path)
        config = _pi_config()
        options = BuildCommandOptions(workspace_path=tmp_path)

        cmd = PiCommandBuilder().build(config, prompt_file, options=options)

        assert cmd == ["pi", "--mode", "json", "--approve", "hello world"]

    def test_mode_json_is_two_argv_tokens_not_one(self, tmp_path: Path) -> None:
        """The argv must NOT contain the literal ``'--mode json'`` as a single element."""
        prompt_file = _make_prompt(tmp_path)
        cmd = PiCommandBuilder().build(
            _pi_config(), prompt_file, options=BuildCommandOptions(workspace_path=tmp_path)
        )

        # The raw argv list keeps the two tokens as separate items.
        assert "--mode" in cmd
        assert "json" in cmd
        assert "--mode json" not in cmd

    def test_session_flag_appears_before_yolo(self, tmp_path: Path) -> None:
        """Session flag is emitted before yolo (mirrors non-agy ordering)."""
        prompt_file = _make_prompt(tmp_path)
        cmd = PiCommandBuilder().build(
            _pi_config(),
            prompt_file,
            options=BuildCommandOptions(
                session_id="sess-1", workspace_path=tmp_path
            ),
        )

        assert cmd == [
            "pi",
            "--mode",
            "json",
            "--session",
            "sess-1",
            "--approve",
            "hello world",
        ]

    def test_model_flag_is_emitted_as_two_argv_tokens(self, tmp_path: Path) -> None:
        """``--model <value>`` is two argv tokens, not one."""
        prompt_file = _make_prompt(tmp_path)
        cmd = PiCommandBuilder().build(
            _pi_config(),
            prompt_file,
            options=BuildCommandOptions(
                model_flag="--model anthropic/claude-sonnet-4-20250514",
                workspace_path=tmp_path,
            ),
        )

        assert "--model" in cmd
        assert "anthropic/claude-sonnet-4-20250514" in cmd
        # ``--model anthropic/claude-sonnet-4-20250514`` must not appear as a
        # single argv element.
        assert (
            "--model anthropic/claude-sonnet-4-20250514" not in cmd
        )

    def test_full_argv_layout(self, tmp_path: Path) -> None:
        """Documented ``pi --mode json --session ID --approve --model M <prompt>`` layout."""
        prompt_file = _make_prompt(tmp_path)
        cmd = PiCommandBuilder().build(
            _pi_config(),
            prompt_file,
            options=BuildCommandOptions(
                session_id="sess-1",
                model_flag="--model gpt-4",
                workspace_path=tmp_path,
            ),
        )

        assert cmd == [
            "pi",
            "--mode",
            "json",
            "--session",
            "sess-1",
            "--approve",
            "--model",
            "gpt-4",
            "hello world",
        ]


class TestPiRuntimeResolver:
    """Pi has no documented CLI MCP wiring path -> fail closed on any MCP endpoint."""

    def test_registered_in_runtime_resolvers(self) -> None:
        assert RUNTIME_RESOLVERS[AgentTransport.PI] is PiRuntimeResolver

    def test_no_mcp_endpoint_returns_minimal_runtime(self, tmp_path: Path) -> None:
        config = _pi_config()
        runtime = PiRuntimeResolver().resolve(
            config,
            extra_env={"FOO": "bar"},
            workspace_path=tmp_path,
        )
        assert runtime.agent_env == {"FOO": "bar"}
        assert runtime.server_env is None
        assert runtime.mcp_endpoint is None

    def test_extra_env_is_preserved_when_no_mcp(self, tmp_path: Path) -> None:
        config = _pi_config()
        runtime = PiRuntimeResolver().resolve(
            config,
            extra_env={"FOO": "bar", "BAZ": "qux"},
            workspace_path=tmp_path,
        )
        assert runtime.agent_env == {"FOO": "bar", "BAZ": "qux"}
        assert runtime.server_env is None
        assert runtime.mcp_endpoint is None

    def test_mcp_endpoint_in_extra_env_raises(self, tmp_path: Path) -> None:
        config = _pi_config()
        with pytest.raises(UnsupportedMcpTransportError) as excinfo:
            PiRuntimeResolver().resolve(
                config,
                extra_env={MCP_ENDPOINT_ENV: "http://localhost:9999/mcp"},
                workspace_path=tmp_path,
            )
        assert "pi" in str(excinfo.value).lower()

    def test_mcp_endpoint_in_base_env_raises(self, tmp_path: Path) -> None:
        config = _pi_config()
        with pytest.raises(UnsupportedMcpTransportError) as excinfo:
            PiRuntimeResolver().resolve(
                config,
                extra_env=None,
                workspace_path=tmp_path,
                base_env={MCP_ENDPOINT_ENV: "http://localhost:9999/mcp"},
            )
        assert "pi" in str(excinfo.value).lower()
