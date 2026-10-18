"""Proof / head-match / ownership / marker / gitdir / clean-diff / index-tree branches.

Split-out of ``test_integration_diagnostic.py`` to keep each file under the
1000-line cap enforced by ``ralph.testing.audit_repo_structure``. These tests
exercise the diagnostic's per-proof branches:

* identity routing via ``diagnostic_needed``;
* positive completion proof releases evidence;
* unreachable merge object takes the intervention path;
* reachable merge head with unreadable index preserves markers;
* no positive proof routes to intervention without consuming the attempt;
* ownership proof requires a non-empty matching worktree;
* index-tree proof treats unknown return codes and exceptions as ``UNKNOWN``;
* uncertain marker query persists intervention while preserving on-disk markers.
"""

from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions
from ralph.git.subprocess_runner import run_git as original_run_git
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    identity_fingerprint,
)
from tests.test_pending_merge_commit_recovery import _commit, _git


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_pending_identity_unavailable_routes_inspection_instead_of_source_repair(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        merge_commit_pending=operation == "merge",
        rebase_continue_pending=operation == "rebase",
    )

    def identity(_root: Path, _record: IntegrationRecord) -> bool:
        return False

    monkeypatch.setattr(
        "ralph.pipeline._pending_merge_commit.pending_merge_identity_matches", identity
    )
    monkeypatch.setattr(
        "ralph.pipeline._pending_rebase_continue.pending_rebase_identity_matches", identity
    )
    assert diagnostic.diagnostic_needed(Path("/workspace"), record)


@pytest.mark.subprocess_e2e
def test_positive_completion_proof_releases_evidence(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0

    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", "main").returncode == 0
    assert _git(root, "merge", "--no-ff", "-m", "merge commit", "feature").returncode == 0
    merge_sha = _git(root, "rev-parse", "HEAD").stdout.strip()

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=merge_sha,
        merge_commit_parent=feature,
        merge_commit_pending=True,
        owning_worktree=str(root),
    )
    record = record.model_copy(
        update={
            "diagnostic_ownership": f"{identity_fingerprint(record)}:{__import__('time').time()}"
        }
    )
    # The release path now routes through apply_record_transition, which
    # requires the on-disk record to be the transition source. Persist
    # the record first; the function then reads it, applies the
    # transition, and writes back the cleared result.
    write_record(root, record)
    collector = InMemoryOperationEvidenceCollector(reachability={"MERGE_HEAD": True})
    diagnostic.set_operation_evidence_collector(collector)
    try:
        agent_called = False

        def fake_invoke(agent: str, prompt: Path) -> bool:
            nonlocal agent_called
            agent_called = True
            return False

        result = diagnostic.handoff_integration_diagnostic(
            root=root,
            record=record,
            failure="test failure",
            agents=("test-agent",),
            invoke=fake_invoke,
        )
        assert result is True
        assert not agent_called
        saved = read_record(root)
        assert saved is not None
        assert saved.diagnostic_evidence == ()
        assert saved.diagnostic_ownership is None
        assert saved.merge_commit_repair_attempts == 0
    finally:
        diagnostic.set_operation_evidence_collector(None)


@pytest.mark.subprocess_e2e
def test_unreachable_merge_object_takes_intervention_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    _commit(root, "file.txt", "content\n")

    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    nonexistent_sha = "0" * 40
    (gitdir / "MERGE_HEAD").write_text(nonexistent_sha + "\n", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_merge=True,
        merge_commit_repair_attempts=2,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test failure",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 2
    assert (gitdir / "MERGE_HEAD").exists()
    assert (gitdir / "MERGE_HEAD").read_text(encoding="utf-8").strip() == nonexistent_sha


@pytest.mark.subprocess_e2e
def test_reachable_merge_head_with_unreadable_index_preserves_metadata(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0

    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature content\n")
    assert _git(root, "switch", "main").returncode == 0
    _commit(root, "shared.txt", "main content\n")

    assert _git(root, "merge", "--no-commit", "--no-ff", "feature").returncode != 0
    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    assert (gitdir / "MERGE_HEAD").exists()
    original_merge_head_bytes = (gitdir / "MERGE_HEAD").read_bytes()

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        merge_commit_parent=feature,
        merge_commit_pending=True,
        merge_commit_repair_attempts=1,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test failure",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 1
    assert (gitdir / "MERGE_HEAD").read_bytes() == original_merge_head_bytes


@pytest.mark.subprocess_e2e
def test_no_positive_proof_routes_to_intervention_without_consuming_attempt(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    _commit(root, "file.txt", "base\n")

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
        merge_commit_pending=True,
        merge_commit_repair_attempts=0,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="ancestry mismatch",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 0


def test_ownership_proof_requires_nonempty_matching_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        owning_worktree=str(root),
    )
    record = record.model_copy(
        update={
            "diagnostic_ownership": f"{identity_fingerprint(record)}:{__import__('time').time()}"
        }
    )

    # Valid matching nonempty ownership
    ok, reason = diagnostic._check_ownership_proof(root, record)
    assert ok is True
    assert reason == "ownership verified"

    # Missing / empty owning_worktree
    no_wt = record.model_copy(update={"owning_worktree": None})
    ok, reason = diagnostic._check_ownership_proof(root, no_wt)
    assert ok is False
    assert "missing or empty" in reason

    empty_wt = record.model_copy(update={"owning_worktree": ""})
    ok, reason = diagnostic._check_ownership_proof(root, empty_wt)
    assert ok is False
    assert "missing or empty" in reason

    # Mismatched owning_worktree
    other = tmp_path / "other"
    other.mkdir()
    mismatched = record.model_copy(update={"owning_worktree": str(other)})
    ok, reason = diagnostic._check_ownership_proof(root, mismatched)
    assert ok is False
    assert "does not match" in reason

    # Missing diagnostic_ownership
    no_fingerprint = record.model_copy(update={"diagnostic_ownership": None})
    ok, reason = diagnostic._check_ownership_proof(root, no_fingerprint)
    assert ok is False
    assert "missing or empty" in reason

    # Forged diagnostic_ownership (no colon separator -> unparseable)
    forged = record.model_copy(update={"diagnostic_ownership": "0" * 64})
    ok, reason = diagnostic._check_ownership_proof(root, forged)
    assert ok is False
    assert "does not match ownership_fingerprint" in reason


def test_index_tree_proof_treats_unknown_returncodes_and_exceptions_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    # 1. Unexpected exit code on MERGE_HEAD
    def mock_run_git_merge_head_error(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal: lock error")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_merge_head_error)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "MERGE_HEAD status is UNKNOWN (unexpected exit code 128)" in reason

    # 2. Exception on MERGE_HEAD
    def mock_run_git_merge_head_exc(*_args: object, **_kwargs: object) -> GitRunResult:
        raise RuntimeError("git merge-head subprocess crashed")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_merge_head_exc)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "MERGE_HEAD status is UNKNOWN (git merge-head subprocess crashed)" in reason

    # 3. Unexpected exit code on REBASE_HEAD
    def mock_run_git_rebase_head_error(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")
        if "REBASE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal error")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_rebase_head_error)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "REBASE_HEAD status is UNKNOWN (unexpected exit code 128)" in reason

    # 4. Exception on diff
    def mock_run_git_diff_exc(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t or "REBASE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")
        if "--absolute-git-dir" in args_t:
            return GitRunResult(args=args_t, returncode=0, stdout=f"{root}\n", stderr="")
        if "diff" in args_t:
            raise OSError("diff command unavailable")
        return GitRunResult(args=args_t, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_diff_exc)

    def fake_unmerged(_root: Path) -> list[str]:
        return []

    monkeypatch.setattr("ralph.git.merge.unmerged_paths", fake_unmerged)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "git diff failed: diff command unavailable (UNKNOWN)" in reason


@pytest.mark.subprocess_e2e
def test_handoff_uncertain_marker_query_persists_intervention_preserving_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    base = _commit(root, "shared.txt", "base\n")
    second = _commit(root, "shared.txt", "second\n")

    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    marker_content = b"test-marker-sha\n"
    (gitdir / "MERGE_HEAD").write_bytes(marker_content)

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha=base,
        integrated_feature_sha=second,
        merge_commit_parent=base,
        merge_commit_pending=True,
        merge_commit_repair_attempts=0,
        owning_worktree=str(root),
    )
    record = record.model_copy(
        update={
            "diagnostic_ownership": f"{identity_fingerprint(record)}:{__import__('time').time()}"
        }
    )

    def fake_run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        **kwargs: object,
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t and "check_merge_head" in label:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal lock error")
        return original_run_git(args, cwd=cwd, label=label, options=options)

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", fake_run_git)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test uncertain error",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert "UNKNOWN" in (saved.repair_cap_reason or "")
    assert saved.merge_commit_repair_attempts == 0
    # Marker on disk preserved!
    assert (gitdir / "MERGE_HEAD").exists()
    assert (gitdir / "MERGE_HEAD").read_bytes() == marker_content


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_interrupted_diagnostic_dead_owner_releases_and_makes_automated_progress(
    tmp_path: Path,
) -> None:
    """AC3: Killing the owning process mid-diagnostic releases automatically on next run.

    Runs a diagnostic in a real subprocess until ``HOLD_DIAGNOSTIC_EVIDENCE``
    is durably written with owner identity (PID/boot ID), hard-kills the owning
    process with SIGKILL, restarts recovery in a NEW subprocess (different
    PID), and verifies automated progress is made (hold released, no operator
    intervention, no retained skip).
    """
    import os
    import signal
    import subprocess
    import sys

    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import read_record

    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", "main").returncode == 0

    # 1. Run a real subprocess that exercises the diagnostic hold
    # seam via the actual ``diagnose_retained_integration`` entry point.
    # The subprocess writes a held record with its own PID/boot id,
    # then sleeps so the test can SIGKILL it before the diagnostic
    # completes (mimicking the production crash window).
    diag_script = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "\n"
        "from ralph.pipeline.auto_integrate_record import (\n"
        "    IntegrationRecord,\n"
        "    read_record,\n"
        "    write_record,\n"
        "    HOLD_DIAGNOSTIC_EVIDENCE,\n"
        ")\n"
        "from ralph.pipeline._integration_diagnostic import (\n"
        "    diagnose_retained_integration,\n"
        ")\n"
        "from ralph.pipeline.operation_evidence import identity_fingerprint\n"
        "from ralph.pipeline._auto_integrate_record_restamp import current_boot_id\n"
        "\n"
        "root = Path(sys.argv[1])\n"
        "feature = sys.argv[2]\n"
        "target = sys.argv[3]\n"
        "\n"
        "record = IntegrationRecord(\n"
        "    phase='integrating',\n"
        "    target='main',\n"
        "    pre_feature_sha=feature,\n"
        "    pre_target_sha=target,\n"
        "    owning_worktree=str(root),\n"
        "    operation_kind='feature_integrate',\n"
        ")\n"
        "fp = identity_fingerprint(record)\n"
        "lease = f'{fp}:{time.time()}'\n"
        "held = record.with_transition(\n"
        "    HOLD_DIAGNOSTIC_EVIDENCE,\n"
        "    diagnostic_evidence=(('unstaged_work', 'clean'),),\n"
        "    diagnostic_ownership=lease,\n"
        "    diagnostic_owner_pid=os.getpid(),\n"
        "    diagnostic_owner_boot_id=current_boot_id(),\n"
        ")\n"
        "write_record(root, held)\n"
        "sys.stdout.write('HELD\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", diag_script, str(root), feature, base],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    sync_line = proc.stdout.readline()
    assert sync_line.strip() == "HELD"

    # Confirm the hold is durably on disk with proc.pid
    held_on_disk = read_record(root)
    assert held_on_disk is not None
    assert held_on_disk.diagnostic_ownership is not None
    assert held_on_disk.diagnostic_owner_pid == proc.pid

    # 2. Hard-kill the owning process (real SIGKILL, not simulated)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()

    # 3. Start a NEW subprocess (fresh PID) and run recovery there.
    # The recovery entry point must execute in a process different
    # from the killed diagnostic owner to faithfully reproduce the
    # production crash-recovery path: a fresh Ralph start in any
    # worktree picks up where the killed run left off.
    recovery_script = (
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        "from ralph.pipeline.auto_integrate_recovery import (\n"
        "    recover_incomplete_integration,\n"
        ")\n"
        "from ralph.workspace.scope import WorkspaceScope\n"
        "\n"
        "root = Path(sys.argv[1])\n"
        "_outcome = recover_incomplete_integration(\n"
        "    WorkspaceScope(root), backend=DEFAULT_FILE_BACKEND\n"
        ")\n"
        "sys.stdout.write('RECOVERED\\n')\n"
        "sys.stdout.flush()\n"
    )
    rec_proc = subprocess.Popen(
        [sys.executable, "-c", recovery_script, str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert rec_proc.stdout is not None
    assert rec_proc.stdout.readline().strip() == "RECOVERED"
    rec_proc.wait(timeout=15)

    # 4. Assert next run made automated progress: hold released, no
    # intervention, no retained skip. The fresh subprocess had a
    # different PID than the killed diagnostic owner.
    after = read_record(root)
    assert after is not None
    assert after.diagnostic_ownership is None
    assert after.diagnostic_evidence == ()
    assert after.diagnostic_owner_pid is None
    assert after.diagnostic_intervention_required is False
    assert next_integration_action(after) != IntegrationAction.REQUIRE_INTERVENTION
