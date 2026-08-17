"""Interactive agent startup/configuration error handling tests."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import ClaudeInteractiveCommandBuilder
from ralph.agents.invoke._commands import _interactive_stop_hook_settings
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from tests._support.typed_accessors import must_bool, must_dict_list, must_mapping, must_str


class _RecordingWatchdog:
    def evaluate(self, *, classify_quiet: object) -> object:
        del classify_quiet
        return WatchdogVerdict.CONTINUE


class _PlainTextStrategy:
    def classify_activity_line(self, line: str) -> None:
        del line

    def observe_line(self, line: str) -> None:
        del line


def _build_nanocoder_reader() -> PtyLineReader:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = SimpleNamespace(
        master_fd=master_fd,
        pid=None,
        terminate=lambda grace_period_s=None: None,
        poll=lambda: None,
    )
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=_PlainTextStrategy(),
        liveness_probe=None,
        waiting_listener=None,
    )
    try:
        return PtyLineReader(handle, "nanocoder", ctx, FakeClock(start=0.0), extras=None)
    finally:
        os.close(master_fd)


def _hook_command_source(command: str) -> str:
    """Extract the ``-c`` source from a ``python -c <source>`` hook command."""
    tokens = shlex.split(command)
    assert len(tokens) == 3
    assert tokens[1] == "-c"
    return tokens[2]


def test_claude_interactive_settings_envelope_pins_required_hooks(tmp_path: Path) -> None:
    """The ``--settings`` payload pins the Claude Code hook envelope.

    Future Claude Code settings-schema breakage — the ``Stop`` hook no
    longer writing the sentinel file, the ``PermissionRequest`` hook no
    longer emitting the always-allow JSON envelope, or the
    ``skipDangerousModePermissionPrompt`` flag disappearing — surfaces
    as this focused failure instead of a silent production regression.
    """
    sentinel = tmp_path / "ralph-claude-interactive-sess-envelope.done"
    settings_json = _interactive_stop_hook_settings(sentinel)

    argv = ClaudeInteractiveCommandBuilder().build(
        AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        "PROMPT.md",
        options=BuildCommandOptions(settings_json=settings_json),
    )

    settings_index = argv.index("--settings")
    settings = must_mapping(json.loads(must_str(argv[settings_index + 1])))

    assert must_bool(settings["skipDangerousModePermissionPrompt"]) is True

    hooks = must_mapping(settings["hooks"], field="hooks")

    stop_entry = must_mapping(
        must_dict_list(hooks["Stop"], field="hooks.Stop")[0], field="hooks.Stop[0]"
    )
    stop_hook = must_mapping(
        must_dict_list(stop_entry["hooks"], field="hooks.Stop[0].hooks")[0],
        field="hooks.Stop[0].hooks[0]",
    )
    assert stop_hook["type"] == "command"
    stop_match = re.search(r"Path\((.*)\)\.touch", _hook_command_source(must_str(stop_hook["command"])))
    assert stop_match is not None
    assert ast.literal_eval(stop_match.group(1)) == str(sentinel)

    permission_entry = must_mapping(
        must_dict_list(hooks["PermissionRequest"], field="hooks.PermissionRequest")[0],
        field="hooks.PermissionRequest[0]",
    )
    permission_hook = must_mapping(
        must_dict_list(permission_entry["hooks"], field="hooks.PermissionRequest[0].hooks")[0],
        field="hooks.PermissionRequest[0].hooks[0]",
    )
    assert permission_hook["type"] == "command"
    permission_match = re.search(
        r"json\.dumps\((.*), separators=",
        _hook_command_source(must_str(permission_hook["command"])),
    )
    assert permission_match is not None
    assert ast.literal_eval(permission_match.group(1)) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def test_nanocoder_provider_error_line_raises_agent_invocation_error() -> None:
    """Nanocoder startup/config errors must fail fast instead of waiting for idle timeout."""
    reader = _build_nanocoder_reader()
    line = "Provider 'minimax' not found in agents.config.json. Available providers: \n"
    iterator = reader._handle_queued_line(line, _RecordingWatchdog())

    assert next(iterator) == line
    with pytest.raises(AgentInvocationError) as exc_info:
        next(iterator)

    assert exc_info.value.agent_name == "nanocoder"
    assert exc_info.value.returncode == 1
    assert "Provider 'minimax' not found in agents.config.json" in str(exc_info.value)
    assert exc_info.value.parsed_output == [line.strip()]
