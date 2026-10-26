"""Composed reproduction of the observed 2026-10-24 adversarial sequence.

The observed case (``PRODUCT_CRITERIA.md``, "Open symptom class:
Git/process/environment failures stall the run"): while agents were active
in the worktree, the background catch-up ticker ran a ``merge --ff-only``
that hung, timed out after 120 s, and was retried identically forever; the
force-killed child left a stale lock; and the operator's Ctrl+C ended in
``run_time_report emission failed: run_time_report validation failed`` with
the only explanation lost.

This file chains the whole sequence end-to-end in the shared simulated
environment (``tests._fake_git_env.make_fake_environment``): no real git,
no subprocess, no sleep, no wall clock. Each phase asserts the rule the
observed run violated:

* (a) with an agent active the tick defers and names the CURRENT
  observation, and no ``merge --ff-only`` is issued;
* (b) after the agent finishes, the hung merge is charged once per attempt
  against the durable budget and then quarantined: a ladder rung replaces
  the identical unbounded retry;
* (c) the ``index.lock`` the force-killed merge child left behind is
  recovered by evidence and never blocks the next observation;
* (d) an interrupt in that stalled state still emits a valid run-time
  report: the interrupted run's state is COMPOSED from the same simulated
  catch-up sequence -- the ledger rung, the quarantine reason, the charged
  timeout attempts -- so a defect propagating that state into the report
  fails here; and a rejected full report degrades to a valid one instead
  of being lost.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol
from unittest.mock import MagicMock

import pytest
from loguru import logger

import ralph.pipeline.run_time_report as run_time_report_module
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import EscalationRung
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState, RunMetrics
from tests._fake_git_env import LIVE_OWNER, STALE_OWNER, make_fake_environment
from tests._fake_git_fault import FaultKind

if TYPE_CHECKING:
    from tests._fake_git_env import SimulatedGitEnvironment

_TARGET = "main"
_HEAD = "feature"
#: The observed run sat ~298 minutes before the operator pressed Ctrl+C.
_STALL_SECONDS = 298.0 * 60.0
_OBSERVED_MERGE_TIMEOUT_SECONDS = 120.0


pytestmark = pytest.mark.timeout_seconds(15)


def _config() -> UnifiedConfig:
    """Return a config with the catch-up ticker enabled and the remote off."""
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_remote_enabled": False}}
    )


def _ledger(tmp_path: Path) -> AttemptLedger:
    """Return a durable ledger rooted in the temporary workspace."""
    return AttemptLedger(tmp_path, ledger_path=tmp_path / ".agent" / "catchup-ledger.json")


class _FakeCommit:
    """Commit double exposing the tip SHA the tick reads."""

    def __init__(self, sha: str) -> None:
        self.hexsha = sha


class _FakeHead:
    """Branch double exposing the name and tip the tick observes."""

    def __init__(self, name: str, sha: str) -> None:
        self.name = name
        self.commit = _FakeCommit(sha)


class _FakeBranch:
    """Branch double for the attached branch the tick reads."""

    name = _HEAD


class _FakeGit:
    """GitPython's dynamic dispatch double for the porcelain status probe."""

    def status(self, *_args: object, **_kwargs: object) -> str:
        """Return an empty porcelain status: the tracked worktree is clean."""
        return ""


class _FakeRepo:
    """Repository double reading the LIVE simulated environment state.

    Unlike the per-test doubles in ``test_catchup_ticker_adversity``, the
    ref tips are read from the environment on every construction, so the
    test can advance the target mid-sequence exactly as the observed run's
    world moved on while the ticker retried one frozen state.
    """

    def __init__(self, root: Path, env: SimulatedGitEnvironment) -> None:
        self.git_dir = root / ".git"
        self.common_dir = root / ".git"
        self.active_branch = _FakeBranch()
        self.git = _FakeGit()
        self.heads = [
            _FakeHead(env.target, env.target_sha),
            _FakeHead(env.feature_branch, env.feature_sha),
        ]

    def commit(self, sha: str) -> _FakeCommit:
        """Return the commit double for the given SHA."""
        return _FakeCommit(sha)

    def is_ancestor(self, _ancestor: _FakeCommit, _descendant: _FakeCommit) -> bool:
        """Report the target as a descendant: the checkout is strictly behind."""
        return True

    def close(self) -> None:
        """Match GitPython's close contract."""


class _LockEnvironment:
    """Maps the simulated lock ownership onto the catch-up liveness seam."""

    def __init__(self, env: SimulatedGitEnvironment) -> None:
        self._env = env

    def candidates(self, _git_dir: Path, _common_dir: Path) -> list[Path]:
        """Return every lock the simulated repository currently holds."""
        return [Path(name) for name in self._env.locks]

    def verdict(self, lock_path: Path, _git_dir: Path, _common_dir: Path) -> str:
        """Return the liveness verdict the probe would reach for the lock."""
        owner = self._env.locks.get(lock_path.name)
        if owner == STALE_OWNER:
            return "dead"
        if owner == LIVE_OWNER:
            return "live"
        return "unknown"

    def remove(self, lock_path: Path, _identity: tuple[int, int, int] | None) -> bool:
        """Remove the lock and report the removal to the simulated state."""
        self._env.locks.pop(lock_path.name, None)
        return True


class _Seams:
    """The patched collaborators and the merge calls they recorded."""

    def __init__(self, merges: list[str]) -> None:
        self.merges = merges


def _install_seams(monkeypatch: pytest.MonkeyPatch, env: SimulatedGitEnvironment) -> _Seams:
    """Patch every catch-up collaborator; record the issued merge calls.

    The merge double consumes :data:`FaultKind.GIT_TIMEOUT` from the
    simulated environment, tying the injected hang to the named catalogue
    row "A. a Git command misbehaves -- times out with no output".
    """
    merges: list[str] = []
    locks = _LockEnvironment(env)

    def _merge(_root: Path, sha: str) -> bool:
        merges.append(sha)
        if env.pop_fault(FaultKind.GIT_TIMEOUT):
            raise subprocess.TimeoutExpired(
                ["git", "-c", "rerere.enabled=false", "merge", "--ff-only", sha],
                _OBSERVED_MERGE_TIMEOUT_SECONDS,
            )
        return True

    def _repo(root: Path, *_args: object, **_kwargs: object) -> _FakeRepo:
        return _FakeRepo(root, env)

    def _identity(_lock_path: Path) -> tuple[int, int, int]:
        return (1, 2, 3)

    def _lease_held(_root: Path) -> bool:
        return env.agent_active

    def _acquire_lease(*_args: object, **_kwargs: object) -> object | None:
        return None if env.agent_active else object()

    def _end_lease(_lease: object) -> None:
        return None

    monkeypatch.setattr(catchup, "Repo", _repo)
    monkeypatch.setattr(catchup, "fast_forward_via_worktree", _merge)
    monkeypatch.setattr(catchup, "is_lease_held", _lease_held)
    monkeypatch.setattr(catchup, "try_acquire_lease", _acquire_lease)
    monkeypatch.setattr(catchup, "end_agent_lease", _end_lease)
    monkeypatch.setattr(catchup, "git_lock_candidates", locks.candidates)
    monkeypatch.setattr(catchup, "lock_has_no_live_holder", locks.verdict)
    monkeypatch.setattr(catchup, "lock_identity", _identity)
    monkeypatch.setattr(catchup, "unlink_if_unchanged", locks.remove)
    return _Seams(merges)


def _capture_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the ticker's warnings as formatted text."""
    messages: list[str] = []

    def _record(message: str, *args: object, **_kwargs: object) -> None:
        messages.append(message.format(*args) if args else message)

    monkeypatch.setattr(logger, "warning", _record)
    return messages


def _tick(env: SimulatedGitEnvironment, ledger: AttemptLedger, root: Path) -> str:
    """Run one catch-up tick against the simulated repository."""
    return catchup.attempt_catchup_fast_forward(
        _config(),
        root,
        target=_TARGET,
        ledger=ledger,
        clock=env.clock,
    )


def _assert_valid_report(report: str) -> None:
    """Assert the report parses under the closed run_time_report grammar."""
    __import__("ralph.mcp.artifacts.markdown.specs")
    _, diagnostics = parse_and_validate(report, get_spec("run_time_report"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert not errors, errors


class _SubmissionSink(Protocol):
    def __call__(
        self,
        workspace_root: Path,
        artifact_type: str,
        parsed_content: dict[str, object],
        *,
        markdown: str,
        deps: object,
    ) -> None: ...


def _capture_submission(captured: list[str]) -> _SubmissionSink:
    def _submit(
        workspace_root: Path,
        artifact_type: str,
        parsed_content: dict[str, object],
        *,
        markdown: str,
        deps: object,
    ) -> None:
        del workspace_root, artifact_type, parsed_content, deps
        captured.append(markdown)

    return _submit


def _capture_errors(captured: list[str]) -> int:
    """Route loguru errors into ``captured`` and return the sink id."""
    return logger.add(captured.append, level="ERROR", format="{message}")


class _LoopContextView(Protocol):
    """Structural view of the run-loop context the interrupt driver writes.

    The driver only ever assigns the single-slot ``latest_state`` sink, so
    the test depends on that one attribute rather than on the loop's
    private context class.
    """

    latest_state: list[PipelineState]


class _StallSequence:
    """The artifacts the observed catch-up sequence produced in the simulation.

    Clause (d) composes the interrupted run's state from exactly these
    observed values -- the timeout outcomes, the charged ledger, the ladder
    rung, the stale-lock recovery -- so nothing in the interrupt state is
    hand-built.
    """

    def __init__(
        self,
        *,
        env: SimulatedGitEnvironment,
        seams: _Seams,
        ledger: AttemptLedger,
        warnings: list[str],
        deferred_outcome: str,
        merge_count_after_defer: int,
        timeout_outcomes: list[str],
        ladder_outcome: str,
    ) -> None:
        self.env = env
        self.seams = seams
        self.ledger = ledger
        self.warnings = warnings
        self.deferred_outcome = deferred_outcome
        self.merge_count_after_defer = merge_count_after_defer
        self.timeout_outcomes = timeout_outcomes
        self.ladder_outcome = ladder_outcome


def _run_catchup_stall_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _StallSequence:
    """Replay the observed (a)-(c) ticker sequence in the simulated environment.

    Runs the same phases as the chained (a)-(c) test up to the quarantine
    tick -- the moment the observed run sat stalled -- and returns every
    artifact clause (d) needs. The world is deliberately NOT advanced
    afterwards: the interrupt lands in the stall, before recovery.
    """
    env, _git_ops, _agent_runner, _clock = make_fake_environment(
        target=_TARGET, feature_branch=_HEAD
    )
    seams = _install_seams(monkeypatch, env)
    warnings = _capture_warnings(monkeypatch)
    ledger = _ledger(tmp_path)
    # The observed run: the merge hung twice against one unchanged state.
    env.set_faults([FaultKind.GIT_TIMEOUT, FaultKind.GIT_TIMEOUT])

    # (a) the agent is active: defer, name the observation, no merge.
    env.agent_active = True
    deferred_outcome = _tick(env, ledger, tmp_path)
    merge_count_after_defer = len(seams.merges)

    # The agent finishes; the first merge hangs, the second tick recovers the
    # stale lock the force-killed child left BEFORE charging the second hang.
    env.agent_active = False
    first_timeout = _tick(env, ledger, tmp_path)
    env.leave_stale_lock("index.lock")
    second_timeout = _tick(env, ledger, tmp_path)

    # (b) the identical merge is quarantined: the ladder rung answers.
    ladder_outcome = _tick(env, ledger, tmp_path)

    return _StallSequence(
        env=env,
        seams=seams,
        ledger=ledger,
        warnings=warnings,
        deferred_outcome=deferred_outcome,
        merge_count_after_defer=merge_count_after_defer,
        timeout_outcomes=[first_timeout, second_timeout],
        ladder_outcome=ladder_outcome,
    )


def _assert_stall_sequence(sequence: _StallSequence) -> None:
    """Guard that the sequence really reached the catch-up stall state.

    Clause (d) builds the interrupted run's state from these artifacts; if
    the sequence ever stops short of the stall, the report assertions below
    would compose a state the ticker never produced.
    """
    durable = sequence.ledger.read()
    assert sequence.deferred_outcome == catchup.CATCHUP_REFUSED_AGENT_ACTIVE
    assert sequence.merge_count_after_defer == 0, "the deferral must issue no merge"
    assert len(sequence.seams.merges) == 2
    assert len(sequence.timeout_outcomes) == 2
    assert all(outcome.startswith(catchup.CATCHUP_TIMEOUT) for outcome in sequence.timeout_outcomes)
    assert "index.lock" not in sequence.env.locks, "the killed child's lock must be recovered"
    assert sequence.ladder_outcome.startswith(catchup.CATCHUP_LADDER_ESCALATED)
    assert f"rung={durable.rung.value}" in sequence.ladder_outcome
    # Per-attempt, not an aggregate sum: the single (signature, action) key
    # must hold exactly one charge for each merge the ticker issued, so a
    # mispaired ledger (one key at 0, another at 2) cannot pass.
    assert list(durable.repeat_counts.values()) == [len(sequence.seams.merges)], (
        "each attempt charges the durable budget exactly once"
    )


def _stall_state(sequence: _StallSequence) -> PipelineState:
    """The pipeline state the observed run had reached when it was interrupted.

    Every integration fact is projected from the catch-up sequence clauses
    (a)-(c) ran in the simulated environment -- nothing is hand-built: the
    typed LADDER_ESCALATION outcome and the quarantine reason string the
    ticker returned (naming the ledger's rung inside it) become the run's
    rebase state and the reason its development timebox redirected. Hours
    elapsed with zero agent activity while that timebox ran.
    """
    return PipelineState(
        phase="development",
        metrics=RunMetrics(
            total_agent_calls=0,
            total_continuations=0,
            total_fallbacks=0,
            total_retries=0,
        ),
        push_count=0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=_STALL_SECONDS,
        dev_timebox_redirect_reason=sequence.ladder_outcome,
        rebase=RebaseState(
            last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
            last_reason=sequence.ladder_outcome,
        ),
    )


def _interrupt_in_stall_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stall_state: PipelineState,
    submitted: list[str],
    invalid_render: bool,
) -> tuple[int, list[str]]:
    """Drive ``run_loop.run`` to an interrupt in ``stall_state``; return results."""
    monkeypatch.setattr(
        run_time_report_module,
        "submit_artifact_canonical",
        _capture_submission(submitted),
    )
    if invalid_render:
        # Reproduce the observed shutdown failure: the run's own renderer
        # produced a report the closed grammar rejected.
        def _invalid_render(**_kwargs: object) -> str:
            return (
                "---\n"
                "type: run_time_report\n"
                "outcome: interrupted\n"
                f"elapsed_seconds: {_STALL_SECONDS:.3f}\n"
                "final_phase: development\n"
                "---\n\n"
                "## Unexpected Section\n"
                "- [X-1] The stalled run's full report was rejected.\n"
            )

        monkeypatch.setattr(run_time_report_module, "render_run_time_report", _invalid_render)

    def _skip_checkpoint(_state: PipelineState, **_kwargs: object) -> None:
        return None

    def _skip_cleanup(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner_module, "save_checkpoint_or_log", _skip_checkpoint)
    monkeypatch.setattr(run_loop, "_cleanup_pipeline", _skip_cleanup)

    clock = iter((0.0, _STALL_SECONDS))

    def _monotonic() -> float:
        return next(clock)

    def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        run_loop,
        "time",
        SimpleNamespace(monotonic=_monotonic, sleep=_sleep),
    )

    def _advance_then_interrupt(
        _state: PipelineState,
        ctx: _LoopContextView,
        _prev_phase: str,
    ) -> tuple[PipelineState, str, int | None]:
        ctx.latest_state[:] = [stall_state]
        raise KeyboardInterrupt

    monkeypatch.setattr(run_loop, "_run_inner_loop", _advance_then_interrupt)

    errors: list[str] = []
    sink_id = _capture_errors(errors)
    try:
        exit_code = run_loop.run(
            MagicMock(),
            initial_state=PipelineState(phase="planning"),
            verbosity=Verbosity.QUIET,
        )
    finally:
        logger.remove(sink_id)
    return exit_code, errors


def test_observed_sequence_agent_hang_budget_ladder_and_stale_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(a)-(c) The observed ticker sequence, chained in one simulated run."""
    sequence = _run_catchup_stall_sequence(monkeypatch, tmp_path)
    env = sequence.env
    seams = sequence.seams
    ledger = sequence.ledger

    # Phase 1 — (a) the agent is active: defer, name the observation, no merge.
    assert sequence.deferred_outcome == catchup.CATCHUP_REFUSED_AGENT_ACTIVE
    assert sequence.merge_count_after_defer == 0
    deferred = [message for message in sequence.warnings if "deferred" in message]
    assert deferred, sequence.warnings
    assert f"target={_TARGET}@{env.target_sha}" in deferred[0]
    assert f"head={_HEAD}@{env.feature_sha}" in deferred[0]

    # Phase 2 — the agent finished; the merge hung and was force-killed,
    # leaving the stale lock the observed run found. The second tick
    # recovered the lock FIRST (evidence: the killed child owns it) and the
    # second hang charged the budget a second time.
    first_outcome, second_outcome = sequence.timeout_outcomes
    assert first_outcome.startswith(catchup.CATCHUP_TIMEOUT)
    assert second_outcome.startswith(catchup.CATCHUP_TIMEOUT)
    assert "index.lock" not in env.locks, "the killed child's lock must be recovered"
    assert len(seams.merges) == 2
    durable = ledger.read()
    # Per-attempt, not an aggregate sum: one charge per issued merge, so a
    # mispaired ledger (two keys at 0 and 2) cannot pass as "once per attempt".
    assert list(durable.repeat_counts.values()) == [len(seams.merges)], (
        "each attempt charges the durable budget exactly once"
    )
    assert durable.rung is EscalationRung.DISPATCH_RESOLVER

    # Phase 3 — (b) the identical merge is never issued a third time: the
    # quarantine rung answers, naming the ladder rung it moved to.
    assert sequence.ladder_outcome.startswith(catchup.CATCHUP_LADDER_ESCALATED)
    assert "rung=" in sequence.ladder_outcome
    assert f"target={_TARGET}@{env.target_sha}" in sequence.ladder_outcome
    assert len(seams.merges) == 2, "the quarantined merge must not run again"
    assert list(ledger.read().repeat_counts.values()) == [2], "a quarantined action charges nothing"

    # Phase 4 — (c) the world moved on; the next observation is not blocked:
    # a fresh state signature, no locks, and the merge succeeds.
    env.target_sha = f"{env.target_sha}-advanced"
    recovered_outcome = _tick(env, ledger, tmp_path)
    assert recovered_outcome == catchup.CATCHUP_FAST_FORWARDED
    assert seams.merges[-1] == env.target_sha
    assert env.locks == {}


def test_observed_sequence_interrupt_emits_a_valid_run_time_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(d) Interrupting the stalled run still emits a valid, truthful report.

    The interrupted state is COMPOSED from the same simulated catch-up
    sequence as clauses (a)-(c): the ledger rung, the quarantine reason and
    the per-attempt charges flow into the run's state, and the emitted
    report must carry them -- a defect in propagating the stall state into
    the report fails here.
    """
    sequence = _run_catchup_stall_sequence(monkeypatch, tmp_path)
    _assert_stall_sequence(sequence)
    submitted: list[str] = []

    exit_code, errors = _interrupt_in_stall_state(
        monkeypatch,
        stall_state=_stall_state(sequence),
        submitted=submitted,
        invalid_render=False,
    )

    durable = sequence.ledger.read()
    assert exit_code == 130
    assert len(submitted) == 1
    report = submitted[0]
    _assert_valid_report(report)
    assert "outcome: failed" in report
    assert "final_phase: development" in report
    assert f"elapsed_seconds: {_STALL_SECONDS:.3f}" in report
    assert "Agent calls: 0; retries: 0; continuations: 0; fallbacks: 0; pushes: 0." in report
    assert "## Development Timebox" in report
    # The report names the ladder rung the ledger recorded: the quarantine
    # outcome the ticker returned (carrying ``rung=<rung>``) flowed into the
    # run's timebox redirect reason, so the operator sees WHY the run stalled.
    assert "[DT-2] Redirected: " in report
    assert f"rung={durable.rung.value}" in report
    # The pending integration the stall never resolved is named, and the
    # elapsed wall-clock the operator waited through is reported.
    assert "Integration status: unresolved at report time." in report
    assert f"Total wall-clock time: {_STALL_SECONDS:.3f}s." in report
    # The full report validated, so the run never lost its report to the
    # observed ``run_time_report emission failed`` failure.
    assert "Degraded run-time report" not in report
    assert not [message for message in errors if "run_time_report emission failed" in message]


def test_observed_sequence_rejected_full_report_degrades_instead_of_dying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(d) The observed validation failure degrades to a valid report."""
    sequence = _run_catchup_stall_sequence(monkeypatch, tmp_path)
    _assert_stall_sequence(sequence)
    submitted: list[str] = []

    exit_code, _errors = _interrupt_in_stall_state(
        monkeypatch,
        stall_state=_stall_state(sequence),
        submitted=submitted,
        invalid_render=True,
    )

    assert exit_code == 130
    assert len(submitted) == 1
    report = submitted[0]
    _assert_valid_report(report)
    assert "Degraded run-time report" in report
    assert "run_time_report validation failed" in report
    assert f"elapsed_seconds: {_STALL_SECONDS:.3f}" in report
    assert "Agent calls: 0; retries: 0; continuations: 0; fallbacks: 0; pushes: 0." in report
    assert "Integration status: unresolved at report time." in report
    # The rejection of the full report is logged (``errors``); what the
    # observed run lost was the report itself. The degraded replacement was
    # still submitted.
