from __future__ import annotations

import time
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline.operation_evidence import identity_fingerprint
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._integration_ladder_fakes import _seed_terminal_rungs

pytestmark = [pytest.mark.timeout_seconds(20), pytest.mark.subprocess_e2e]

_OPTIONS = GitRunOptions(timeout=10)
_TERMINAL = "Integration ladder terminal state:"


class TestClock:
    __test__ = False

    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(args, cwd=root, label="landed-predicate-blackbox", options=_OPTIONS)


def _value(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, path: str, content: str, message: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _value(root, "rev-parse", "HEAD")


def _init(root: Path) -> None:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0


def _start_conflicted_rebase(root: Path, commits: int) -> tuple[str, str, str, str]:
    _commit(root, "shared.txt", "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    stop = _commit(root, "shared.txt", "feature 0\n", "feature 0")
    for index in range(1, commits):
        _commit(root, f"feature-{index}.txt", f"feature {index}\n", f"feature {index}")
    feature = _value(root, "rev-parse", "HEAD")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "shared.txt", "main\n", "main moves")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    assert _value(root, "rev-parse", "REBASE_HEAD") == stop
    return feature, target, _value(root, "rev-parse", "HEAD"), stop


def _snapshot(
    root: Path,
    feature: str,
    target: str,
    anchor: str,
    stop: str,
) -> None:
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=feature,
            pre_target_sha=target,
            rebase_continue_pending=True,
            rebase_continue_head=anchor,
            rebase_continue_stop=stop,
            rebase_continue_tree=_value(root, "write-tree"),
            owning_worktree=str(root),
        ),
    )


def _finish_rebase(root: Path) -> None:
    for _ in range(12):
        gitdir = Path(_value(root, "rev-parse", "--absolute-git-dir"))
        if not (gitdir / "rebase-merge").exists() and not (gitdir / "rebase-apply").exists():
            return
        if _git(root, "rebase", "--continue").returncode != 0:
            assert _git(root, "rebase", "--skip").returncode == 0
    pytest.fail("rebase did not finish")


def _lease(root: Path) -> IntegrationRecord:
    record = read_record(root)
    assert record is not None
    leased = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
    )
    write_record(root, leased)
    return leased


def _diagnose(
    root: Path,
    *,
    ledger: AttemptLedger | None = None,
    clock: TestClock | None = None,
    controller: RecoveryController | None = None,
) -> tuple[bool, list[str], list[tuple[str, str]]]:
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []

    def invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return False

    def sink(message: object) -> None:
        data: dict[str, object] = getattr(message, "record", {})
        level = data.get("level")
        captured.append(
            (str(getattr(level, "name", "")), str(getattr(message, "message", message)))
        )

    handler = loguru_logger.add(sink, level="INFO")
    try:
        result = handoff_integration_diagnostic(
            controller=controller
            or RecoveryController(options=RecoveryControllerOptions(clock=FakeClock())),
            root=root,
            record=_lease(root),
            failure="retained completed rebase",
            agents=("test-resolver",),
            invoke=invoke,
            ledger=ledger,
            clock=clock,
        )
    finally:
        loguru_logger.remove(handler)
    return result, invoked, captured


def test_three_commit_first_stop_conflict_lands_without_resolver(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    feature, target, anchor, stop = _start_conflicted_rebase(root, 3)
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    _snapshot(root, feature, target, anchor, stop)
    _finish_rebase(root)

    result, invoked, captured = _diagnose(root)

    assert result is True
    assert invoked == []
    assert [message for level, message in captured if level == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrated"
    assert saved.integrated_feature_sha == _value(root, "rev-parse", "HEAD")
    assert saved.rebase_continue_head is None
    assert saved.rebase_continue_stop is None
    assert saved.rebase_continue_tree is None


def test_empty_resolution_stop_is_proven_landed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    feature, target, anchor, stop = _start_conflicted_rebase(root, 2)
    (root / "shared.txt").write_text("main\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    _snapshot(root, feature, target, anchor, stop)
    _finish_rebase(root)

    result, invoked, captured = _diagnose(root)

    assert result is True
    assert invoked == []
    assert [message for level, message in captured if level == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrated"
    assert saved.integrated_feature_sha == _value(root, "rev-parse", "HEAD")


def test_restarted_rebase_onto_advanced_target_is_proven_landed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    feature, target, anchor, stop = _start_conflicted_rebase(root, 1)
    (root / "shared.txt").write_text("first resolution\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    _snapshot(root, feature, target, anchor, stop)
    assert _git(root, "rebase", "--abort").returncode == 0
    assert _git(root, "switch", "main").returncode == 0
    _commit(root, "main-advanced.txt", "advanced\n", "main advances again")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    (root / "shared.txt").write_text("second resolution\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    _finish_rebase(root)

    result, invoked, captured = _diagnose(root)

    assert result is True
    assert invoked == []
    assert [message for level, message in captured if level == "CRITICAL"] == []
    saved = read_record(root)
    assert saved is not None
    assert saved.phase == "integrated"
    assert saved.integrated_feature_sha == _value(root, "rev-parse", "HEAD")
    assert saved.rebase_continue_head is None
    assert saved.rebase_continue_stop is None
    assert saved.rebase_continue_tree is None


def test_unrelated_head_emits_one_terminal_status_then_stays_silent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    feature, target, anchor, stop = _start_conflicted_rebase(root, 1)
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    _snapshot(root, feature, target, anchor, stop)
    _finish_rebase(root)
    tree = _value(root, "rev-parse", "HEAD^{tree}")
    unrelated_ref = "refs/heads/unrelated"
    assert _git(root, "checkout", "--orphan", "unrelated").returncode == 0
    assert _git(root, "commit", "-m", "unrelated root").returncode == 0
    assert _value(root, "rev-parse", "HEAD^{tree}") == tree
    unrelated = _value(root, "rev-parse", "HEAD")
    assert _value(root, "symbolic-ref", "HEAD") == unrelated_ref
    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "landed-terminal.json")
    _seed_terminal_rungs(ledger)
    clock = TestClock()
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))

    terminal: list[str] = []
    invoked: list[str] = []
    captured: list[tuple[str, str]] = []
    for _ in range(6):
        result, round_invoked, round_captured = _diagnose(
            root, ledger=ledger, clock=clock, controller=controller
        )
        assert result is False
        invoked.extend(round_invoked)
        captured.extend(round_captured)
        terminal.extend(message for _level, message in round_captured if _TERMINAL in message)
        if terminal:
            break

    assert len(terminal) == 1
    assert unrelated in terminal[0]
    assert anchor in terminal[0]
    record = read_record(root)
    assert record is not None
    assert record.rebase_continue_tree is not None
    assert record.rebase_continue_tree in terminal[0]
    assert "ralph --resume" in terminal[0]
    assert "preserved copy:" in terminal[0]
    assert invoked == []
    assert not any("Selected agent" in message for _level, message in captured)

    later: list[tuple[str, str]] = []
    for _ in range(10):
        clock.advance(60)
        result, round_invoked, round_captured = _diagnose(
            root, ledger=ledger, clock=clock, controller=controller
        )
        assert result is False
        invoked.extend(round_invoked)
        later.extend(round_captured)

    assert invoked == []
    assert [item for item in later if item[0] in {"CRITICAL", "WARNING"}] == []
    assert not any("Selected agent" in message for _level, message in later)
