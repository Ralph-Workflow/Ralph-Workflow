from __future__ import annotations

import fcntl
import json
import socket
import threading
from pathlib import Path

import pytest

from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_transaction import (
    LOCK_OWNER_FILENAME,
    begin_integration_operation,
    completed_integration_steps,
    integration_transaction,
    operation_marker_path,
    record_integration_step,
)
from ralph.pipeline.integration_attempt_ledger import (
    AttemptLedger,
    repository_ledger_path,
    shared_rung_result_key,
)
from ralph.pipeline.lease_outcome_kind import LeaseOutcomeKind

pytestmark = pytest.mark.timeout_seconds(30)


def _git(repo: Path, *args: str) -> str:
    result = run_git(args, cwd=repo, label="concurrent-integration-resources")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(
        repo,
        "-c",
        "user.name=Ralph Tests",
        "-c",
        "user.email=ralph@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_killed_owner_lock_is_recovered_without_stealing_live_owner(tmp_git_repo: Path) -> None:
    git_dir = Path(_git(tmp_git_repo, "rev-parse", "--absolute-git-dir"))
    owner_path = git_dir / LOCK_OWNER_FILENAME
    owner_path.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 2147483647,
                "hostname": socket.gethostname(),
                "recorded_at": 0.0,
                "released": False,
            }
        ),
        encoding="utf-8",
    )

    with integration_transaction(tmp_git_repo) as recovered:
        assert recovered.acquired
        assert recovered.stale_lock_recovered

    lock_path = git_dir / "ralph-auto-integrate.lock"
    with lock_path.open("a+", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with integration_transaction(tmp_git_repo) as blocked:
            assert blocked.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER
            assert not blocked.stale_lock_recovered


def test_crash_marker_resumes_completed_steps_and_sibling_landing(tmp_git_repo: Path) -> None:
    base = _git(tmp_git_repo, "rev-parse", "HEAD")
    _git(tmp_git_repo, "switch", "-c", "feature")
    feature_tip = _commit(tmp_git_repo, "feature.txt", "feature\n", "feature")
    begin_integration_operation(
        tmp_git_repo,
        operation_id="operation-one",
        target="main",
        target_tip=base,
        feature_tip=feature_tip,
    )
    record_integration_step(tmp_git_repo, "backup-ref-created")
    record_integration_step(tmp_git_repo, "backup-ref-created")

    with integration_transaction(tmp_git_repo) as resumed:
        assert resumed.resumed_operation
        assert not resumed.sibling_landed
    assert completed_integration_steps(tmp_git_repo) == ("backup-ref-created",)

    _git(tmp_git_repo, "branch", "-f", "main", feature_tip)
    with integration_transaction(tmp_git_repo) as converged:
        assert converged.resumed_operation
        assert converged.sibling_landed
    marker = operation_marker_path(tmp_git_repo)
    assert marker is not None
    assert not marker.exists()


def test_two_worktrees_publish_shared_results_without_loss(tmp_git_repo: Path, tmp_path: Path) -> None:
    _git(tmp_git_repo, "branch", "worker-a")
    _git(tmp_git_repo, "branch", "worker-b")
    worktree_a = tmp_path / "worktree-a"
    worktree_b = tmp_path / "worktree-b"
    _git(tmp_git_repo, "worktree", "add", str(worktree_a), "worker-a")
    _git(tmp_git_repo, "worktree", "add", str(worktree_b), "worker-b")
    observed_common = Path(_git(tmp_git_repo, "rev-parse", "--git-common-dir"))
    common_dir = (
        observed_common if observed_common.is_absolute() else tmp_git_repo / observed_common
    ).resolve()
    ledger_path = repository_ledger_path(common_dir)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def publish(root: Path, signature: str, result_key: str) -> None:
        try:
            ledger = AttemptLedger(root, common_dir=common_dir)
            barrier.wait(timeout=5)
            ledger.charge(signature, "repair", is_unknown=False)
            ledger.publish_rung_result(result_key, f"result:{signature}")
        except BaseException as error:
            errors.append(error)

    key_a = shared_rung_result_key("target", "vendor-a", ("a.txt",))
    key_b = shared_rung_result_key("target", "vendor-b", ("b.txt",))
    threads = (
        threading.Thread(target=publish, args=(worktree_a, "signature-a", key_a)),
        threading.Thread(target=publish, args=(worktree_b, "signature-b", key_b)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    json.loads(ledger_path.read_text(encoding="utf-8"))
    restored = AttemptLedger(worktree_a, common_dir=common_dir).read()
    assert restored.repeat_counts[("signature-a", "repair")] == 1
    assert restored.repeat_counts[("signature-b", "repair")] == 1
    assert restored.shared_rung_results[key_a] == "result:signature-a"
    assert restored.shared_rung_results[key_b] == "result:signature-b"
    assert not tuple(ledger_path.parent.glob(f"{ledger_path.name}.staging.*"))
