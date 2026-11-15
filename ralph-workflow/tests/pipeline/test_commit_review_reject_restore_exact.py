from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.git_command_queue import route_run_git
from ralph.git.proposal_seal import (
    diff_tree,
    preflight,
    seal_baseline,
    seal_pending,
)
from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.runner import (
    execute_pending_rollback,
    rollback_checkpoint_path_for_root,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"

_RUN_ID = "run-reject-exact"
_ITERATION = 1

_NON_UTF8_BINARY_PAYLOAD = b"\x00\xff\xfe\xfd\x80\x81\x82\x83\xaa\xbb\xcc\xdd\xee\xff"
_EXECUTABLE_SCRIPT_PAYLOAD = b"#!/bin/sh\necho execute\nexit 0\n"
_ADDED_FILE_PAYLOAD = b"new added file payload that was not in pre-work tree\n"
_MODIFIED_TRACKED_PAYLOAD = b"README modified during iteration\n"


def _snapshot_tree(root: Path, *, exclude_ignored: bool = False) -> dict[str, tuple[bytes, int]]:
    snapshot: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        if rel.startswith(".agent/") or rel == ".agent":
            continue
        if exclude_ignored and rel == "activity.log":
            continue
        mode = path.stat().st_mode & 0o777
        snapshot[rel] = (path.read_bytes(), mode)
    return snapshot


def _setup_prework_tree(
    root: Path,
    *,
    with_untracked: bool = True,
) -> tuple[dict[str, tuple[bytes, int]], str | None]:
    build_script = root / "scripts" / "build.sh"
    build_script.parent.mkdir(parents=True, exist_ok=True)
    build_script.write_bytes(b"#!/bin/sh\necho build\nexit 0\n")
    build_script.chmod(0o755)

    gitignore = root / ".gitignore"
    gitignore.write_text("*.log\n", encoding="utf-8")

    route_run_git(["add", "scripts/build.sh", ".gitignore"], cwd=root, label="add")
    route_run_git(["commit", "-m", "add baseline fixtures"], cwd=root, label="commit")

    ignored_log = root / "activity.log"
    ignored_log.write_text("pre-work activity log line\n", encoding="utf-8")

    agent_state = root / ".agent" / "state.json"
    agent_state.parent.mkdir(parents=True, exist_ok=True)
    agent_state.write_text('{"phase": "development", "step": 1}\n', encoding="utf-8")

    baseline_ref: str | None = None
    if with_untracked:
        user_notes = root / "user_notes.txt"
        user_notes.write_text("user uncommitted work that must survive\n", encoding="utf-8")
        baseline = seal_baseline(root, _RUN_ID)
        assert baseline is not None
        baseline_ref = baseline.ref

    prework_snapshot = _snapshot_tree(root, exclude_ignored=True)
    return prework_snapshot, baseline_ref


def _apply_proposal_changes(root: Path) -> None:
    binary_file = root / "assets" / "binary_blob.bin"
    binary_file.parent.mkdir(parents=True, exist_ok=True)
    binary_file.write_bytes(_NON_UTF8_BINARY_PAYLOAD)

    exec_file = root / "tools" / "deploy_tool.sh"
    exec_file.parent.mkdir(parents=True, exist_ok=True)
    exec_file.write_bytes(_EXECUTABLE_SCRIPT_PAYLOAD)
    exec_file.chmod(0o755)

    added_file = root / "docs" / "new_specification.txt"
    added_file.parent.mkdir(parents=True, exist_ok=True)
    added_file.write_bytes(_ADDED_FILE_PAYLOAD)

    readme_file = root / "README.md"
    readme_file.write_bytes(_MODIFIED_TRACKED_PAYLOAD)

    ignored_log = root / "activity.log"
    ignored_log.write_text(
        ignored_log.read_text(encoding="utf-8") + "iteration 1 runtime log line\n",
        encoding="utf-8",
    )

    agent_state = root / ".agent" / "state.json"
    agent_state.write_text('{"phase": "development", "step": 2}\n', encoding="utf-8")


def _assert_proposal_restorable_exact(root: Path, proposal_ref: str) -> None:
    with pytest.raises(UnicodeDecodeError):
        _NON_UTF8_BINARY_PAYLOAD.decode("utf-8")

    ls_exec = route_run_git(
        ["ls-tree", proposal_ref, "tools/deploy_tool.sh"], cwd=root, label="ls-tree"
    ).stdout.strip()
    assert ls_exec.startswith("100755 blob")
    assert ls_exec.endswith("tools/deploy_tool.sh")

    ls_bin = route_run_git(
        ["ls-tree", proposal_ref, "assets/binary_blob.bin"], cwd=root, label="ls-tree"
    ).stdout.strip()
    assert ls_bin.startswith("100644 blob")
    assert ls_bin.endswith("assets/binary_blob.bin")

    archive_tar = root / ".agent" / "proposal_inspect.tar"
    route_run_git(["archive", proposal_ref, "-o", str(archive_tar)], cwd=root, label="archive")
    restore_target = root / ".agent" / "proposal_extracted"
    restore_target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_tar) as tar:
        tar.extractall(path=restore_target, filter="data")

    extracted_bin = restore_target / "assets" / "binary_blob.bin"
    extracted_exec = restore_target / "tools" / "deploy_tool.sh"
    extracted_added = restore_target / "docs" / "new_specification.txt"
    extracted_readme = restore_target / "README.md"

    assert extracted_bin.read_bytes() == _NON_UTF8_BINARY_PAYLOAD
    assert extracted_exec.read_bytes() == _EXECUTABLE_SCRIPT_PAYLOAD
    assert oct(extracted_exec.stat().st_mode & 0o777) == "0o755"
    assert extracted_added.read_bytes() == _ADDED_FILE_PAYLOAD
    assert extracted_readme.read_bytes() == _MODIFIED_TRACKED_PAYLOAD


def test_commit_review_reject_restores_prework_working_tree_and_proposal_is_exact(
    tmp_path: Path,
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    prework_snapshot, baseline_ref = _setup_prework_tree(root, with_untracked=True)
    assert baseline_ref is not None

    _apply_proposal_changes(root)

    seal = seal_pending(root, _RUN_ID, _ITERATION)
    preflight(root, seal.ref)
    assert seal.ref == f"refs/ralph/proposals/{_RUN_ID}/{_ITERATION}"

    entries = {e.path: e for e in diff_tree(root, ref=seal.ref, base=baseline_ref)}
    assert entries["assets/binary_blob.bin"].is_binary is True
    assert entries["assets/binary_blob.bin"].status == "A"
    assert entries["tools/deploy_tool.sh"].new_mode == "100755"
    assert entries["tools/deploy_tool.sh"].status == "A"
    assert entries["docs/new_specification.txt"].status == "A"
    assert entries["docs/new_specification.txt"].is_binary is False
    assert entries["README.md"].status == "M"

    bundle = load_policy(_DEFAULT_POLICY_DIR)
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
        rollback_baseline_ref=baseline_ref,
    )
    pending_state, _ = reduce(
        state,
        CommitReviewRejected(
            reason="proposal rejected: binary asset and executable additions",
            ref=seal.ref,
            hard=True,
        ),
        bundle.pipeline,
    )
    assert pending_state.rollback_pending is True
    assert pending_state.rollback_ref == seal.ref

    checkpoint_path = rollback_checkpoint_path_for_root(root)
    cleared_state = execute_pending_rollback(
        pending_state,
        root,
        bundle.pipeline,
        checkpoint_path=checkpoint_path,
    )
    assert cleared_state.rollback_pending is False
    assert cleared_state.rollback_ref is None

    assert not (root / "assets" / "binary_blob.bin").exists()
    assert not (root / "tools" / "deploy_tool.sh").exists()
    assert not (root / "docs" / "new_specification.txt").exists()
    assert not (root / "assets").exists()
    assert not (root / "tools").exists()
    assert not (root / "docs").exists()

    assert _snapshot_tree(root, exclude_ignored=True) == prework_snapshot

    ignored_log = root / "activity.log"
    assert "pre-work activity log line\n" in ignored_log.read_text(encoding="utf-8")
    assert "iteration 1 runtime log line\n" in ignored_log.read_text(encoding="utf-8")

    agent_state = root / ".agent" / "state.json"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development", "step": 2}\n'

    user_notes = root / "user_notes.txt"
    assert user_notes.read_text(encoding="utf-8") == "user uncommitted work that must survive\n"

    _assert_proposal_restorable_exact(root, seal.ref)

    route_run_git(["checkout", seal.ref, "--", "."], cwd=root, label="checkout")
    assert (root / "assets" / "binary_blob.bin").read_bytes() == _NON_UTF8_BINARY_PAYLOAD
    assert (root / "tools" / "deploy_tool.sh").read_bytes() == _EXECUTABLE_SCRIPT_PAYLOAD
    assert oct((root / "tools" / "deploy_tool.sh").stat().st_mode & 0o777) == "0o755"
    assert (root / "docs" / "new_specification.txt").read_bytes() == _ADDED_FILE_PAYLOAD
    assert (root / "README.md").read_bytes() == _MODIFIED_TRACKED_PAYLOAD


def test_commit_review_reject_clean_baseline_restores_head_exact(
    tmp_path: Path,
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    prework_snapshot, baseline_ref = _setup_prework_tree(root, with_untracked=False)
    assert baseline_ref is None

    _apply_proposal_changes(root)

    seal = seal_pending(root, _RUN_ID, _ITERATION)
    preflight(root, seal.ref)

    bundle = load_policy(_DEFAULT_POLICY_DIR)
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
        rollback_baseline_ref=None,
    )
    pending_state, _ = reduce(
        state,
        CommitReviewRejected(
            reason="proposal rejected on clean baseline",
            ref=seal.ref,
            hard=True,
        ),
        bundle.pipeline,
    )
    assert pending_state.rollback_pending is True

    resumed_state = resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=bundle,
        initial_state=pending_state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=root,
    )
    assert resumed_state.rollback_pending is False
    assert resumed_state.rollback_ref is None

    assert not (root / "assets" / "binary_blob.bin").exists()
    assert not (root / "tools" / "deploy_tool.sh").exists()
    assert not (root / "docs" / "new_specification.txt").exists()

    assert _snapshot_tree(root, exclude_ignored=True) == prework_snapshot

    ignored_log = root / "activity.log"
    assert "pre-work activity log line\n" in ignored_log.read_text(encoding="utf-8")
    assert "iteration 1 runtime log line\n" in ignored_log.read_text(encoding="utf-8")

    agent_state = root / ".agent" / "state.json"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development", "step": 2}\n'

    _assert_proposal_restorable_exact(root, seal.ref)
