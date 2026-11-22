from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_record import record_path
from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.scenarios._integration_run import IntegrationRun

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_young_lock_the_probe_cannot_clear_ends_the_tick_in_a_bounded_status": (
        "the scenario creates a Linux non-dumpable process and relies on /proc process "
        "inspection, so its existing environment guard now matches that Linux-only boundary"
    ),
    "test_a_stale_lock_on_the_shared_target_is_recovered_and_both_siblings_land": (
        "the stale-lock cell now bounds the same operator lines and failed landings "
        "the concurrent cell bounds, so a recovery that repeats its landing report "
        "fails instead of passing; the landing and no-park expectations are unchanged"
    ),
    "test_a_worktree_that_crashed_mid_operation_recovers_its_lock_and_lands": (
        "the crashed-sibling cell now bounds the same operator lines and failed "
        "landings the concurrent cell bounds, so a recovery that repeats its landing "
        "report fails instead of passing; the landing and no-park expectations are "
        "unchanged"
    ),
}

_PARKING_LITERALS: tuple[str, ...] = (
    "operator action required",
    "needsoperator",
    "landedprooffailed",
)

_STALE_LOCK_AGE_SECONDS: float = 2 * 3600.0

_YOUNG_LOCK_AGE_SECONDS: float = 120.0

_MAX_YOUNG_LOCK_TICK_SECONDS: float = 30.0

_UNINSPECTABLE_PROCESS: str = (
    "import ctypes, time\n"
    "ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
    "print('ready', flush=True)\n"
    "time.sleep(300)\n"
)

_CONCURRENT_JOIN_SECONDS: float = 120.0

_MAX_OPERATOR_LINES: int = 8

_MAX_FAILED_LANDINGS: int = 2


def _sibling_worktree(repo: Path, path: Path, branch: str) -> Path:
    git(repo, "worktree", "add", "-q", "-b", branch, str(path), "main")
    return path


def _drive(root: Path) -> IntegrationRun:
    clock = FakeClock()
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, clock=clock)
    return drive_integration(root, resolver=runner, clock=clock)


def _landed(run: IntegrationRun) -> bool:
    state = run.state
    return state is not None and bool(state.fast_forwarded)


def _operator_texts(runs: tuple[IntegrationRun, ...]) -> tuple[str, ...]:
    texts: list[str] = []
    for run in runs:
        for line in run.operator_lines:
            text = str(line.content)
            if text not in texts:
                texts.append(text)
    return tuple(texts)


def _parked(texts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        text for text in texts if any(literal in text.lower() for literal in _PARKING_LITERALS)
    )


def _parked_state(run: IntegrationRun) -> bool:
    state = run.state
    return state is not None and state.is_intervention_required


def _failed_landings(texts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(text for text in texts if "did NOT land" in text)


def _resume_point(root: Path) -> bool:
    return record_path(root).exists()


def _plant_lock(lock_path: Path, age_seconds: float) -> Path:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("", encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(lock_path, (stamp, stamp))
    return lock_path


def _plant_stale_lock(lock_path: Path) -> Path:
    return _plant_lock(lock_path, _STALE_LOCK_AGE_SECONDS)


def _plant_young_lock(lock_path: Path) -> Path:
    return _plant_lock(lock_path, _YOUNG_LOCK_AGE_SECONDS)


@contextlib.contextmanager
def _uninspectable_process(tmp_path: Path) -> Iterator[subprocess.Popen[str]]:
    script = tmp_path / "uninspectable.py"
    script.write_text(_UNINSPECTABLE_PROCESS, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-I", str(script)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _worktree_git_dir(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--absolute-git-dir"))


def _branch_carries_file(root: Path, branch: str, name: str) -> bool:
    listed = git(root, "ls-tree", "-r", "--name-only", branch)
    return name in listed.splitlines()


def test_two_worktrees_racing_one_target_both_land(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    alpha = _sibling_worktree(tmp_git_repo, tmp_path / "wt-alpha", "feature-alpha")
    beta = _sibling_worktree(tmp_git_repo, tmp_path / "wt-beta", "feature-beta")
    commit_file(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit_file(beta, "beta.txt", "beta\n", "beta work")
    alpha_sha = git(alpha, "rev-parse", "HEAD")
    beta_sha = git(beta, "rev-parse", "HEAD")

    barrier = threading.Barrier(2)
    runs: dict[str, IntegrationRun] = {}
    raised: list[Exception] = []

    def _integrate(name: str, root: Path) -> None:
        try:
            barrier.wait(timeout=30)
            runs[name] = _drive(root)
        except (AssertionError, OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            raised.append(exc)

    threads = (
        threading.Thread(target=_integrate, args=("alpha", alpha)),
        threading.Thread(target=_integrate, args=("beta", beta)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_CONCURRENT_JOIN_SECONDS)

    assert not any(thread.is_alive() for thread in threads), (
        "two worktrees integrating onto one target must not hang"
    )
    assert raised == [], f"a concurrent integration raised: {raised}"
    assert set(runs) == {"alpha", "beta"}
    for name, run in runs.items():
        assert run.error is None, f"{name} must not crash: {run.error}"

    texts = _operator_texts(tuple(runs.values()))
    assert not _parked(texts), f"a concurrent integration parked for a person: {_parked(texts)}"
    for name, run in runs.items():
        assert not _parked_state(run), f"{name} parked for a person: {run.state}"
    assert len(texts) <= _MAX_OPERATOR_LINES, (
        f"a concurrent integration must not log repeatedly: {texts}"
    )
    assert len(_failed_landings(texts)) <= _MAX_FAILED_LANDINGS, (
        f"a concurrent integration must not retry the landing without bound: {texts}"
    )

    for name, root in (("alpha", alpha), ("beta", beta)):
        if _landed(runs[name]):
            continue
        assert _resume_point(root), (
            f"{name} neither landed nor left a durable resume point to retry from"
        )
        resumed = _drive(root)
        assert resumed.error is None, f"{name} must not crash on the retry seam: {resumed.error}"
        assert not _parked_state(resumed), f"{name} parked for a person: {resumed.state}"
        assert _landed(resumed), f"{name} must land on the next seam after the race"
        runs[name] = resumed

    assert_content_equivalence(
        tmp_git_repo,
        contract="the alpha sibling's work must reach the shared target",
        target="main",
        feature=alpha_sha,
        source_refs=("main",),
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="the beta sibling's work must reach the shared target",
        target="main",
        feature=beta_sha,
        source_refs=("main",),
    )
    assert _branch_carries_file(alpha, "feature-alpha", "alpha.txt"), (
        "the racing sibling must keep its own work on its own branch"
    )
    assert _branch_carries_file(beta, "feature-beta", "beta.txt"), (
        "the racing sibling must keep its own work on its own branch"
    )


def test_a_stale_lock_on_the_shared_target_is_recovered_and_both_siblings_land(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    alpha = _sibling_worktree(tmp_git_repo, tmp_path / "wt-alpha", "feature-alpha")
    beta = _sibling_worktree(tmp_git_repo, tmp_path / "wt-beta", "feature-beta")
    commit_file(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit_file(beta, "beta.txt", "beta\n", "beta work")
    alpha_sha = git(alpha, "rev-parse", "HEAD")
    beta_sha = git(beta, "rev-parse", "HEAD")

    stale_lock = _plant_stale_lock(tmp_git_repo / ".git" / "refs" / "heads" / "main.lock")

    alpha_run = _drive(alpha)
    assert alpha_run.error is None, f"alpha must not crash: {alpha_run.error}"
    assert not stale_lock.exists(), (
        "the next tick must recover the stale lock left on the shared target"
    )
    texts = _operator_texts((alpha_run,))
    assert not _parked(texts), f"a stale lock parked the sibling for a person: {_parked(texts)}"
    assert not _parked_state(alpha_run), f"alpha parked for a person: {alpha_run.state}"
    assert _landed(alpha_run), f"alpha must land past the stale lock: {alpha_run.state}"

    beta_run = _drive(beta)
    assert beta_run.error is None, f"beta must not crash: {beta_run.error}"
    assert not _parked_state(beta_run), f"beta parked for a person: {beta_run.state}"
    assert _landed(beta_run), f"beta must land past the stale lock: {beta_run.state}"

    recovery_texts = _operator_texts((alpha_run, beta_run))
    assert len(recovery_texts) <= _MAX_OPERATOR_LINES, (
        f"a stale-lock recovery must not log repeatedly: {recovery_texts}"
    )
    assert len(_failed_landings(recovery_texts)) <= _MAX_FAILED_LANDINGS, (
        f"a stale-lock recovery must not retry the landing without bound: {recovery_texts}"
    )

    assert_content_equivalence(
        tmp_git_repo,
        contract="the alpha sibling's work must reach the shared target",
        target="main",
        feature=alpha_sha,
        source_refs=("main",),
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="the beta sibling's work must reach the shared target",
        target="main",
        feature=beta_sha,
        source_refs=("main",),
    )


def test_a_worktree_that_crashed_mid_operation_recovers_its_lock_and_lands(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    alpha = _sibling_worktree(tmp_git_repo, tmp_path / "wt-alpha", "feature-alpha")
    beta = _sibling_worktree(tmp_git_repo, tmp_path / "wt-beta", "feature-beta")
    commit_file(alpha, "alpha.txt", "alpha\n", "alpha work")
    commit_file(beta, "beta.txt", "beta\n", "beta work")
    alpha_sha = git(alpha, "rev-parse", "HEAD")
    beta_sha = git(beta, "rev-parse", "HEAD")

    commit_file(tmp_git_repo, "shared.txt", "target value\n", "target side")
    commit_file(beta, "shared.txt", "beta value\n", "beta side")
    git(beta, "rebase", "main", check=False)
    assert git(beta, "rev-parse", "--verify", "REBASE_HEAD", check=False), (
        "the sibling must be left mid-operation for this scenario"
    )
    stale_lock = _plant_stale_lock(_worktree_git_dir(beta) / "index.lock")

    alpha_run = _drive(alpha)
    assert alpha_run.error is None, f"alpha must not crash: {alpha_run.error}"
    assert not _parked_state(alpha_run), f"alpha parked for a person: {alpha_run.state}"
    assert _landed(alpha_run), f"alpha must land while its sibling is crashed: {alpha_run.state}"

    beta_run = _drive(beta)
    assert beta_run.error is None, f"beta must not crash: {beta_run.error}"
    texts = _operator_texts((alpha_run, beta_run))
    assert not _parked(texts), f"the crashed sibling parked for a person: {_parked(texts)}"
    assert not _parked_state(beta_run), f"beta parked for a person: {beta_run.state}"
    assert not stale_lock.exists(), (
        "the next tick must recover the lock the crashed operation left behind"
    )
    assert _landed(beta_run), (
        f"the next tick must resume the crashed operation and land: {beta_run.state}"
    )
    assert len(texts) <= _MAX_OPERATOR_LINES, (
        f"a crashed-sibling recovery must not log repeatedly: {texts}"
    )
    assert len(_failed_landings(texts)) <= _MAX_FAILED_LANDINGS, (
        f"a crashed-sibling recovery must not retry the landing without bound: {texts}"
    )
    assert not git(beta, "status", "--porcelain"), (
        "the recovered operation must leave no paused rebase or conflict residue behind"
    )
    assert _branch_carries_file(beta, "feature-beta", "beta.txt"), (
        "the crashed sibling must keep its own work on its own branch"
    )

    assert_content_equivalence(
        tmp_git_repo,
        contract="the alpha sibling's work must survive the crashed sibling",
        target="main",
        feature=alpha_sha,
        source_refs=("main",),
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="the crashed sibling's work must reach the shared target",
        target="main",
        feature=beta_sha,
        source_refs=("main",),
        excluded_paths=("shared.txt",),
    )


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or os.geteuid() == 0,
    reason="requires Linux /proc inspection as a non-root user",
)
def test_a_young_lock_the_probe_cannot_clear_ends_the_tick_in_a_bounded_status(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    alpha = _sibling_worktree(tmp_git_repo, tmp_path / "wt-alpha", "feature-alpha")
    commit_file(alpha, "alpha.txt", "alpha\n", "alpha work")

    young_lock = _plant_young_lock(tmp_git_repo / ".git" / "refs" / "heads" / "main.lock")

    with _uninspectable_process(tmp_path):
        started = time.monotonic()
        alpha_run = _drive(alpha)
        elapsed = time.monotonic() - started

    assert alpha_run.error is None, f"alpha must not crash: {alpha_run.error}"
    assert elapsed < _MAX_YOUNG_LOCK_TICK_SECONDS, (
        f"the tick must not wait on a holder it cannot disprove (took {elapsed:.1f}s)"
    )
    assert young_lock.exists(), "a lock whose holder cannot be disproved must not be unlinked"
    assert not _landed(alpha_run), (
        f"the tick must not claim a landing it could not make: {alpha_run.state}"
    )
    assert not _parked_state(alpha_run), f"alpha parked for a person: {alpha_run.state}"
    assert _resume_point(alpha), "the tick must leave a durable resume point for the next attempt"
    texts = _operator_texts((alpha_run,))
    assert _failed_landings(texts), (
        f"the tick must surface an operator-facing status for the blocked landing: {texts}"
    )
    assert _branch_carries_file(alpha, "feature-alpha", "alpha.txt"), (
        "the sibling must keep its own work on its own branch while the lock blocks the target"
    )
