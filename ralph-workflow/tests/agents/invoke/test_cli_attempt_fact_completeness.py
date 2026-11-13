from __future__ import annotations

import atexit
import shlex
import shutil
import tempfile
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import ProcessLineReader, ProcessReaderCtx, check_process_result
from ralph.agents.parsers import AgentParser, get_parser, list_parser_keys
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:
    from ralph.agents.invoke.opencode_subagent_sessions import OpenCodeChildPart
    from ralph.recovery.attempt_facts import AttemptFacts

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DISPLAY_FIXTURES = _REPO_ROOT / "tests" / "display" / "_fixtures"
_CLAUDE_INTERACTIVE_CAPTURE = (
    _REPO_ROOT
    / "tests"
    / "agents"
    / "invoke"
    / "fixtures"
    / "claude_interactive_real_capture"
    / "-home-mistlight-Projects-Expeditions-Core-wt-02-contractor"
    / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"
)

_COMMANDS: dict[str, str] = {
    "agy": "agy",
    "claude": "claude -p",
    "claude_interactive": "claude",
    "codex": "codex exec",
    "cursor": "agent",
    "gemini": "gemini",
    "generic": "generic",
    "kimi": "kimi",
    "nanocoder": "nanocoder",
    "opencode": "opencode",
    "pi": "pi",
}

_REGISTRY_KEYS: tuple[str, ...] = list_parser_keys()

_CAPTURES: dict[str, Path | None] = {
    "agy": _DISPLAY_FIXTURES / "agy_wire_tool.jsonl",
    "claude": _DISPLAY_FIXTURES / "claude_wire.jsonl",
    "claude_interactive": _CLAUDE_INTERACTIVE_CAPTURE,
    "codex": _DISPLAY_FIXTURES / "codex_wire.jsonl",
    "cursor": _DISPLAY_FIXTURES / "cursor_wire.jsonl",
    "gemini": _DISPLAY_FIXTURES / "gemini_wire.jsonl",
    "generic": _DISPLAY_FIXTURES / "generic_wire.jsonl",
    "kimi": None,
    "nanocoder": _DISPLAY_FIXTURES / "nanocoder_wire.jsonl",
    "opencode": _DISPLAY_FIXTURES / "opencode_wire.jsonl",
    "pi": _DISPLAY_FIXTURES / "pi_wire.jsonl",
}

_FACT_FIELDS: tuple[str, ...] = (
    "event_kinds",
    "tool_calls",
    "liveness_observed",
    "termination",
)

_GAP_ROWS: tuple[tuple[str, str, str], ...] = (
    ("kimi", "capture", "no recorded capture"),
    (
        "opencode",
        "subagent_activity_observed",
        "no recorded task/child-session capture (opencode_wire_provenance.md:47-52);"
        " populated by the store probe, not the wire",
    ),
    (
        "agy",
        "tool_calls",
        "agy step_update tool frames name the tool under step_update.tool_info,"
        " which extract_tool_call_from_activity_signal does not unwrap",
    ),
    (
        "agy",
        "subagent_activity_observed",
        "agy_wire_tool.jsonl carries no subagent step; subagent activity is"
        " populated by the child-liveness seam, not step_update frames",
    ),
    (
        "claude",
        "tool_calls",
        "claude_wire.jsonl emits content_block_start tool_use frames whose tool"
        " name extract_tool_call_from_activity_signal does not unwrap",
    ),
    (
        "claude",
        "subagent_activity_observed",
        "claude_wire.jsonl carries no task/subagent frame",
    ),
    (
        "claude_interactive",
        "subagent_activity_observed",
        "parent transcript lines classify as tool_use; interactive subagent"
        " activity is populated by the PTY transcript-tail seam, not the"
        " stdout wire",
    ),
    (
        "codex",
        "tool_calls",
        "codex runs GenericExecutionStrategy, which classifies every captured line as output_line",
    ),
    (
        "codex",
        "subagent_activity_observed",
        "codex_wire.jsonl carries no subagent frame",
    ),
    (
        "cursor",
        "subagent_activity_observed",
        "cursor_wire.jsonl carries no subagent frame",
    ),
    (
        "gemini",
        "tool_calls",
        "gemini runs GenericExecutionStrategy, which classifies every captured line as output_line",
    ),
    (
        "gemini",
        "subagent_activity_observed",
        "gemini_wire.jsonl carries no subagent frame",
    ),
    (
        "generic",
        "tool_calls",
        "the committed generic capture contains no tool call",
    ),
    (
        "generic",
        "subagent_activity_observed",
        "generic_wire.jsonl carries no subagent frame",
    ),
    (
        "nanocoder",
        "tool_calls",
        "nanocoder runs GenericExecutionStrategy, which classifies every"
        " captured line as output_line",
    ),
    (
        "nanocoder",
        "subagent_activity_observed",
        "nanocoder_wire.jsonl carries no subagent frame",
    ),
    (
        "pi",
        "tool_calls",
        "the pi capture's toolcall_end frames classify as tool_result; tool"
        " names are only extracted from tool_use signals",
    ),
    (
        "pi",
        "subagent_activity_observed",
        "pi_wire.jsonl carries no subagent frame",
    ),
)


def _transport_for(parser_key: str) -> AgentTransport:
    if parser_key == "gemini":
        return AgentTransport.GENERIC
    return AgentTransport(parser_key)


class _NoChildPartSource:
    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del parent_session_id, since_ms
        return []

    def close(self) -> None:
        return


class _ExitedHandle:
    stdin = None

    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.pid = None
        self.returncode = 0

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


@cache
def _facts_for(parser_key: str) -> AttemptFacts:
    capture = _CAPTURES[parser_key]
    if capture is None:
        msg = f"{parser_key}: no recorded capture; record a gap row instead"
        raise AssertionError(msg)
    lines = capture.read_text(encoding="utf-8").splitlines(keepends=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"ralph-u3-{parser_key}-"))
    atexit.register(shutil.rmtree, workspace, True)
    clock = FakeClock(start=0.0)
    handle = _ExitedHandle(lines)
    reader = ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd=_COMMANDS[parser_key], transport=_transport_for(parser_key)),
            policy=TimeoutPolicy(
                idle_timeout_seconds=300.0,
                drain_window_seconds=10.0,
                idle_poll_interval_seconds=1.0,
                max_session_seconds=None,
            ),
            execution_strategy=strategy_for_transport(_transport_for(parser_key)),
            liveness_probe=FakeLivenessProbe(active=False),
            opencode_child_part_source=_NoChildPartSource(),
            workspace_path=workspace,
        ),
        clock,
    )
    list(reader.read_lines())
    check_process_result(
        handle,
        shlex.split(_COMMANDS[parser_key])[0],
        termination_recorder=reader.termination_recorder,
    )
    return reader.termination_recorder.facts()


def _populated_fields(facts: AttemptFacts) -> set[str]:
    populated: set[str] = set()
    if facts.event_kinds:
        populated.add("event_kinds")
    if facts.tool_calls:
        populated.add("tool_calls")
    if facts.liveness_observed is not None:
        populated.add("liveness_observed")
    if facts.termination.log:
        populated.add("termination")
    if facts.subagent_activity_observed is True:
        populated.add("subagent_activity_observed")
    return populated


def _param_id(parser_key: str) -> str:
    return "antigravity" if parser_key == "agy" else parser_key


@pytest.mark.parametrize(
    "parser_key",
    _REGISTRY_KEYS,
    ids=[_param_id(key) for key in _REGISTRY_KEYS],
)
def test_recorded_capture_populates_attempt_facts(parser_key: str) -> None:
    capture = _CAPTURES[parser_key]
    if capture is None:
        assert (parser_key, "capture", "no recorded capture") in _GAP_ROWS
        return
    assert capture.is_file(), f"missing recorded capture: {capture}"
    facts = _facts_for(parser_key)
    gap_keys = {(key, field) for key, field, _reason in _GAP_ROWS}
    populated = _populated_fields(facts)
    for field in (*_FACT_FIELDS, "subagent_activity_observed"):
        if (parser_key, field) in gap_keys:
            assert field not in populated, (
                f"{parser_key}: {field} is recorded as a gap but the replay"
                " populated it; remove the gap row"
            )
        else:
            assert field in populated, (
                f"{parser_key}: {field} not populated from {capture.name};"
                " record a captured replay or a gap row"
            )


def test_gap_tables_equal_replayed_gaps() -> None:
    registry_keys = list(list_parser_keys())
    assert set(_CAPTURES) == set(registry_keys)
    assert set(_COMMANDS) == set(registry_keys)
    for parser_key in registry_keys:
        assert isinstance(get_parser(parser_key), AgentParser)
    computed: set[tuple[str, str]] = set()
    for parser_key in registry_keys:
        capture = _CAPTURES[parser_key]
        if capture is None:
            computed.add((parser_key, "capture"))
            continue
        populated = _populated_fields(_facts_for(parser_key))
        for field in (*_FACT_FIELDS, "subagent_activity_observed"):
            if field not in populated:
                computed.add((parser_key, field))
    declared = {(key, field) for key, field, _reason in _GAP_ROWS}
    assert declared == computed
    assert len(declared) == len(_GAP_ROWS), "duplicate (key, field) gap rows"
    assert all(key in set(registry_keys) for key, _field, _reason in _GAP_ROWS)
