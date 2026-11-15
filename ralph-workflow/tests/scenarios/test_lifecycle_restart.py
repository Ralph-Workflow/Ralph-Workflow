from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from tests._scenario_harness import commit_file, git, supervisor_restart
from tests.scenarios._content_equivalence import assert_content_equivalence

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

_PHASE_SCRIPT = r"""
import json
import os
import pathlib
import signal
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
package_root = sys.argv[2]
mode = sys.argv[3]
log_path = pathlib.Path(sys.argv[4])
interrupt_at = sys.argv[5]
sys.path.insert(0, package_root)

from tests.scenarios._content_equivalence import drive_integration

log_path.write_text("pid %d\n" % os.getpid(), encoding="utf-8")


def resolver(repo, target, stop):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(stop.sha + "\n")
    if stop.sha == interrupt_at:
        if mode == "interrupt":
            os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGKILL)
    for relative in stop.conflicted_files:
        (repo / relative).write_text("resolved by scenario agent\n", encoding="utf-8")
    subprocess.run(("git", "add", "--", *stop.conflicted_files), cwd=repo, check=True)
    return True


run = drive_integration(root, resolver=resolver)
print(json.dumps({"error": run.error, "launches": run.resolver_launches}))
"""


def _two_stop_conflicted_rebase(root: Path) -> tuple[str, str, str]:
    commit_file(root, "first.txt", "base first\n", "base first")
    commit_file(root, "second.txt", "base second\n", "base second")
    git(root, "checkout", "-q", "-b", "feature")
    completed_stop = commit_file(root, "first.txt", "feature first\n", "feature first stop")
    pending_stop = commit_file(root, "second.txt", "feature second\n", "feature second stop")
    git(root, "checkout", "-q", "main")
    commit_file(root, "first.txt", "target first\n", "target first change")
    commit_file(root, "second.txt", "target second\n", "target second change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("scenario fixture did not pause in a conflicted rebase")
    return git(root, "rev-parse", "feature"), completed_stop, pending_stop


def _run_phase(
    root: Path,
    *,
    mode: str,
    log_path: Path,
    interrupt_at: str,
) -> subprocess.CompletedProcess[str]:
    package_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        (
            sys.executable,
            "-I",
            "-c",
            _PHASE_SCRIPT,
            str(root),
            str(package_root),
            mode,
            str(log_path),
            interrupt_at,
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )


def _phase_log(log_path: Path) -> tuple[int, tuple[str, ...]]:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    pid = int(lines[0].split()[1])
    return pid, tuple(line for line in lines[1:] if line)


@pytest.mark.parametrize("interruption", ["process-crash", "machine-restart", "interrupted-run"])
def test_restart_resumes_from_git_facts_without_redo_or_loss(
    tmp_git_repo: Path,
    interruption: str,
) -> None:
    feature_sha, completed_stop, pending_stop = _two_stop_conflicted_rebase(tmp_git_repo)
    interrupted_log = tmp_git_repo.parent / "interrupted-stops.log"
    mode = "interrupt" if interruption == "interrupted-run" else "crash"

    interrupted = _run_phase(
        tmp_git_repo,
        mode=mode,
        log_path=interrupted_log,
        interrupt_at=pending_stop,
    )

    if interruption == "interrupted-run":
        assert interrupted.returncode != 0, "an interrupted run must not report success"
        assert "KeyboardInterrupt" in interrupted.stderr, (
            "the interrupted run must unwind through the pipeline, not vanish silently"
        )
    else:
        assert interrupted.returncode == -signal.SIGKILL, (
            f"the {interruption} must kill the running process outright: "
            f"got {interrupted.returncode}"
        )
    interrupted_pid, presented_before = _phase_log(interrupted_log)
    assert completed_stop in presented_before, (
        "the first stop must be resolved before the interruption"
    )
    assert pending_stop in presented_before, (
        "the interruption must be injected while the next stop is being resolved"
    )

    subjects = git(tmp_git_repo, "log", "--format=%s", "HEAD").splitlines()
    assert "feature first stop" in subjects, (
        "the completed stop must be durably committed before the interruption"
    )
    assert git(tmp_git_repo, "rev-parse", "REBASE_HEAD") == pending_stop, (
        "the interrupted rebase must still carry the remaining stop"
    )
    progress = json.loads(
        (tmp_git_repo / ".ralph" / "conflict-resolution-progress.json").read_text(encoding="utf-8")
    )
    assert completed_stop in progress["landed_shas"], (
        "the completed stop must be recorded as landed in the durable progress"
    )

    if interruption == "machine-restart":
        restarted = supervisor_restart(tmp_git_repo)
        assert restarted["head"] == git(tmp_git_repo, "rev-parse", "HEAD"), (
            "the restarted supervisor must observe the partial integration, not restart it"
        )
        assert restarted["resumed"] is True, "the restarted supervisor must resume the operation"
        assert git(tmp_git_repo, "rev-parse", "REBASE_HEAD") == pending_stop, (
            "the restarted supervisor must not abort the interrupted rebase"
        )

    resumed_log = tmp_git_repo.parent / "resumed-stops.log"
    resumed = _run_phase(
        tmp_git_repo,
        mode="resume",
        log_path=resumed_log,
        interrupt_at="",
    )
    assert resumed.returncode == 0, f"the resumed run must not crash: {resumed.stderr}"
    payload = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert payload["error"] is None, f"the resumed run must not fail: {payload['error']}"
    resumed_pid, presented_after = _phase_log(resumed_log)
    assert resumed_pid != interrupted_pid, (
        "the resume must run in a fresh process so no process-local state survives"
    )
    assert completed_stop not in presented_after, (
        "a completed stop must never be presented to the agent again"
    )
    assert git(tmp_git_repo, "rev-parse", "main") == git(tmp_git_repo, "rev-parse", "feature"), (
        "the resumed run must land the feature on the target"
    )
    assert git(tmp_git_repo, "show", "main:first.txt").strip() == "resolved by scenario agent", (
        "the target must carry the resolved feature content"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract=f"{interruption} recovery must land without losing work",
        feature=feature_sha,
        source_refs=("main",),
    )
