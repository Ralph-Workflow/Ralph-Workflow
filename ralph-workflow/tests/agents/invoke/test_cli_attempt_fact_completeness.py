from __future__ import annotations

import atexit
import shlex
import shutil
import tempfile
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import PostExitVerdict, TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AttemptEnded,
    ProcessLineReader,
    ProcessReaderCtx,
    check_process_result,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    PostExitHangObserved,
    ProviderErrorObserved,
    SilenceObserved,
)
from ralph.agents.parsers import AgentParser, get_parser, list_parser_keys
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.manager import ManagedProcess
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.end_attempt import end_attempt

if TYPE_CHECKING:
    from ralph.agents.invoke.opencode_subagent_sessions import OpenCodeChildPart
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
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


@dataclass(frozen=True)
class _Shape:
    name: str
    returncode: int
    stderr_line: str | None
    supervisor_timing: str | None
    attempt_failed: bool
    artifact_valid: bool
    expected_kind: AttemptOutcomeKind
    expected_issuer: TerminationIssuer | None
    expected_liveness: bool | None


_SHAPES: tuple[_Shape, ...] = (
    _Shape("healthy", 0, None, None, False, True, AttemptOutcomeKind.COMPLETED, None, False),
    _Shape("nonzero-exit", 1, None, None, True, False, AttemptOutcomeKind.UNKNOWN, None, None),
    _Shape(
        "stderr-quota-line",
        1,
        "quota exceeded",
        "provider-error",
        True,
        False,
        AttemptOutcomeKind.RUNTIME_ENDED,
        TerminationIssuer.QUOTA_DETECTOR,
        True,
    ),
    _Shape(
        "auth-error",
        1,
        "ralph-test-harness-auth-marker: 401 unauthorized",
        None,
        True,
        False,
        AttemptOutcomeKind.UNKNOWN,
        None,
        None,
    ),
    _Shape(
        "killed",
        0,
        None,
        "after-reader",
        True,
        False,
        AttemptOutcomeKind.RUNTIME_ENDED,
        TerminationIssuer.SUPERVISOR_IDLE,
        True,
    ),
    _Shape(
        "descendant-hang",
        0,
        None,
        "after-parent-exit",
        True,
        False,
        AttemptOutcomeKind.RUNTIME_ENDED,
        TerminationIssuer.POST_EXIT_WATCHDOG,
        True,
    ),
)

_RECORDED_SHAPE_GAPS: tuple[tuple[str, str, str], ...] = tuple(
    (
        parser_key,
        shape.name,
        f"missing real {shape.name} capture for {parser_key}",
    )
    for parser_key in _REGISTRY_KEYS
    for shape in _SHAPES
    if parser_key == "kimi" or shape.name != "healthy"
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
        "subagent_activity_observed",
        "agy_wire_tool.jsonl carries no subagent step; subagent activity is"
        " populated by the child-liveness seam, not step_update frames",
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


class _NoopProcessTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label


class _ProcessOutput:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)
        self.exhausted = False

    def __iter__(self) -> _ProcessOutput:
        return self

    def __next__(self) -> str:
        try:
            return next(self._lines)
        except StopIteration:
            self.exhausted = True
            raise


class _ExitedHandle(ManagedProcess):
    def __init__(self, lines: list[str], *, returncode: int, stderr_line: str | None) -> None:
        self._stdout = _ProcessOutput(lines)
        self._ralph_bounded_stderr = "" if stderr_line is None else f"{stderr_line}\n"
        self._final_returncode = returncode
        self._signalled_returncode: int | None = None
        self._exit_observed = False
        self._termination_issuer: str | None = None

    @property
    def stdin(self) -> None:
        return None

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self) -> None:
        return None

    @property
    def pid(self) -> int:
        return 1

    @property
    def returncode(self) -> int | None:
        return self.poll()

    def terminate(
        self,
        grace_period_s: float | None = None,
        *,
        issuer: object | None = None,
        **_kwargs: object,
    ) -> None:
        del grace_period_s
        self._termination_issuer = str(issuer) if issuer is not None else None
        self._signalled_returncode = -15

    def kill(self, *, issuer: object, **_kwargs: object) -> None:
        self._termination_issuer = str(issuer)
        self._signalled_returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        returncode = self.poll()
        return self._final_returncode if returncode is None else returncode

    def poll(self) -> int | None:
        if self._signalled_returncode is not None:
            return self._signalled_returncode
        return self._final_returncode if self._exit_observed else None

    def observe_exit(self) -> None:
        if not self._stdout.exhausted:
            raise AssertionError("process exit observed before stdout reached EOF")
        self._exit_observed = True

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)

    def termination_issuer(self) -> str | None:
        return self._termination_issuer

    def record_terminal_reason(self, reason: str) -> None:
        self._termination_issuer = reason

    def has_live_descendants(self) -> bool:
        return False


@cache
def _replay_shape(parser_key: str, shape_name: str) -> tuple[AttemptFacts, AttemptOutcomeKind]:
    capture = _CAPTURES[parser_key]
    if capture is None:
        msg = f"{parser_key}: no recorded capture; record a gap row instead"
        raise AssertionError(msg)
    shape = next(item for item in _SHAPES if item.name == shape_name)
    lines = capture.read_text(encoding="utf-8").splitlines(keepends=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"ralph-u3-{parser_key}-"))
    atexit.register(shutil.rmtree, workspace, True)
    clock = FakeClock(start=0.0)
    handle = _ExitedHandle(
        lines,
        returncode=shape.returncode,
        stderr_line=shape.stderr_line,
    )
    process_teardown = _NoopProcessTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=process_teardown)
    reader = ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd=_COMMANDS[parser_key], transport=_transport_for(parser_key)),
            policy=TimeoutPolicy(
                idle_timeout_seconds=300.0,
                drain_window_seconds=10.0,
                idle_poll_interval_seconds=1.0,
                max_session_seconds=None,
                process_monitor_enabled=False,
            ),
            execution_strategy=strategy_for_transport(_transport_for(parser_key)),
            liveness_probe=FakeLivenessProbe(active=False),
            opencode_child_part_source=_NoChildPartSource(),
            process_teardown=process_teardown,
            workspace_path=workspace,
        ),
        clock,
        supervisor=supervisor,
    )
    reader_failure: BaseException | None = None
    try:
        list(reader.read_lines())
    except BaseException as exc:
        reader_failure = exc
    handle.observe_exit()
    if shape.supervisor_timing == "after-reader":
        supervisor.act(SilenceObserved(WatchdogFireReason.STALLED_AFTER_TOOL_RESULT))
    elif shape.supervisor_timing == "after-parent-exit":
        supervisor.act(PostExitHangObserved(PostExitVerdict.FIRE_DESCENDANT_HANG))
    elif shape.supervisor_timing == "provider-error":
        supervisor.act(ProviderErrorObserved(shape.stderr_line or "quota exceeded"))
    completion_failure: BaseException | None = None
    if reader_failure is None:
        try:
            check_process_result(
                handle,
                shlex.split(_COMMANDS[parser_key])[0],
                termination_recorder=reader.termination_recorder,
            )
        except BaseException as exc:
            completion_failure = exc
    failure = reader_failure if reader_failure is not None else completion_failure
    facts = (
        failure.facts if isinstance(failure, AttemptEnded) else reader.termination_recorder.facts()
    )
    facts = replace(facts, artifact_valid=shape.artifact_valid)
    typed_signal = facts.typed_signal
    outcome = end_attempt(
        facts,
        attempt_failed=shape.attempt_failed,
        typed_signal=typed_signal,
    )
    assert outcome.issuer is shape.expected_issuer
    return facts, outcome.kind


def _facts_for(parser_key: str) -> AttemptFacts:
    return _replay_shape(parser_key, "healthy")[0]


def _populated_fields(facts: AttemptFacts) -> set[str]:
    populated: set[str] = set()
    if facts.event_kinds:
        populated.add("event_kinds")
    if facts.tool_calls:
        populated.add("tool_calls")
    if facts.liveness_observed is not None:
        populated.add("liveness_observed")
    if facts.termination is not None and facts.termination.log:
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


@pytest.mark.parametrize(
    "parser_key",
    _REGISTRY_KEYS,
    ids=[_param_id(key) for key in _REGISTRY_KEYS],
)
def test_capture_shape_axis_preserves_facts_and_decides_outcome(parser_key: str) -> None:
    capture = _CAPTURES[parser_key]
    shape_gap_keys = {(key, shape) for key, shape, _reason in _RECORDED_SHAPE_GAPS}
    if capture is None:
        assert all((parser_key, shape.name) in shape_gap_keys for shape in _SHAPES)
        return

    field_gap_keys = {(key, field) for key, field, _reason in _GAP_ROWS}
    for shape in _SHAPES:
        facts, outcome_kind = _replay_shape(parser_key, shape.name)
        populated = _populated_fields(facts)
        assert outcome_kind is shape.expected_kind
        assert facts.liveness_observed is shape.expected_liveness
        for field in ("event_kinds", "tool_calls", "termination"):
            if (parser_key, field) in field_gap_keys:
                assert field not in populated
            else:
                assert field in populated
        if shape.name == "healthy":
            assert (parser_key, shape.name) not in shape_gap_keys
        else:
            assert (parser_key, shape.name) in shape_gap_keys


def test_recorded_shape_gap_rows_cover_only_missing_real_captures() -> None:
    expected = {
        (parser_key, shape.name)
        for parser_key in _REGISTRY_KEYS
        for shape in _SHAPES
        if parser_key == "kimi" or shape.name != "healthy"
    }
    declared = {(key, shape) for key, shape, _reason in _RECORDED_SHAPE_GAPS}
    assert declared == expected
    assert len(declared) == len(_RECORDED_SHAPE_GAPS)
    assert all(
        reason == f"missing real {shape} capture for {key}"
        for key, shape, reason in _RECORDED_SHAPE_GAPS
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
