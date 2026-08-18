"""Fast default-gate regression for the AGY parser-to-harness pipeline (S-4).

The slow, real-binary end-to-end proof is the default-gate
``tests/test_smoke_agy_full_lifecycle_e2e.py`` (re-included into
``make test`` via ``REQUIRED_AUTO_INTEGRATE_E2E_FILES``). This module is the
sub-5 s, subprocess-free complement: it replays the measured v1.1.13 wire
capture (``tests/display/_fixtures/agy_wire_v1_1_13.jsonl`` -- see
``agy_wire_provenance.md`` in the same directory) through ``AgyParser`` and
through ``ralph.pipeline.plumbing.smoke_plumbing._meaningful_output_lines``
so a parser or harness-whitelist regression is caught by default CI without
spawning a mock binary.

The v1.1.13 capture is bodiless for ``system_message`` steps, so the
syntax-highlight contract is pinned with one synthetic payload-carrying
frame (the same shape ``tests/test_agy_syntax_highlighting.py`` uses),
clearly labeled as synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.parsers.agy import AgyParser
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.smoke_plumbing import (
    _meaningful_output_lines,
)

_FIXTURE = (
    Path(__file__).parent / "display" / "_fixtures" / "agy_wire_v1_1_13.jsonl"
)
_WHITELIST = {"text", "thinking", "tool_use", "tool_result", "error"}


def _fixture_lines() -> list[str]:
    return _FIXTURE.read_text(encoding="utf-8").splitlines()


def _agy_config() -> AgentConfig:
    return AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def test_v1_1_13_fixture_replay_emits_the_measured_five_event_types() -> None:
    """Every event type the v1.1.13 wire actually carries reaches the parser stream.

    The measured capture produces exactly {text, tool_use, tool_result,
    lifecycle, stop}; it carries no ``error`` or ``thinking`` frame, so the
    assertion deliberately stops at the fixture's measured vocabulary.
    """
    events = list(AgyParser().parse(iter(_fixture_lines())))
    types = [event.type for event in events]
    for expected in ("text", "tool_use", "tool_result", "lifecycle", "stop"):
        assert expected in types, f"{expected!r} missing from {types}"


def test_v1_1_13_fixture_flows_through_meaningful_output_lines() -> None:
    """The fixture's whitelisted content survives the harness output filter.

    ``_meaningful_output_lines`` keeps only {text, thinking, tool_use,
    tool_result, error} prefixed lines in parser emission order; the
    ``lifecycle`` and ``stop`` events the fixture also emits are excluded by
    documented contract.
    """
    lines = _fixture_lines()
    events = list(AgyParser().parse(iter(lines)))
    meaningful = _meaningful_output_lines(_agy_config(), lines)

    assert meaningful, "fixture text/tool content must survive the whitelist"
    prefixes: list[str] = []
    for entry in meaningful:
        prefix, sep, content = entry.partition(": ")
        assert sep, f"line lacks '<type>: <content>' shape: {entry!r}"
        assert prefix in _WHITELIST, f"non-whitelist prefix {prefix!r}"
        assert content, f"empty content in {entry!r}"
        prefixes.append(prefix)

    expected_prefixes = [
        event.type for event in events if event.type in _WHITELIST and event.content.strip()
    ]
    # The harness caps output at _MAX_MEANINGFUL_OUTPUT_LINES; the returned
    # prefixes must be a bounded prefix of the parser stream in emission order.
    assert prefixes == expected_prefixes[: len(prefixes)]
    assert 0 < len(prefixes) <= len(expected_prefixes)
    assert "lifecycle" not in prefixes
    assert "stop" not in prefixes


def test_synthetic_fenced_system_message_highlight_stays_off_meaningful_lines() -> None:
    """The syntax-highlight metadata travels on the parser event, not the harness line.

    Synthetic frame (the v1.1.13 capture is bodiless for ``system_message``):
    the parser must emit a ``text`` event with ``syntax_highlight is True``
    and the resolved language alias; the harness whitelist then renders it
    as an ordinary ``text:`` line -- no highlight-specific surface.
    """
    frame = {
        "event": "step_update",
        "step_update": {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": "Here is the snippet:\n```python\nprint('hello')\n```\n",
        },
    }
    raw = json.dumps(frame)
    events = list(AgyParser().parse(iter([raw])))
    text_events = [event for event in events if event.type == "text"]
    assert len(text_events) == 1, f"expected one text event, got {events}"
    event = text_events[0]
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "python"

    meaningful = _meaningful_output_lines(_agy_config(), [raw])
    assert len(meaningful) == 1
    assert meaningful[0].startswith("text: ")
    assert "syntax_highlight" not in meaningful[0]
