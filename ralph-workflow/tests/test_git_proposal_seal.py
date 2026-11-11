"""Tests for proposal seal and rollback primitives (ralph.git.proposal_seal)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.config.models import UnifiedConfig
from ralph.git.git_command_queue import route_git_method
from ralph.git.proposal_seal import (
    DiffEntry,
    ProposalPreflightError,
    Seal,
    diff_tree,
    preflight,
    prune,
    rollback,
    seal_baseline,
    seal_pending,
)
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _setup_repo(path: Path) -> Repo:
    """Initialize a git repo with identity and an initial commit."""
    repo = Repo.init(path)
    repo.config_writer().set_value("user", "name", "Ralph Test").release()
    repo.config_writer().set_value("user", "email", "test@ralph.internal").release()
    init_file = path / "init.txt"
    init_file.write_text("initial commit payload\n", encoding="utf-8")
    actor = Actor("Ralph Test", "test@ralph.internal")
    repo.index.add(["init.txt"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    return repo


def test_seal_pending_leaves_real_index_and_worktree_intact(tmp_path: Path) -> None:
    """Sealing must leave the real .git/index and working tree byte-identical."""
    repo = _setup_repo(tmp_path)
    real_index_path = Path(repo.index.path)
    real_index_before = real_index_path.read_bytes()

    # Create uncommitted modifications and an untracked file
    init_file = tmp_path / "init.txt"
    init_file.write_text("modified in working tree\n", encoding="utf-8")
    untracked_file = tmp_path / "untracked.txt"
    untracked_file.write_text("new untracked content\n", encoding="utf-8")

    seal = seal_pending(tmp_path, "run-alpha", 1)

    assert isinstance(seal, Seal)
    assert seal.ref == "refs/ralph/proposals/run-alpha/1"
    assert len(seal.commit_sha) == 40
    assert len(seal.tree_sha) == 40

    # Real index must be byte-identical
    real_index_after = real_index_path.read_bytes()
    assert real_index_after == real_index_before

    # Working tree files must be untouched
    assert init_file.read_text(encoding="utf-8") == "modified in working tree\n"
    assert untracked_file.read_text(encoding="utf-8") == "new untracked content\n"

    # The proposal ref must exist and resolve to the commit and tree SHA
    resolved_commit = str(route_git_method(repo, "rev_parse", (seal.ref,))).strip()
    assert resolved_commit == seal.commit_sha
    resolved_tree = str(route_git_method(repo, "rev_parse", (f"{seal.ref}^{{tree}}",))).strip()
    assert resolved_tree == seal.tree_sha


def test_diff_tree_reports_all_change_types_and_metadata(tmp_path: Path) -> None:
    """diff_tree must accurately reflect additions, deletions, modifications, modes, and binary flags."""
    repo = _setup_repo(tmp_path)

    # Add initial tracked files of various types
    tracked_txt = tmp_path / "tracked.txt"
    tracked_txt.write_text("tracked text v1\n", encoding="utf-8")
    to_delete_txt = tmp_path / "to_delete.txt"
    to_delete_txt.write_text("to be deleted\n", encoding="utf-8")
    binary_file = tmp_path / "binary.dat"
    binary_file.write_bytes(b"initial\x00binary\x00data")
    executable_file = tmp_path / "script.sh"
    executable_file.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
    executable_file.chmod(0o755)

    route_git_method(repo, "add", ("tracked.txt", "to_delete.txt", "binary.dat", "script.sh"))
    route_git_method(repo, "commit", ("-m", "add test fixtures"))

    # Apply development mutations
    tracked_txt.write_text("tracked text v2 modified\n", encoding="utf-8")
    to_delete_txt.unlink()
    binary_file.write_bytes(b"modified\x00binary\x00v2\x00bytes")
    executable_file.chmod(0o644)

    new_txt = tmp_path / "new_file.txt"
    new_txt.write_text("brand new text file\n", encoding="utf-8")
    new_bin = tmp_path / "new_bin.png"
    new_bin.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    seal = seal_pending(tmp_path, "run-diff", 1)
    entries = diff_tree(tmp_path, seal.ref)
    by_path = {e.path: e for e in entries}

    assert "tracked.txt" in by_path
    e_tracked = by_path["tracked.txt"]
    assert isinstance(e_tracked, DiffEntry)
    assert e_tracked.status == "M"
    assert not e_tracked.is_binary
    assert e_tracked.size == len(b"tracked text v2 modified\n")

    assert "to_delete.txt" in by_path
    e_del = by_path["to_delete.txt"]
    assert e_del.status == "D"
    assert e_del.size == 0
    assert not e_del.is_binary

    assert "binary.dat" in by_path
    e_bin = by_path["binary.dat"]
    assert e_bin.status == "M"
    assert e_bin.is_binary
    assert e_bin.size == len(b"modified\x00binary\x00v2\x00bytes")

    assert "new_file.txt" in by_path
    e_new_txt = by_path["new_file.txt"]
    assert e_new_txt.status == "A"
    assert not e_new_txt.is_binary
    assert e_new_txt.size == len(b"brand new text file\n")

    assert "new_bin.png" in by_path
    e_new_bin = by_path["new_bin.png"]
    assert e_new_bin.status == "A"
    assert e_new_bin.is_binary
    assert e_new_bin.size == len(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")


def test_rollback_restores_head_and_cleans_added_files_intact_agent_and_ignored(
    tmp_path: Path,
) -> None:
    """Rollback restores HEAD exactly, removes proposal additions, and preserves ignored and .agent/ files."""
    repo = _setup_repo(tmp_path)

    # Initial tracked files
    tracked_txt = tmp_path / "tracked.txt"
    tracked_txt.write_text("tracked v1\n", encoding="utf-8")
    del_target = tmp_path / "to_delete.txt"
    del_target.write_text("will be deleted\n", encoding="utf-8")
    exec_script = tmp_path / "run.sh"
    exec_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exec_script.chmod(0o755)

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.log\nignored_dir/\n", encoding="utf-8")

    route_git_method(repo, "add", ("tracked.txt", "to_delete.txt", "run.sh", ".gitignore"))
    route_git_method(repo, "commit", ("-m", "initial baseline fixtures"))

    # Add ignored file and .agent runtime directory
    ignored_log = tmp_path / "activity.log"
    ignored_log.write_text("persisted log line 1\n", encoding="utf-8")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    agent_state = agent_dir / "state.json"
    agent_state.write_text('{"phase": "test"}\n', encoding="utf-8")

    # Mutate working directory (proposal state)
    tracked_txt.write_text("tracked modified v2\n", encoding="utf-8")
    del_target.unlink()
    exec_script.chmod(0o644)

    sub_dir = tmp_path / "sub" / "nested"
    sub_dir.mkdir(parents=True)
    added_sub_file = sub_dir / "added.txt"
    added_sub_file.write_text("added in proposal\n", encoding="utf-8")

    added_bin = tmp_path / "added.bin"
    added_bin.write_bytes(b"\x00\x01\x02\x03\x04")

    added_exec = tmp_path / "added_exec.sh"
    added_exec.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    added_exec.chmod(0o755)

    # Mutate ignored and .agent state (should be preserved)
    ignored_log.write_text("persisted log line 2\n", encoding="utf-8")
    agent_state.write_text('{"phase": "test", "step": 2}\n', encoding="utf-8")

    seal = seal_pending(tmp_path, "run-rb", 1)

    # Rollback to HEAD
    rollback(tmp_path, seal.ref)

    # Verifications after rollback:
    # 1. Tracked modifications restored
    assert tracked_txt.read_text(encoding="utf-8") == "tracked v1\n"
    # 2. Deleted tracked files restored
    assert del_target.read_text(encoding="utf-8") == "will be deleted\n"
    # 3. Executable mode restored
    assert oct(exec_script.stat().st_mode)[-3:] == "755"
    # 4. Added files and empty parent directories removed
    assert not added_sub_file.exists()
    assert not sub_dir.exists()
    assert not added_bin.exists()
    assert not added_exec.exists()
    # 5. Ignored files and .agent/ state preserved intact
    assert ignored_log.read_text(encoding="utf-8") == "persisted log line 2\n"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "test", "step": 2}\n'

    # Recovery test: git checkout <ref> -- . restores the rejected proposal byte-for-byte
    route_git_method(repo, "checkout", (seal.ref, "--", "."))
    assert tracked_txt.read_text(encoding="utf-8") == "tracked modified v2\n"
    assert added_sub_file.read_text(encoding="utf-8") == "added in proposal\n"
    assert added_bin.read_bytes() == b"\x00\x01\x02\x03\x04"
    # The recovery restores the FILE MODE recorded in the proposal tree, not
    # just the content: ``run.sh`` was 755 in HEAD and 644 in the proposal, and
    # ``added_exec.sh`` is 755 in the proposal.
    assert oct(exec_script.stat().st_mode)[-3:] == "644"
    assert oct(added_exec.stat().st_mode)[-3:] == "755"


def test_rollback_crash_per_step_is_idempotent_and_resumes(tmp_path: Path) -> None:
    """Crash injected at each rollback step leaves repository recoverable and resumes cleanly.

    Every step of the crash-safe rollback sequence is covered, including the
    FINAL ``complete`` step: a crash after the tree was restored and the
    proposal additions were removed must still leave a repository that a
    resumed rollback converges on the same end state.
    """
    _setup_repo(tmp_path)

    init_file = tmp_path / "init.txt"
    init_file.write_text("dev modified text\n", encoding="utf-8")
    new_file = tmp_path / "proposal_added.txt"
    new_file.write_text("temporary addition\n", encoding="utf-8")

    seal = seal_pending(tmp_path, "run-crash", 1)

    steps_to_test = ("preflight", "read_tree", "remove_added_files", "complete")
    for crash_step in steps_to_test:
        # Re-dirty working tree to proposal state
        init_file.write_text("dev modified text\n", encoding="utf-8")
        new_file.write_text("temporary addition\n", encoding="utf-8")

        def make_hook(target_step: str) -> tuple[Callable[[str], None], str]:
            def hook(step: str) -> None:
                if step == target_step:
                    raise RuntimeError(f"simulated crash at {step}")

            return hook, target_step

        crash_hook, target = make_hook(crash_step)

        with pytest.raises(RuntimeError, match=f"simulated crash at {target}"):
            rollback(tmp_path, seal.ref, step_hook=crash_hook)

        # Resume rollback without crash hook
        rollback(tmp_path, seal.ref)

        assert init_file.read_text(encoding="utf-8") == "initial commit payload\n"
        assert not new_file.exists()


def test_resume_completes_a_rollback_pending_checkpoint_after_a_crash(
    tmp_path: Path,
) -> None:
    """A crash between the rollback checkpoint and the restore is resumed, not skipped.

    The pipeline checkpoints ``rollback_pending`` BEFORE it restores the
    tree. A crash in that window leaves the flag set and the working tree
    still holding the rejected proposal; the resume seam must complete the
    pending rollback (instead of repeating or skipping it) and clear the
    flag.
    """
    _setup_repo(tmp_path)

    init_file = tmp_path / "init.txt"
    init_file.write_text("dev modified text\n", encoding="utf-8")
    proposal_file = tmp_path / "proposal_added.txt"
    proposal_file.write_text("temporary addition\n", encoding="utf-8")

    seal = seal_pending(tmp_path, "run-resume", 1)

    # The checkpoint the pipeline writes before it restores the tree.
    crashed_state = PipelineState(
        phase="development",
        rollback_pending=True,
        rollback_ref=seal.ref,
        commit_review_rejections=1,
    )
    assert crashed_state.rollback_pending is True
    assert crashed_state.rollback_ref == seal.ref

    # The crash happened BEFORE the restore: the tree still holds the
    # rejected proposal.
    assert init_file.read_text(encoding="utf-8") == "dev modified text\n"
    assert proposal_file.exists()

    resumed = resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=load_policy(_DEFAULT_POLICY_DIR),
        initial_state=crashed_state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=tmp_path,
    )

    # The resume completed the pending rollback and cleared the flag.
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None
    assert init_file.read_text(encoding="utf-8") == "initial commit payload\n"
    assert not proposal_file.exists()


def test_preflight_fails_on_damaged_git_or_missing_refs(tmp_path: Path) -> None:
    """Preflight raises ProposalPreflightError on invalid/missing refs or damaged git metadata."""
    _setup_repo(tmp_path)

    # Missing proposal ref
    with pytest.raises(ProposalPreflightError, match="Proposal ref .* does not resolve"):
        preflight(tmp_path, "refs/ralph/proposals/nonexistent/99")

    # Rollback refuses to execute on invalid ref
    with pytest.raises(ProposalPreflightError):
        rollback(tmp_path, "refs/ralph/proposals/nonexistent/99")

    # Damaged .git metadata
    git_dir = tmp_path / ".git"
    git_head = git_dir / "HEAD"
    git_head.unlink()

    with pytest.raises(ProposalPreflightError):
        preflight(tmp_path, "refs/ralph/proposals/dummy/1")


def test_seal_baseline_and_rollback_to_baseline(tmp_path: Path) -> None:
    """Baseline seals uncommitted start state and rollback restores to baseline instead of HEAD."""
    _setup_repo(tmp_path)

    # Clean working tree: seal_baseline should return None
    clean_baseline = seal_baseline(tmp_path, "run-base")
    assert clean_baseline is None

    # User uncommitted changes at run start
    init_file = tmp_path / "init.txt"
    init_file.write_text("user uncommitted modifications\n", encoding="utf-8")
    user_file = tmp_path / "user_untracked.txt"
    user_file.write_text("user uncommitted new file\n", encoding="utf-8")

    base_seal = seal_baseline(tmp_path, "run-base")
    assert isinstance(base_seal, Seal)
    assert base_seal.ref == "refs/ralph/baselines/run-base"

    # Pipeline development introduces further changes
    init_file.write_text("pipeline broke this file\n", encoding="utf-8")
    pipeline_junk = tmp_path / "pipeline_junk.txt"
    pipeline_junk.write_text("pipeline junk content\n", encoding="utf-8")

    prop_seal = seal_pending(tmp_path, "run-base", 1)

    # Rollback to baseline
    rollback(tmp_path, prop_seal.ref, baseline_ref=base_seal.ref)

    # User changes are restored; pipeline additions removed
    assert init_file.read_text(encoding="utf-8") == "user uncommitted modifications\n"
    assert user_file.read_text(encoding="utf-8") == "user uncommitted new file\n"
    assert not pipeline_junk.exists()


def test_prune_removes_proposals_honoring_keep_and_retention(tmp_path: Path) -> None:
    """prune deletes proposal refs according to run_id, keep_refs, and max_retained rules."""
    repo = _setup_repo(tmp_path)
    head_sha = str(route_git_method(repo, "rev_parse", ("HEAD",))).strip()

    # Create several proposal refs across two run IDs
    ref_a1 = "refs/ralph/proposals/run-a/1"
    ref_a2 = "refs/ralph/proposals/run-a/2"
    ref_a3 = "refs/ralph/proposals/run-a/3"
    ref_b1 = "refs/ralph/proposals/run-b/1"

    route_git_method(repo, "update_ref", (ref_a1, head_sha))
    route_git_method(repo, "update_ref", (ref_a2, head_sha))
    route_git_method(repo, "update_ref", (ref_a3, head_sha))
    route_git_method(repo, "update_ref", (ref_b1, head_sha))

    # Prune run-a with keep_refs protecting ref_a1 and max_retained=1 protecting ref_a3
    pruned = prune(
        tmp_path,
        run_id="run-a",
        keep_refs=[ref_a1],
        max_retained=1,
    )

    # ref_a2 was neither kept nor in the top 1 newest, so it is pruned
    assert ref_a2 in pruned
    assert ref_a1 not in pruned
    assert ref_b1 not in pruned

    # Remaining refs check
    raw_refs = str(
        route_git_method(repo, "for_each_ref", ("refs/ralph/proposals", "--format=%(refname)"))
    )
    remaining = raw_refs.splitlines()
    assert ref_a1 in remaining
    assert ref_a3 in remaining
    assert ref_b1 in remaining
    assert ref_a2 not in remaining


def test_prune_keeps_the_rejection_ref_and_bounds_the_retained_refs(tmp_path: Path) -> None:
    """A kept rejection ref is never pruned; the retention limit bounds the rest.

    The documented recovery for a wrongly rejected iteration is
    ``git checkout <ref> -- .``, so the ref of a rejected iteration must
    survive every sweep. The retention limit bounds how many other
    proposal refs the sweep keeps.
    """
    repo = _setup_repo(tmp_path)
    head_sha = str(route_git_method(repo, "rev_parse", ("HEAD",))).strip()

    rejection_ref = "refs/ralph/proposals/run-a/1"
    older_ref = "refs/ralph/proposals/run-a/2"
    newest_ref = "refs/ralph/proposals/run-a/3"
    other_run_ref = "refs/ralph/proposals/run-b/1"

    for ref in (rejection_ref, older_ref, newest_ref, other_run_ref):
        route_git_method(repo, "update_ref", (ref, head_sha))

    pruned = prune(
        tmp_path,
        run_id="run-a",
        keep_refs=[rejection_ref],
        max_retained=1,
    )

    # The kept ref is never pruned, the retention limit keeps exactly one
    # other ref of the run, and a ref of another run is out of scope.
    assert rejection_ref not in pruned
    assert newest_ref not in pruned
    assert older_ref in pruned
    assert other_run_ref not in pruned

    raw_refs = str(
        route_git_method(repo, "for_each_ref", ("refs/ralph/proposals", "--format=%(refname)"))
    )
    remaining = raw_refs.splitlines()
    assert rejection_ref in remaining
    assert newest_ref in remaining
    assert older_ref not in remaining
    assert other_run_ref in remaining
