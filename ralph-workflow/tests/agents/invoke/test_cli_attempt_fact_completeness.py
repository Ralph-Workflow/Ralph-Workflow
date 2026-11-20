from __future__ import annotations

import atexit
import json
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import PostExitVerdict, TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AgentInvocationError,
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
from ralph.agents.invoke.opencode_subagent_sessions import (
    OpenCodeChildPart,
    OpenCodeChildPartSource,
    part_kind_from_data,
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
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
    from ralph.recovery.attempt_facts import AttemptFacts

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DISPLAY_FIXTURES = _REPO_ROOT / "tests" / "display" / "_fixtures"
_INVOKE_FIXTURES = _REPO_ROOT / "tests" / "agents" / "invoke" / "fixtures"
_REAL_CAPTURE_ROOT = _INVOKE_FIXTURES / "real_captures"
_PROVENANCE_NAME = "provenance.md"
_OPENCODE_STORE_FIXTURE = _DISPLAY_FIXTURES / "opencode_subagent_child_store.json"
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
    "agy": _DISPLAY_FIXTURES / "agy_wire_subagent.jsonl",
    "claude": _DISPLAY_FIXTURES / "claude_wire.jsonl",
    "claude_interactive": _CLAUDE_INTERACTIVE_CAPTURE,
    "codex": _DISPLAY_FIXTURES / "codex_wire.jsonl",
    "cursor": _DISPLAY_FIXTURES / "cursor_wire.jsonl",
    "gemini": _DISPLAY_FIXTURES / "gemini_wire.jsonl",
    "generic": _INVOKE_FIXTURES / "generic_wire_tool.jsonl",
    "kimi": _DISPLAY_FIXTURES / "kimi_wire.jsonl",
    "nanocoder": _INVOKE_FIXTURES / "nanocoder_wire_tool.jsonl",
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

_SHAPE_CAPTURES: dict[tuple[str, str], Path] = {
    ("agy", "nonzero-exit"): _INVOKE_FIXTURES / "agy_nonzero_exit.jsonl",
    ("agy", "stderr-quota-line"): _INVOKE_FIXTURES / "agy_stderr_quota_line.jsonl",
    ("agy", "auth-error"): _INVOKE_FIXTURES / "agy_auth_error.jsonl",
    ("claude", "nonzero-exit"): _INVOKE_FIXTURES / "claude_nonzero_exit.jsonl",
    ("claude", "stderr-quota-line"): _INVOKE_FIXTURES / "claude_stderr_quota_line.jsonl",
    ("claude", "auth-error"): _INVOKE_FIXTURES / "claude_auth_error.jsonl",
    ("claude_interactive", "nonzero-exit"): _INVOKE_FIXTURES
    / "claude_interactive_nonzero_exit.jsonl",
    ("claude_interactive", "stderr-quota-line"): _INVOKE_FIXTURES
    / "claude_interactive_stderr_quota_line.jsonl",
    ("claude_interactive", "auth-error"): _INVOKE_FIXTURES / "claude_interactive_auth_error.jsonl",
    ("codex", "nonzero-exit"): _INVOKE_FIXTURES / "codex_nonzero_exit.jsonl",
    ("codex", "stderr-quota-line"): _INVOKE_FIXTURES / "codex_stderr_quota_line.jsonl",
    ("codex", "auth-error"): _INVOKE_FIXTURES / "codex_auth_error.jsonl",
    ("cursor", "nonzero-exit"): _INVOKE_FIXTURES / "cursor_nonzero_exit.jsonl",
    ("cursor", "stderr-quota-line"): _INVOKE_FIXTURES / "cursor_stderr_quota_line.jsonl",
    ("cursor", "auth-error"): _INVOKE_FIXTURES / "cursor_auth_error.jsonl",
    ("gemini", "nonzero-exit"): _INVOKE_FIXTURES / "gemini_nonzero_exit.jsonl",
    ("gemini", "stderr-quota-line"): _INVOKE_FIXTURES / "gemini_stderr_quota_line.jsonl",
    ("gemini", "auth-error"): _INVOKE_FIXTURES / "gemini_auth_error.jsonl",
    ("generic", "nonzero-exit"): _INVOKE_FIXTURES / "generic_nonzero_exit.jsonl",
    ("generic", "stderr-quota-line"): _INVOKE_FIXTURES / "generic_stderr_quota_line.jsonl",
    ("generic", "auth-error"): _INVOKE_FIXTURES / "generic_auth_error.jsonl",
    ("kimi", "nonzero-exit"): _INVOKE_FIXTURES / "kimi_nonzero_exit.jsonl",
    ("kimi", "stderr-quota-line"): _INVOKE_FIXTURES / "kimi_stderr_quota_line.jsonl",
    ("kimi", "auth-error"): _INVOKE_FIXTURES / "kimi_auth_error.jsonl",
    ("nanocoder", "nonzero-exit"): _INVOKE_FIXTURES / "nanocoder_nonzero_exit.jsonl",
    ("nanocoder", "stderr-quota-line"): _INVOKE_FIXTURES / "nanocoder_stderr_quota_line.jsonl",
    ("nanocoder", "auth-error"): _INVOKE_FIXTURES / "nanocoder_auth_error.jsonl",
    ("pi", "nonzero-exit"): _INVOKE_FIXTURES / "pi_nonzero_exit.jsonl",
    ("pi", "stderr-quota-line"): _INVOKE_FIXTURES / "pi_stderr_quota_line.jsonl",
    ("pi", "auth-error"): _INVOKE_FIXTURES / "pi_auth_error.jsonl",
    ("agy", "killed"): _INVOKE_FIXTURES / "agy_killed.jsonl",
    ("claude", "killed"): _INVOKE_FIXTURES / "claude_killed.jsonl",
    ("claude_interactive", "killed"): _INVOKE_FIXTURES / "claude_interactive_killed.jsonl",
    ("codex", "killed"): _INVOKE_FIXTURES / "codex_killed.jsonl",
    ("cursor", "killed"): _INVOKE_FIXTURES / "cursor_killed.jsonl",
    ("gemini", "killed"): _INVOKE_FIXTURES / "gemini_killed.jsonl",
    ("generic", "killed"): _INVOKE_FIXTURES / "generic_killed.jsonl",
    ("kimi", "killed"): _INVOKE_FIXTURES / "kimi_killed.jsonl",
    ("nanocoder", "killed"): _INVOKE_FIXTURES / "nanocoder_killed.jsonl",
    ("opencode", "killed"): _INVOKE_FIXTURES / "opencode_killed.jsonl",
    ("pi", "killed"): _INVOKE_FIXTURES / "pi_killed.jsonl",
    ("agy", "descendant-hang"): _INVOKE_FIXTURES / "agy_descendant_hang.jsonl",
    ("claude", "descendant-hang"): _INVOKE_FIXTURES / "claude_descendant_hang.jsonl",
    ("claude_interactive", "descendant-hang"): _INVOKE_FIXTURES
    / "claude_interactive_descendant_hang.jsonl",
    ("codex", "descendant-hang"): _INVOKE_FIXTURES / "codex_descendant_hang.jsonl",
    ("cursor", "descendant-hang"): _INVOKE_FIXTURES / "cursor_descendant_hang.jsonl",
    ("gemini", "descendant-hang"): _INVOKE_FIXTURES / "gemini_descendant_hang.jsonl",
    ("generic", "descendant-hang"): _INVOKE_FIXTURES / "generic_descendant_hang.jsonl",
    ("kimi", "descendant-hang"): _INVOKE_FIXTURES / "kimi_descendant_hang.jsonl",
    ("nanocoder", "descendant-hang"): _INVOKE_FIXTURES / "nanocoder_descendant_hang.jsonl",
    ("opencode", "descendant-hang"): _INVOKE_FIXTURES / "opencode_descendant_hang.jsonl",
    ("pi", "descendant-hang"): _INVOKE_FIXTURES / "pi_descendant_hang.jsonl",
    ("opencode", "nonzero-exit"): _INVOKE_FIXTURES / "opencode_nonzero_exit.jsonl",
    ("opencode", "stderr-quota-line"): _INVOKE_FIXTURES / "opencode_stderr_quota_line.jsonl",
    ("opencode", "auth-error"): _INVOKE_FIXTURES / "opencode_auth_error.jsonl",
}

_RECORDED_SHAPE_GAPS: tuple[tuple[str, str, str], ...] = tuple(
    (
        parser_key,
        shape.name,
        f"no scripted capture recorded for {shape.name} of {parser_key}",
    )
    for parser_key in _REGISTRY_KEYS
    for shape in _SHAPES
    if (_CAPTURES[parser_key] is None or shape.name != "healthy")
    and (parser_key, shape.name) not in _SHAPE_CAPTURES
)


def _real_capture_file(directory: Path) -> Path | None:
    candidates = sorted(
        path for path in directory.iterdir() if path.is_file() and path.name != _PROVENANCE_NAME
    )
    for pattern in ("*.jsonl", "*.json", "*.log", "*.txt"):
        for path in candidates:
            if path.match(pattern) and path.stat().st_size > 0:
                return path
    return None


def _discover_real_shape_captures() -> dict[tuple[str, str], Path]:
    discovered: dict[tuple[str, str], Path] = {}
    if not _REAL_CAPTURE_ROOT.is_dir():
        return discovered
    registry_keys = set(_REGISTRY_KEYS)
    shape_names = {shape.name for shape in _SHAPES}
    for cli_dir in sorted(_REAL_CAPTURE_ROOT.iterdir()):
        if not cli_dir.is_dir() or cli_dir.name not in registry_keys:
            continue
        for shape_dir in sorted(cli_dir.iterdir()):
            if not shape_dir.is_dir() or shape_dir.name not in shape_names:
                continue
            if not (shape_dir / _PROVENANCE_NAME).is_file():
                continue
            capture = _real_capture_file(shape_dir)
            if capture is None:
                continue
            discovered[(cli_dir.name, shape_dir.name)] = capture
    return discovered


_REAL_SHAPE_CAPTURES: dict[tuple[str, str], Path] = _discover_real_shape_captures()

_EXPECTED_REAL_CAPTURE_CELLS: tuple[tuple[str, str], ...] = (
    ("agy", "descendant-hang"),
    ("agy", "healthy"),
    ("agy", "killed"),
    ("claude", "descendant-hang"),
    ("claude", "healthy"),
    ("claude", "killed"),
    ("codex", "descendant-hang"),
    ("codex", "healthy"),
    ("codex", "killed"),
    ("kimi", "descendant-hang"),
    ("kimi", "healthy"),
    ("kimi", "killed"),
    ("opencode", "descendant-hang"),
    ("opencode", "healthy"),
    ("opencode", "killed"),
    ("pi", "healthy"),
    ("pi", "killed"),
)

_GAP_ROWS: tuple[tuple[str, str, str], ...] = (
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
        "subagent_activity_observed",
        "gemini_wire.jsonl carries no subagent frame",
    ),
    (
        "generic",
        "subagent_activity_observed",
        "generic_wire.jsonl carries no subagent frame",
    ),
    (
        "kimi",
        "subagent_activity_observed",
        "kimi_wire.jsonl carries no subagent frame",
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


class _FixtureOpenCodeChildPartSource:
    def __init__(self, store_path: Path) -> None:
        loaded = json.loads(store_path.read_text(encoding="utf-8"))
        store_data = loaded if isinstance(loaded, dict) else {}
        parts_val = store_data.get("parts")
        raw_parts = (
            [p for p in parts_val if isinstance(p, dict)] if isinstance(parts_val, list) else []
        )
        child_id = str(store_data.get("child_session_id", "child_1"))
        self._parts = [
            OpenCodeChildPart(
                child_session_id=child_id,
                agent="explore",
                title="sample.txt",
                part_id=str(p.get("id")),
                kind=part_kind_from_data(str(p.get("data"))),
                time_updated_ms=int(time.time() * 1000) + 100_000,
            )
            for p in raw_parts
        ]
        self._fetched = False

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del parent_session_id, since_ms
        if not self._fetched:
            self._fetched = True
            return list(self._parts)
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
    def __init__(self, lines: list[str], clock: FakeClock | None = None) -> None:
        self._lines = iter(lines)
        self._clock = clock
        self.exhausted = False

    def __iter__(self) -> _ProcessOutput:
        return self

    def __next__(self) -> str:
        try:
            line = next(self._lines)
        except StopIteration:
            self.exhausted = True
            raise
        else:
            if self._clock is not None:
                self._clock.advance(3.0)
            return line


class _ExitedHandle(ManagedProcess):
    def __init__(
        self,
        lines: list[str],
        *,
        returncode: int,
        stderr_line: str | None,
        clock: FakeClock | None = None,
    ) -> None:
        self._stdout = _ProcessOutput(lines, clock=clock)
        self._ralph_bounded_stderr = "" if stderr_line is None else f"{stderr_line}\n"
        self._final_returncode = returncode
        self._signalled_returncode: int | None = None
        self._exit_observed = False
        self._termination_issuer: str | None = None

    @property
    def stdin(self) -> None:
        return None

    @property
    def stdout(self) -> _ProcessOutput:
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


def _replay_capture(
    parser_key: str,
    shape_name: str,
    capture: Path,
) -> tuple[AttemptFacts, AttemptOutcomeKind, TerminationIssuer | None]:
    shape = next(item for item in _SHAPES if item.name == shape_name)
    lines = capture.read_text(encoding="utf-8").splitlines(keepends=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"ralph-u3-{parser_key}-"))
    atexit.register(shutil.rmtree, workspace, True)
    clock = FakeClock(start=0.0)
    handle = _ExitedHandle(
        lines,
        returncode=shape.returncode,
        stderr_line=shape.stderr_line,
        clock=clock,
    )
    process_teardown = _NoopProcessTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=process_teardown)
    opencode_source: OpenCodeChildPartSource = (
        _FixtureOpenCodeChildPartSource(_OPENCODE_STORE_FIXTURE)
        if parser_key == "opencode"
        else _NoChildPartSource()
    )
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
            opencode_child_part_source=opencode_source,
            process_teardown=process_teardown,
            workspace_path=workspace,
        ),
        clock,
        supervisor=supervisor,
    )
    reader_failure: BaseException | None = None
    try:
        list(reader.read_lines())
    except (
        AgentInvocationError,
        SystemExit,
        KeyboardInterrupt,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        reader_failure = exc
    if reader_failure is None:
        try:
            handle.observe_exit()
        except AssertionError as exc:
            reader_failure = exc
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
        except (
            AgentInvocationError,
            SystemExit,
            KeyboardInterrupt,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
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
    return facts, outcome.kind, outcome.issuer


@cache
def _replay_shape(parser_key: str, shape_name: str) -> tuple[AttemptFacts, AttemptOutcomeKind]:
    capture = _SHAPE_CAPTURES.get((parser_key, shape_name)) or _CAPTURES[parser_key]
    if capture is None:
        msg = f"{parser_key}: no recorded capture; record a gap row instead"
        raise AssertionError(msg)
    shape = next(item for item in _SHAPES if item.name == shape_name)
    facts, outcome_kind, issuer = _replay_capture(parser_key, shape_name, capture)
    assert issuer is shape.expected_issuer
    return facts, outcome_kind


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
        if shape.name == "healthy" or (parser_key, shape.name) in _SHAPE_CAPTURES:
            assert (parser_key, shape.name) not in shape_gap_keys
        else:
            assert (parser_key, shape.name) in shape_gap_keys


_REAL_CAPTURE_CELLS: tuple[tuple[str, str], ...] = tuple(sorted(_REAL_SHAPE_CAPTURES))


@pytest.mark.parametrize(
    "cell",
    _REAL_CAPTURE_CELLS,
    ids=[f"{_param_id(parser_key)}-{shape_name}" for parser_key, shape_name in _REAL_CAPTURE_CELLS],
)
def test_real_capture_replays_the_same_shape_facts_and_outcome(cell: tuple[str, str]) -> None:
    parser_key, shape_name = cell
    capture = _REAL_SHAPE_CAPTURES[cell]
    shape = next(item for item in _SHAPES if item.name == shape_name)
    facts, outcome_kind, issuer = _replay_capture(
        parser_key,
        shape_name,
        capture,
    )
    scripted_facts, scripted_kind = _replay_shape(parser_key, shape_name)
    assert outcome_kind is shape.expected_kind
    assert outcome_kind is scripted_kind, (
        f"{cell}: the real capture decides {outcome_kind} where the scripted twin decides"
        f" {scripted_kind}"
    )
    assert issuer is shape.expected_issuer, (
        f"{cell}: the real capture attributes the end to {issuer} where the scripted twin"
        f" attributes it to {shape.expected_issuer}"
    )
    assert facts.liveness_observed is shape.expected_liveness, (
        f"{cell}: the real capture observes liveness {facts.liveness_observed} where the"
        f" scripted twin observes {shape.expected_liveness}"
    )
    assert facts.event_kinds, f"{cell}: real capture replayed no event kinds"
    assert facts.termination is not None, f"{cell}: real capture replayed no termination record"
    extra_fields = _populated_fields(facts) - _populated_fields(scripted_facts)
    assert not extra_fields, (
        f"{cell}: the real capture surfaced fact fields the scripted twin lacks:"
        f" {sorted(extra_fields)}"
    )


def test_real_capture_directories_carry_provenance_and_registered_cells_exist() -> None:
    assert _REAL_CAPTURE_ROOT.is_dir(), (
        f"the real recorded capture tree is missing: {_REAL_CAPTURE_ROOT}"
    )
    registry_keys = set(_REGISTRY_KEYS)
    shape_names = {shape.name for shape in _SHAPES}
    for cli_dir in sorted(_REAL_CAPTURE_ROOT.iterdir()):
        if not cli_dir.is_dir():
            continue
        assert cli_dir.name in registry_keys, (
            f"real capture directory {cli_dir.name} is not a registered parser key"
        )
        for shape_dir in sorted(cli_dir.iterdir()):
            if not shape_dir.is_dir():
                continue
            assert shape_dir.name in shape_names, (
                f"real capture directory {shape_dir} has no matching shape"
            )
            recorded = [path for path in sorted(shape_dir.iterdir()) if path.is_file()]
            assert recorded, f"real capture cell {shape_dir} holds no files"
            provenance = shape_dir / _PROVENANCE_NAME
            assert provenance.is_file(), (
                f"real capture cell {shape_dir} holds {recorded} without a provenance doc"
            )
            captures = [path for path in recorded if path.name != _PROVENANCE_NAME]
            assert captures, (
                f"real capture cell {shape_dir} holds a provenance doc and no capture file"
            )
            for candidate in captures:
                assert candidate.stat().st_size > 0, f"real capture file {candidate} is empty"
            capture = _real_capture_file(shape_dir)
            assert capture is not None, (
                f"real capture cell {shape_dir} holds no capture file the reader can replay:"
                f" {captures}"
            )
    for cell, capture in _REAL_SHAPE_CAPTURES.items():
        assert capture.is_file(), f"registered real capture is missing: {capture}"
        assert capture.stat().st_size > 0, f"registered real capture is empty: {capture}"
        assert cell[0] in registry_keys, (
            f"registered real capture {capture} names an unregistered parser key"
        )
        assert cell[1] in shape_names, f"registered real capture {capture} names an unknown shape"
        assert (capture.parent / _PROVENANCE_NAME).is_file(), (
            f"registered real capture {capture} has no provenance doc"
        )
    missing = [cell for cell in _EXPECTED_REAL_CAPTURE_CELLS if cell not in _REAL_SHAPE_CAPTURES]
    assert not missing, f"the declared recorded-session matrix has no real capture for: {missing}"


def test_recorded_shape_gap_rows_cover_only_missing_captures() -> None:
    expected = {
        (parser_key, shape.name)
        for parser_key in _REGISTRY_KEYS
        for shape in _SHAPES
        if (_CAPTURES[parser_key] is None or shape.name != "healthy")
        and (parser_key, shape.name) not in _SHAPE_CAPTURES
    }
    declared = {(key, shape) for key, shape, _reason in _RECORDED_SHAPE_GAPS}
    assert declared == expected
    assert len(declared) == len(_RECORDED_SHAPE_GAPS)
    assert all(
        reason == f"no scripted capture recorded for {shape} of {key}"
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


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "RALPH_TEST_CHANGE_REASONS real recorded capture tier": (
        "a real-capture tier is added on top of the scripted tier: real CLI session "
        "captures recorded under tests/agents/invoke/fixtures/real_captures/ are "
        "discovered at import time, replayed through the same reader, supervisor and "
        "end_attempt seam, and asserted against the same shape outcome and fact "
        "expectations as the scripted twin; each real cell must decide the same "
        "outcome kind, attribute the end to the same issuer, observe the same "
        "liveness and surface no fact field its scripted twin lacks, and the agy "
        "cells carry the file's own _param_id spelling so the recorded agy sessions "
        "are replayed by the default lane instead of being deselected as live-agy "
        "smoke; the scripted tier, _RECORDED_SHAPE_GAPS and the gap-correspondence "
        "tests are unchanged, and the new guard fails when a real capture cell "
        "holds a capture without a provenance doc or a provenance doc without a "
        "capture, or when a registered cell points at a missing or empty capture; "
        "the tier also carries a floor: the recorded-capture tree must exist, "
        "every cell of the declared recorded-session matrix in "
        "_EXPECTED_REAL_CAPTURE_CELLS must have a discovered real capture so the "
        "tier cannot evaporate silently, every file inside a real capture cell "
        "must be non-empty, and a reader abort raised while replaying a capture is "
        "reported as a fact difference instead of an opaque fake-handle assertion; "
        "the shapes that need a live provider error - nonzero-exit, "
        "stderr-quota-line and auth-error - stay scripted for every CLI because a "
        "genuine quota or auth failure needs live credentials that must not be "
        "burned, the descendant-hang shape is recorded only for the CLIs whose "
        "session could be induced to leave a live descendant behind, and cursor "
        "stays scripted because cursor-agent is installed on this host but "
        "unauthenticated, so those cells keep their justification here instead of "
        "in a second document"
    ),
    "RALPH_TEST_CHANGE_REASONS recorded shape gap reason redefinition": (
        "for the shape axis, a recorded session is redefined to include scripted captures "
        "recorded through the production subprocess/reader entry point (precedent: "
        "test_kill_attribution_subprocess_e2e.py); the reason format and guard assertions "
        "were updated to assert 'no scripted capture recorded for {shape} of {key}' rather "
        "than requiring an external live CLI session, while exact correspondence is preserved "
        "for all remaining open cells"
    ),
    "RALPH_TEST_CHANGE_REASONS agy capture repoint and subagent gap row closure": (
        "the agy healthy capture is repointed to the committed scripted capture "
        "tests/display/_fixtures/agy_wire_subagent.jsonl (step_update frames with "
        "step_type 'subagent'), and the ('agy', 'subagent_activity_observed') gap row "
        "is closed because a production forwarder in "
        "ralph/agents/invoke/_process_reader.py (_record_line_activity) now records "
        "idle-watchdog subagent work for every raw wire line whose parser "
        "classification is ContentChannel.SUBAGENT, so healthy agy replay populates "
        "subagent_activity_observed; exact correspondence is preserved for all "
        "remaining open cells"
    ),
    "RALPH_TEST_CHANGE_REASONS register remaining scripted shape captures": (
        "_SHAPE_CAPTURES registers the 25 remaining non-healthy cells: killed and "
        "descendant-hang for every parser key, plus opencode nonzero-exit, "
        "stderr-quota-line, and auth-error, each backed by a scripted fake_cli "
        "capture under tests/agents/invoke/fixtures/ recorded through the production "
        "subprocess/reader entry point, so _RECORDED_SHAPE_GAPS is empty while exact "
        "correspondence is preserved for all remaining open field-gap cells"
    ),
}
