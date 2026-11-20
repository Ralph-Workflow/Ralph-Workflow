from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol

import pytest
from loguru import logger

from ralph.agents.invoke import AttemptEnded, BuildCommandOptions, build_command
from ralph.config.agent_config import AgentConfig
from ralph.git.errors import GitOperationError
from ralph.git.operations import find_repo_root
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    EntryPointRunner,
    registered_runners,
)
from ralph.process.manager import ProcessStatus
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    CANARY_PROBE_PHASE,
    CANARY_PROMPT_FILENAME,
    DEFAULT_PROBE_TIMEOUT_S,
    PROBE_INCONCLUSIVE_VERDICT,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)
from ralph.recovery.dead_agent_probe import build_dead_agent_probe_runner
from ralph.workspace.fs import FsWorkspace

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_dead_agent_probe_surfaces_the_probe_reason_for_the_post_mortem": (
        "build_dead_agent_probe_runner widened to return the richer ProbeResult carrier "
        "carrying .outcome and .reason; the assertion asserts result.outcome is ProbeOutcome.INCONCLUSIVE "
        "and result.reason matches the expected probe reason"
    ),
    "test_probe_removes_the_scratch_directory_when_the_first_delete_fails_transiently": (
        "the scratch removal now retries a transiently failed delete a bounded number of times, "
        "so the new test asserts the scratch directory is gone after a first delete call that "
        "raises OSError, instead of the removal giving up on the first failure"
    ),
}

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
    from ralph.recovery.controller import RecoveryController


class _FakeInvoke(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None: ...


@dataclass(frozen=True)
class _Call:
    config: AgentConfig
    prompt_file: str
    options: InvokeOptions | None


@dataclass(frozen=True)
class _Case:
    name: str
    behaviour: str
    expected: ProbeOutcome
    expected_detail: str
    expected_reason: str
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S
    budget_exhausted: bool = False


_CASES: tuple[_Case, ...] = (
    _Case(
        name="marker_written",
        behaviour="write_marker",
        expected=ProbeOutcome.PASSED,
        expected_detail="marker_written",
        expected_reason="",
    ),
    _Case(
        name="marker_missing_without_evidence",
        behaviour="no_marker",
        expected=ProbeOutcome.INCONCLUSIVE,
        expected_detail="probe_could_not_run",
        expected_reason="",
    ),
    _Case(
        name="infrastructure_error",
        behaviour="raise",
        expected=ProbeOutcome.INCONCLUSIVE,
        expected_detail="probe_could_not_run",
        expected_reason="invocation_error",
    ),
    _Case(
        name="timeout",
        behaviour="block",
        expected=ProbeOutcome.INCONCLUSIVE,
        expected_detail="probe_could_not_run",
        expected_reason="probe_timeout",
        timeout_s=0.1,
    ),
    _Case(
        name="budget_exhausted",
        behaviour="write_marker",
        expected=ProbeOutcome.INCONCLUSIVE,
        expected_detail="probe_could_not_run",
        expected_reason="budget_exhausted",
        budget_exhausted=True,
    ),
)


def _agent_config() -> AgentConfig:
    return AgentConfig(cmd="fake-agent", model="fake-model")


def _make_invoke(behaviour: str, calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        if behaviour == "block":
            threading.Event().wait(1.0)
            return iter(())
        if behaviour == "raise":
            raise RuntimeError("infrastructure failure")
        if behaviour == "write_marker":
            assert options is not None
            workspace_path = options.workspace_path
            assert workspace_path is not None
            (workspace_path / CANARY_MARKER_FILENAME).write_text(
                CANARY_MARKER_CONTENT, encoding="utf-8"
            )
        return iter(())

    return _invoke


def _unreadable_marker_invoke(calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        (workspace_path / CANARY_MARKER_FILENAME).mkdir()
        return iter(())

    return _invoke


def _marker_then_raise_invoke(calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        raise RuntimeError("the cli exited after writing the marker")

    return _invoke


def _marker_then_block_invoke(calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        threading.Event().wait(1.0)
        return iter(())

    return _invoke


def _gated_invoke(gate: threading.Event, calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        gate.wait(timeout=60)
        return iter(())

    return _invoke


def _spawn_reporting_invoke(gate: threading.Event, calls: list[_Call], pid: int) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        on_spawn = options.on_spawn
        assert on_spawn is not None
        on_spawn(pid)
        gate.wait(timeout=60)
        return iter(())

    return _invoke


def _attempt_ended_invoke(facts: AttemptFacts, calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Generator[str, None, AttemptEnded]:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        yield ""
        return AttemptEnded(facts=facts)

    return _invoke


def _command_building_invoke(calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        build_command(config, prompt_file, options=BuildCommandOptions())
        return iter(())

    return _invoke


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("case", _CASES, ids=[case.name for case in _CASES])
def test_probe_reports_the_expected_outcome(case: _Case, tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke(case.behaviour, calls),
        workspace_factory=lambda: scratch,
        timeout_s=case.timeout_s,
        budget_exhausted=(lambda: True) if case.budget_exhausted else None,
    )
    assert isinstance(result, ProbeResult)
    assert result.outcome == case.expected
    assert result.detail == case.expected_detail
    assert result.reason == case.expected_reason
    if case.budget_exhausted:
        assert calls == []
        assert result.marker_written is None
        assert result.error is None
    elif result.outcome == ProbeOutcome.INCONCLUSIVE:
        assert result.marker_written is None
    else:
        assert result.marker_written is (result.outcome == ProbeOutcome.PASSED)


def test_probe_uses_a_scratch_workspace_and_leaves_the_phase_workspace_untouched(
    tmp_path: Path,
) -> None:
    phase_workspace = tmp_path / "phase"
    phase_workspace.mkdir()
    (phase_workspace / "keep.txt").write_text("keep", encoding="utf-8")
    before = sorted(entry.name for entry in phase_workspace.iterdir())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        phase_workspace=phase_workspace,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    options = calls[0].options
    assert options is not None
    workspace_path = options.workspace_path
    assert workspace_path is not None
    assert workspace_path == scratch
    assert workspace_path != phase_workspace
    assert not _is_within(workspace_path, phase_workspace)
    assert sorted(entry.name for entry in phase_workspace.iterdir()) == before
    assert (phase_workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (scratch / CANARY_MARKER_FILENAME).read_text(encoding="utf-8") == CANARY_MARKER_CONTENT
    assert (scratch / CANARY_PROMPT_FILENAME).exists()


def test_unreadable_marker_with_agent_activity_is_inconclusive(tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_unreadable_marker_invoke(calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.marker_written is None


def test_probe_hands_the_same_config_and_environment_to_the_invoke_fn(tmp_path: Path) -> None:
    config = AgentConfig(cmd="fake-agent", model="fake-model")
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        config,
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        extra_env={"RALPH_CANARY_ENV": "kept"},
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    received = calls[0]
    assert received.config is config
    assert received.config.cmd == "fake-agent"
    assert received.config.model == "fake-model"
    received_options = received.options
    assert received_options is not None
    assert received_options.extra_env == {"RALPH_CANARY_ENV": "kept"}
    assert received_options.phase == CANARY_PROBE_PHASE
    assert Path(received.prompt_file).parent == scratch


def test_probe_rejects_scratch_elsewhere_inside_phase_workspace(tmp_path: Path) -> None:
    phase_workspace = tmp_path / "phase"
    phase_workspace.mkdir()
    scratch = phase_workspace / "scratch"
    scratch.mkdir()
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        phase_workspace=phase_workspace,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "probe_internal_error"
    assert result.error == "scratch_inside_phase_workspace"
    assert calls == []
    assert scratch.exists()


def test_probe_allows_scratch_in_the_designated_probe_area(tmp_path: Path) -> None:
    phase_workspace = tmp_path / "phase"
    probe_area = phase_workspace / ".agent" / "tmp"
    scratch = probe_area / "canary-probe-fixed"
    scratch.mkdir(parents=True)
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        phase_workspace=phase_workspace,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    assert (scratch / CANARY_MARKER_FILENAME).read_text(encoding="utf-8") == CANARY_MARKER_CONTENT


def test_probe_places_scratch_under_the_trusted_parent_and_removes_it(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        trusted_parent=trusted_parent,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    options = calls[0].options
    assert options is not None
    workspace_path = options.workspace_path
    assert workspace_path is not None
    assert workspace_path.parent == trusted_parent / ".agent" / "tmp"
    assert workspace_path.name.startswith("canary-probe-")
    assert options.permission_prompt_listener is None
    assert not workspace_path.exists()
    assert not (workspace_path / CANARY_MARKER_FILENAME).exists()


def test_probe_fails_closed_when_no_pretrusted_parent_resolves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _failing_find_repo_root(start: Path) -> Path:
        raise RuntimeError("not inside a git repository")

    monkeypatch.setattr("ralph.recovery.canary_probe.find_repo_root", _failing_find_repo_root)
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "no_pretrusted_workspace"
    assert calls == []


def test_probe_never_creates_a_scratch_outside_a_pretrusted_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _failing_find_repo_root(start: Path) -> Path:
        raise RuntimeError("not inside a git repository")

    def _forbidden_mkdtemp(*args: object, **kwargs: object) -> str:
        raise AssertionError("the probe must not create a temporary directory")

    monkeypatch.setattr("ralph.recovery.canary_probe.find_repo_root", _failing_find_repo_root)
    monkeypatch.setattr(tempfile, "mkdtemp", _forbidden_mkdtemp)
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.reason == "no_pretrusted_workspace"
    assert calls == []


def test_probe_outside_a_repository_fails_closed_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(GitOperationError):
        find_repo_root(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "no_pretrusted_workspace"
    assert calls == []


def test_probe_passes_when_the_marker_was_written_before_the_invocation_failed(
    tmp_path: Path,
) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_marker_then_raise_invoke(calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    assert result.marker_written is True
    assert len(calls) == 1


def test_probe_passes_when_the_marker_was_written_before_the_session_timed_out(
    tmp_path: Path,
) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_marker_then_block_invoke(calls),
        workspace_factory=lambda: scratch,
        timeout_s=0.1,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    assert result.marker_written is True
    assert len(calls) == 1


def test_probe_copies_the_runtime_termination_reason_from_the_judge(tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    facts = AttemptFacts(
        event_kinds=("tool_use",),
        artifact_valid=True,
        termination=TerminationRecord.create(
            TerminationIssuer.SUPERVISOR_IDLE,
            observed_returncode=9,
        ),
    )
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_attempt_ended_invoke(facts, calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert len(calls) == 1


def test_probe_refuses_to_launch_a_cli_without_a_headless_selection(tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_command_building_invoke(calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "transport_not_probe_safe"
    assert len(calls) == 1


def test_probe_bounds_the_invoke_session_below_the_outer_join_timeout(tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        timeout_s=5.0,
    )
    assert result.outcome == ProbeOutcome.PASSED
    options = calls[0].options
    assert options is not None
    assert options.idle_timeout_seconds is not None
    assert options.max_session_seconds is not None
    assert options.idle_timeout_seconds == options.max_session_seconds
    assert options.idle_timeout_seconds < 5.0


def test_probe_keeps_the_scratch_directory_while_a_timed_out_session_may_own_it(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("block", calls),
        trusted_parent=trusted_parent,
        timeout_s=0.1,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.reason == "probe_timeout"
    surviving = list((trusted_parent / ".agent" / "tmp").glob("canary-probe-*"))
    assert len(surviving) == 1


def test_probe_removes_the_scratch_directory_once_the_timed_out_session_ends(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    gate = threading.Event()
    calls: list[_Call] = []
    try:
        result = run_canary_probe(
            _agent_config(),
            invoke_fn=_gated_invoke(gate, calls),
            trusted_parent=trusted_parent,
            timeout_s=0.1,
        )
        assert result.outcome == ProbeOutcome.INCONCLUSIVE
        assert result.reason == "probe_timeout"
        surviving = list((trusted_parent / ".agent" / "tmp").glob("canary-probe-*"))
        assert len(surviving) == 1
        scratch = surviving[0]
        gate.set()
        waiter = threading.Event()
        removed = False
        for _ in range(20):
            if not scratch.exists():
                removed = True
                break
            waiter.wait(timeout=0.05)
        assert removed, "the scratch directory must be removed once the session has ended"
    finally:
        gate.set()


def test_probe_removes_scratch_directory_when_process_record_is_terminal_even_if_session_thread_hung(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    gate = threading.Event()
    arrived = threading.Event()

    def _block_on_outcome(message: object) -> None:
        if "canary probe attempt outcome:" in str(message):
            arrived.set()
            gate.wait(timeout=5.0)

    sink_id = logger.add(_block_on_outcome, format="{message}")

    termination = TerminationRecord.create(
        TerminationIssuer.AGENT_EXIT,
        observed_returncode=0,
        reported_returncode=0,
    )
    facts = AttemptFacts(exit_status="0", termination=termination)
    calls: list[_Call] = []
    try:
        result = run_canary_probe(
            _agent_config(),
            invoke_fn=_attempt_ended_invoke(facts, calls),
            trusted_parent=trusted_parent,
            timeout_s=0.1,
        )
        assert arrived.wait(timeout=5.0), "the probe attempt outcome must be reached"
        assert result.outcome == ProbeOutcome.INCONCLUSIVE
        assert result.reason == "probe_timeout"
        surviving = list((trusted_parent / ".agent" / "tmp").glob("canary-probe-*"))
        waiter = threading.Event()
        removed = False
        if not surviving:
            removed = True
        else:
            scratch = surviving[0]
            for _ in range(40):
                if not scratch.exists():
                    removed = True
                    break
                waiter.wait(timeout=0.05)
        assert removed, "the scratch directory must be removed once process record is terminal"
    finally:
        gate.set()
        logger.remove(sink_id)


def test_probe_removes_the_scratch_directory_when_the_first_delete_fails_transiently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    probe_area = trusted_parent / ".agent" / "tmp"
    original_delete = FsWorkspace.delete
    failures: list[Path] = []

    def _delete_failing_once(self: FsWorkspace, path: str, *, recursive: bool = False) -> None:
        target = Path(path).resolve()
        if not failures and target.parent == probe_area.resolve():
            failures.append(target)
            raise OSError("transient scratch removal failure")
        original_delete(self, path, recursive=recursive)

    monkeypatch.setattr(FsWorkspace, "delete", _delete_failing_once)
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        trusted_parent=trusted_parent,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    assert len(failures) == 1
    assert list(probe_area.glob("canary-probe-*")) == []


def test_probe_dispatches_its_attempt_through_the_registered_canary_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _agent_config()
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    dispatched: list[dict[str, object]] = []
    registered = registered_runners()[AgentEntryPoint.CANARY_PROBE]

    def _spy(
        *,
        effect: object | None = None,
        invocation: object | None = None,
        config: UnifiedConfig,
        controller: RecoveryController,
        deps: AttemptRunnerDeps,
        state: object | None = None,
        **execute_kwargs: object,
    ) -> AttemptResult:
        dispatched.append({"invocation": invocation, **execute_kwargs})
        return registered(
            effect=effect,
            invocation=invocation,
            config=config,
            controller=controller,
            deps=deps,
            state=state,
            **execute_kwargs,
        )

    def _fake_registered_runners() -> Mapping[AgentEntryPoint, EntryPointRunner]:
        return {AgentEntryPoint.CANARY_PROBE: _spy}

    monkeypatch.setattr("ralph.recovery.canary_probe.registered_runners", _fake_registered_runners)
    result = run_canary_probe(
        config,
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(dispatched) == 1
    attempt = dispatched[0]
    assert attempt["invocation"] is config
    assert attempt["phase"] == CANARY_PROBE_PHASE
    assert attempt["agent"] == "fake-agent"
    assert Path(str(attempt["prompt_file"])).name == CANARY_PROMPT_FILENAME


def test_probe_result_carries_a_reason_field_with_an_empty_default() -> None:
    result = ProbeResult(outcome=ProbeOutcome.INCONCLUSIVE, detail="marker_missing")
    assert result.reason == ""


def test_dead_agent_probe_surfaces_the_probe_reason_for_the_post_mortem() -> None:
    records: list[str] = []
    sink_id = logger.add(records.append, level="INFO", format="{message}")
    try:
        runner = build_dead_agent_probe_runner(
            lambda name: AgentConfig(cmd="fake-agent"),
            lambda config: ProbeResult(
                outcome=ProbeOutcome.INCONCLUSIVE,
                detail="probe_could_not_run",
                reason="no_pretrusted_workspace",
            ),
        )
        result = runner("development", "fake-agent")
    finally:
        logger.remove(sink_id)

    assert result is not None
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.reason == "no_pretrusted_workspace"
    assert any("no_pretrusted_workspace" in record for record in records), (
        f"the probe reason must reach the post-mortem log (observed records: {records!r})"
    )


def test_inconclusive_maps_to_the_unknown_verdict_hint(tmp_path: Path) -> None:
    assert PROBE_INCONCLUSIVE_VERDICT == "unknown"
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("raise", calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert PROBE_INCONCLUSIVE_VERDICT == "unknown"


def _uninitialized_agent_config() -> AgentConfig:
    return object.__new__(AgentConfig)


def test_probe_never_raises_for_unusable_input(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _uninitialized_agent_config(),
        invoke_fn=_make_invoke("no_marker", []),
        workspace_factory=lambda: scratch,
    )
    assert isinstance(result, ProbeResult)
    assert result.outcome == ProbeOutcome.INCONCLUSIVE


def test_probe_reports_inconclusive_when_the_workspace_cannot_be_created(tmp_path: Path) -> None:
    def _failing_factory() -> Path:
        raise OSError("no scratch space")

    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("no_marker", []),
        workspace_factory=_failing_factory,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.detail == "probe_could_not_run"
    assert result.reason == "probe_internal_error"
    assert result.error is not None


def test_default_invocation_uses_the_raw_agent_invoke_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _agent_config()
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def _fake_invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        return None

    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _fake_invoke)
    result = run_canary_probe(
        config,
        workspace_factory=lambda: scratch,
    )
    assert result.outcome is ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    assert result.marker_written is True
    assert len(calls) == 1
    received = calls[0]
    assert received.config is config
    assert received.prompt_file.endswith(CANARY_PROMPT_FILENAME)
    received_options = received.options
    assert received_options is not None
    assert received_options.workspace_path == scratch


def test_probe_outcome_exposes_exactly_the_three_contract_members() -> None:
    assert [member.value for member in ProbeOutcome] == [
        "passed",
        "failed_reproducing_no_work",
        "inconclusive",
    ]
    assert DEFAULT_PROBE_TIMEOUT_S == 30


def test_canary_probe_regression_scratch_removed_when_the_session_thread_never_ends(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    gate = threading.Event()
    calls: list[_Call] = []
    try:
        result = run_canary_probe(
            _agent_config(),
            invoke_fn=_gated_invoke(gate, calls),
            trusted_parent=trusted_parent,
            timeout_s=0.1,
        )
        assert result.outcome == ProbeOutcome.INCONCLUSIVE
        assert result.reason == "probe_timeout"
        surviving = list((trusted_parent / ".agent" / "tmp").glob("canary-probe-*"))
        assert len(surviving) == 1, "the scratch directory must survive the poll window"
        scratch = surviving[0]
        waiter = threading.Event()
        removed = False
        for _ in range(70):
            if not scratch.exists():
                removed = True
                break
            waiter.wait(timeout=0.05)
        assert not gate.is_set(), (
            "the session thread must still be hung when the scratch is reclaimed"
        )
        assert removed, (
            "the scratch directory must be reclaimed even when the session thread never ends"
        )
    finally:
        gate.set()


def test_canary_probe_regression_scratch_reclaimed_on_a_terminal_process_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    gate = threading.Event()
    calls: list[_Call] = []
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="INFO", format="{message}")
    monkeypatch.setattr(
        "ralph.process.manager.get_process_manager",
        lambda: SimpleNamespace(
            get_record=lambda pid, include_terminal=True: SimpleNamespace(
                pid=pid, status=ProcessStatus.EXITED
            )
        ),
    )
    try:
        result = run_canary_probe(
            _agent_config(),
            invoke_fn=_spawn_reporting_invoke(gate, calls, 4321),
            trusted_parent=trusted_parent,
            timeout_s=1.0,
        )
        assert result.outcome == ProbeOutcome.INCONCLUSIVE
        assert result.reason == "probe_timeout"
        assert len(calls) == 1
        options = calls[0].options
        assert options is not None
        scratch = options.workspace_path
        assert scratch is not None
        waiter = threading.Event()
        removed = False
        for _ in range(40):
            if not scratch.exists():
                removed = True
                break
            waiter.wait(timeout=0.02)
        assert not gate.is_set(), (
            "the session thread must still be hung when the scratch is reclaimed"
        )
        assert removed, "a terminal process record must reclaim the scratch without thread death"
        assert not [
            message for message in messages if "canary probe attempt outcome:" in message
        ], "no attempt result may be published while the session thread is wedged"
    finally:
        gate.set()
        logger.remove(sink_id)
