"""Cursor embeds a per-call id INSIDE ``args``, which must never be fingerprinted.

Verified against a live ``cursor-agent --output-format stream-json`` run: a
shell call's ``args`` carries ``toolCallId`` (unique per call) and
``conversationId`` alongside the actual ``command``. Leaving them in made two
byte-identical ``ls -la`` calls look like two different calls, so
REPEATED_IDENTICAL_TOOL_CALL was structurally unreachable on this transport.
"""

from __future__ import annotations

import json

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke._tool_call_extraction import extract_tool_call_from_activity_signal
from ralph.config.enums import AgentTransport


def _shell_call_line(command: str, *, call_id: str) -> str:
    return json.dumps(
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "shellToolCall": {
                    "args": {
                        "command": command,
                        "workingDirectory": "/repo",
                        "timeout": 30000,
                        "toolCallId": call_id,
                        "conversationId": "conv_1",
                    }
                },
                "toolCallId": call_id,
            },
        }
    )


def test_identical_shell_calls_share_one_fingerprint() -> None:
    strategy = strategy_for_transport(AgentTransport.CURSOR)

    fingerprints = set()
    for index in range(3):
        signal = strategy.classify_activity_line(_shell_call_line("ls -la", call_id=f"t_{index}"))
        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        fingerprints.add(json.dumps(extracted, sort_keys=True))

    assert len(fingerprints) == 1


def test_different_shell_commands_stay_distinct() -> None:
    """Stripping per-call ids must not also collapse genuinely different calls."""
    first = extract_tool_call_from_activity_signal(_shell_call_line("ls -la", call_id="t_1"))
    second = extract_tool_call_from_activity_signal(_shell_call_line("pwd", call_id="t_2"))

    assert first != second
    assert first == ("shellToolCall", {"command": "ls -la", "workingDirectory": "/repo",
                                       "timeout": 30000})
