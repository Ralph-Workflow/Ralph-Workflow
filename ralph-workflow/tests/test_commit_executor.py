"""Tests for commit executor detached HEAD reattachment and git state repair."""

from __future__ import annotations

import functools
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Actor, Repo

from ralph.git.commit_result import CommitCreationResult
from ralph.git.proposal_seal import rollback, seal_pending
from ralph.pipeline.commit_executor import (
    execute_commit_effect,
    get_last_commit_error,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

pytestmark = pytest.mark.timeout_seconds(5)

#: Branch the shared template repository is created on.
_TEMPLATE_BRANCH = "main"

#: Settings applied once, while the shared template is built. ``gc.auto=0``
#: keeps git from packing refs behind the tests' back, ``core.fsync=none``
#: takes the per-write fsync out of the commit path, and
#: ``commit.gpgSign=false`` keeps the commit path free of a signing prompt.
_TEMPLATE_CONFIG: tuple[tuple[str, str, str], ...] = (
    ("user", "name", "Test User"),
    ("user", "email", "test@example.com"),
    ("gc", "auto", "0"),
    ("core", "fsync", "none"),
    ("commit", "gpgSign", "false"),
)

#: Entries that never need to travel with a repository copy. ``git init``
#: writes a directory of inert sample hooks plus a description file; none of
#: them take part in the behaviour under test, and dropping them keeps the
#: per-test copy small.
_COPY_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")


@pytest.fixture(autouse=True)
def _without_unobserved_commit_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the unobserved commit-evidence snapshot out of every test.

    ``execute_commit_effect`` snapshots the changed paths through
    ``build_commit_evidence_bundle``. That snapshot exists to feed the
    residual-work comparison, which runs only when a caller supplies
    ``has_residual_work_fn`` -- no test in this module does -- and it also
    renders the pending diff, which no assertion here reads. Replacing the
    two snapshot helpers therefore removes six ``git`` subprocesses per
    commit without changing any observable outcome.
    """
    from ralph.prompts import commit_evidence

    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "")
    monkeypatch.setattr(commit_evidence, "list_changed_paths", lambda _root: [])


@functools.lru_cache(maxsize=1)
def _get_commit_executor_template() -> Path:
    """Build the shared template repository once per session.

    The template already carries the commit identity, the ``.agent/`` ignore
    rule every Ralph workspace has, and the initial commit, so a test pays
    for a copy instead of for ``git init`` plus a fresh commit.
    """
    base = Path(tempfile.mkdtemp(prefix="commit-executor-template-"))
    repo = Repo.init(base, initial_branch=_TEMPLATE_BRANCH)
    writer = repo.config_writer()
    for section, option, value in _TEMPLATE_CONFIG:
        writer.set_value(section, option, value)
    writer.release()
    actor = Actor("Test User", "test@example.com")
    (base / "initial.txt").write_text("initial content", encoding="utf-8")
    # ``.agent/`` is gitignored in every Ralph workspace: the pipeline's
    # runtime state enters neither the sealed tree nor the commit, so the
    # sealed tree and the committed tree stay comparable.
    (base / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    repo.index.add(["initial.txt", ".gitignore"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.close()
    return base


@functools.cache
def _template_for_branch(branch: str) -> Path:
    """Return a template repository whose only branch is ``branch``.

    The branch rename is paid once per distinct branch name for the whole
    session instead of once per test.
    """
    base = _get_commit_executor_template()
    if branch == _TEMPLATE_BRANCH:
        return base
    variant = Path(tempfile.mkdtemp(prefix=f"commit-executor-template-{branch}-"))
    shutil.rmtree(variant)
    shutil.copytree(base, variant)
    repo = Repo(variant)
    repo.git.branch("-M", branch)
    repo.close()
    return variant


def _init_test_git_repo(repo_root: Path, branch: str = "main") -> Repo:
    """Copy the shared template for ``branch`` into ``repo_root`` and open it."""
    template = _template_for_branch(branch)
    shutil.copytree(template, repo_root, dirs_exist_ok=True, ignore=_COPY_IGNORE)
    return Repo(repo_root)


def _create_commit_message_artifact(repo_root: Path, message: str = "fix: update file") -> Path:
    artifact = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact


def _proposal_refs(repo_root: Path) -> list[str]:
    """Return the proposal refs that exist in ``repo_root``.

    The proposal namespace is written by the commit sweep and by the
    proposal seal, and both keep their refs loose, so the refs are read
    straight from the repository instead of paying for a
    ``git for-each-ref`` subprocess on every observation.
    """
    git_dir = repo_root / ".git"
    proposals = git_dir / "refs" / "ralph" / "proposals"
    refs: set[str] = set()
    if proposals.is_dir():
        for path in proposals.rglob("*"):
            if path.is_file():
                refs.add(f"refs/ralph/proposals/{path.relative_to(proposals).as_posix()}")
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for raw_line in packed.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1].startswith("refs/ralph/proposals/"):
                refs.add(parts[1])
    return sorted(refs)


def _write_proposal_ref(repo_root: Path, ref: str, sha: str) -> None:
    """Create the loose ref ``ref`` pointing at ``sha``.

    Equivalent to ``git update-ref`` for this namespace: git keeps such
    refs loose and records no reflog for them.
    """
    path = repo_root / ".git" / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sha}\n", encoding="utf-8")


def _policy_bundle_with_retention(retention: int) -> PolicyBundle:
    """Return the bundled default policy with ``proposal_retention`` overridden."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    commit_review = bundle.pipeline.commit_review.model_copy(
        update={"proposal_retention": retention}
    )
    pipeline = bundle.pipeline.model_copy(update={"commit_review": commit_review})
    return bundle.model_copy(update={"pipeline": pipeline})


def test_detached_head_reattaches_to_branch_before_commit(tmp_path: Path) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-branch")
    initial_sha = repo.head.commit.hexsha

    # Detach HEAD
    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked file changes
    test_file = tmp_path / "initial.txt"
    test_file.write_text("modified content", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: update initial file")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature-branch",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-branch"
    assert repo.head.commit.message.splitlines()[0] == "feat: update initial file"


def test_detached_head_resolves_branch_from_opts(tmp_path: Path) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-opts")
    initial_sha = repo.head.commit.hexsha

    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    work_file = tmp_path / "initial.txt"
    work_file.write_text("new work", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: add work file")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        feature_branch="feature-opts",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-opts"


def test_detached_head_resolves_branch_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-env")
    initial_sha = repo.head.commit.hexsha

    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    monkeypatch.setenv("FEATURE_BRANCH", "feature-env")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("env work", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: env work")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-env"


def test_repairable_git_state_stale_index_lock_repaired_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_test_git_repo(tmp_path, "main")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("updated work", encoding="utf-8")

    # Create stale index.lock with dead PID. The age-gated recovery
    # refuses to unlink locks younger than ``_STALE_GIT_LOCK_AGE_SECONDS``
    # (10s) because the holder cannot be proven dead from the file
    # contents alone, so backdate the mtime past the 10s threshold
    # rather than waiting in the test body.
    git_dir = tmp_path / ".git"
    index_lock = git_dir / "index.lock"
    # Write stale index.lock (older than 10s age gate)
    index_lock.write_text("99999999\n", encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(index_lock, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git._lock_recovery._lock_has_no_live_holder", lambda *_a, **_kw: "dead"
    )
    monkeypatch.setattr("ralph.git.operations._lock_has_no_live_holder", lambda *_a, **_kw: "dead")
    message_file = _create_commit_message_artifact(tmp_path, "feat: lock recovery")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    assert not index_lock.exists()


def test_commit_creation_failure_records_last_error(tmp_path: Path) -> None:
    _init_test_git_repo(tmp_path, "main")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("content", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: failing commit")

    mock_create_commit = MagicMock(
        return_value=CommitCreationResult.failed("simulated commit failure")
    )

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        create_commit_fn=mock_create_commit,
    )

    assert event == PipelineEvent.COMMIT_FAILURE
    last_err = get_last_commit_error()
    assert last_err is not None
    assert "simulated commit failure" in last_err


def test_detached_head_commit_failure_not_routed_to_failed_terminal_and_clears_completed(
    tmp_path: Path,
) -> None:
    """Criterion 11: commit failure on detached HEAD is not routed to failed_terminal and does not report completed."""
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy

    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    repo = _init_test_git_repo(tmp_path, "feature-branch")
    initial_sha = repo.head.commit.hexsha

    # Detach HEAD
    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    work_file = tmp_path / "initial.txt"
    work_file.write_text("modified for failure test", encoding="utf-8")
    message_file = _create_commit_message_artifact(tmp_path, "feat: failing commit")

    # Simulate commit failure even after repair attempt
    mock_create_commit = MagicMock(
        return_value=CommitCreationResult.failed("GitOperationError: detached HEAD commit failure")
    )

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature-branch",
        create_commit_fn=mock_create_commit,
    )

    assert event == PipelineEvent.COMMIT_FAILURE
    last_err = get_last_commit_error()
    assert last_err is not None

    # Verify reducer routing: not failed_terminal, and pending_cycle_outcome is cleared
    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_review",
        pending_cycle_outcome="completed",
        last_error=last_err,
    )
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase != "failed_terminal", (
        "Must route to automated recovery, not failed_terminal"
    )
    assert next_state.phase in ("development_commit_review", "commit_recovery")
    assert next_state.pending_cycle_outcome is None, (
        "Phase display must not report completed for failed commit"
    )


def test_detached_head_auto_repaired_commit_succeeds_without_failed_terminal(
    tmp_path: Path,
) -> None:
    """Criterion 11: commit on detached HEAD that is auto-repaired succeeds and does not route to failed_terminal."""
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy

    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    repo = _init_test_git_repo(tmp_path, "feature-branch")
    initial_sha = repo.head.commit.hexsha

    # Detach HEAD
    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    work_file = tmp_path / "initial.txt"
    work_file.write_text("modified for auto-repair success", encoding="utf-8")
    message_file = _create_commit_message_artifact(tmp_path, "feat: auto repaired commit")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature-branch",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-branch"

    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_review",
        pending_cycle_outcome="completed",
    )
    next_state, _ = reduce(state, event, policy)
    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_review")


def test_sha_bound_staging_succeeds_when_tree_sha_matches(tmp_path: Path) -> None:
    from ralph.config.bootstrap import auto_seed_default_git_exclude
    from ralph.git.operations import write_tree

    repo = _init_test_git_repo(tmp_path, "main")
    auto_seed_default_git_exclude(tmp_path)
    work_file = tmp_path / "initial.txt"
    work_file.write_text("updated content for sha bound test", encoding="utf-8")
    repo.index.add(["initial.txt"])
    expected_tree = write_tree(tmp_path)

    message_file = _create_commit_message_artifact(tmp_path, "feat: sha bound test")
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file), expected_tree_sha=expected_tree),
        tmp_path,
        branch="main",
    )
    assert event == PipelineEvent.COMMIT_SUCCESS


def test_sha_bound_staging_fails_when_tree_sha_mismatches(tmp_path: Path) -> None:
    _ = _init_test_git_repo(tmp_path, "main")
    work_file = tmp_path / "initial.txt"
    work_file.write_text("updated content for sha bound failure test", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: sha bound test")
    event = execute_commit_effect(
        CommitEffect(
            message_file=str(message_file),
            expected_tree_sha="1111111111111111111111111111111111111111",
        ),
        tmp_path,
        branch="main",
    )
    assert event == PipelineEvent.COMMIT_FAILURE
    last_err = get_last_commit_error()
    assert last_err is not None
    assert "does not match approved tree SHA" in last_err


def test_execute_commit_effect_prunes_proposals_on_success(tmp_path: Path) -> None:
    _ = _init_test_git_repo(tmp_path, "main")
    work_file = tmp_path / "initial.txt"
    work_file.write_text("updated content for prune test", encoding="utf-8")

    pruned = False
    captured: dict[str, object] = {}

    def _mock_prune(root: Path, **kwargs: object) -> list[str]:
        nonlocal pruned
        pruned = True
        captured["root"] = root
        captured.update(kwargs)
        return ["refs/ralph/proposals/test/1"]

    message_file = _create_commit_message_artifact(tmp_path, "feat: prune test")
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="main",
        prune_fn=_mock_prune,
    )
    assert event == PipelineEvent.COMMIT_SUCCESS
    assert pruned is True
    # The sweep receives the retention limit from
    # ``commit_review.proposal_retention`` and the run scope it covers.
    assert captured["root"] == tmp_path
    assert captured["max_retained"] == 10
    assert captured["run_id"] is None
    assert captured["keep_refs"] == ()


def test_rejected_proposal_ref_survives_a_later_successful_commit(tmp_path: Path) -> None:
    """A rejected iteration's proposal ref must survive the next successful commit.

    The documented recovery for a wrongly rejected iteration is
    ``git checkout <ref> -- .``. The post-commit proposal sweep must
    therefore never delete the ref that recovery depends on.
    """
    _init_test_git_repo(tmp_path, "main")
    # ``.agent/`` is gitignored in every Ralph workspace (the shared
    # template carries the rule): the pipeline's runtime state enters
    # neither the sealed tree nor the commit, so the sealed tree and the
    # committed tree stay comparable.
    tracked = tmp_path / "initial.txt"
    rejected_extra = tmp_path / "rejected_extra.bin"

    # --- iteration 1: the commit review rejects the work ------------------
    tracked.write_text("rejected content\n", encoding="utf-8")
    rejected_extra.write_bytes(b"rejected\x00bytes\x01")
    rejected_seal = seal_pending(tmp_path, "run-reject", 1)
    assert rejected_seal.ref == "refs/ralph/proposals/run-reject/1"
    rollback(tmp_path, rejected_seal.ref)
    assert tracked.read_text(encoding="utf-8") == "initial content"
    assert not rejected_extra.exists()

    # --- iteration 2: a later iteration commits successfully ---------------
    tracked.write_text("approved content\n", encoding="utf-8")
    approved_seal = seal_pending(tmp_path, "run-reject", 2)
    message_file = _create_commit_message_artifact(tmp_path, "feat: approved work")
    event = execute_commit_effect(
        CommitEffect(
            message_file=str(message_file),
            expected_tree_sha=approved_seal.tree_sha,
        ),
        tmp_path,
        branch="main",
    )
    assert event == PipelineEvent.COMMIT_SUCCESS

    # The rejected iteration's ref survived the successful commit.
    assert "refs/ralph/proposals/run-reject/1" in _proposal_refs(tmp_path)

    # The documented recovery still restores the rejected tree byte-for-byte.
    repo = Repo(tmp_path)
    repo.git.checkout(rejected_seal.ref, "--", ".")
    assert tracked.read_text(encoding="utf-8") == "rejected content\n"
    assert rejected_extra.read_bytes() == b"rejected\x00bytes\x01"


def test_commit_prune_honours_the_policy_proposal_retention_limit(tmp_path: Path) -> None:
    """``commit_review.proposal_retention`` bounds the refs the sweep keeps."""
    repo_root = tmp_path / "repo"
    _init_test_git_repo(repo_root, "main")

    repo = Repo(repo_root)
    head_sha = repo.head.commit.hexsha
    rejection_ref = "refs/ralph/proposals/run-x/4"

    def _setup_proposals() -> None:
        for iteration in (1, 2, 3):
            _write_proposal_ref(repo_root, f"refs/ralph/proposals/run-x/{iteration}", head_sha)
        if rejection_ref not in _proposal_refs(repo_root):
            tracked = repo_root / "initial.txt"
            tracked.write_text("rejected content\n", encoding="utf-8")
            rejected_seal = seal_pending(repo_root, "run-x", 4)
            rollback(repo_root, rejected_seal.ref)

    content_index = 0

    def _commit_with_policy(policy_bundle: PolicyBundle | None) -> list[str]:
        nonlocal content_index
        content_index += 1
        tracked = repo_root / "initial.txt"
        tracked.write_text(f"approved content {content_index}\n", encoding="utf-8")
        message_file = _create_commit_message_artifact(repo_root, "feat: retention sweep")
        event = execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            repo_root,
            branch="main",
            policy_bundle=policy_bundle,
        )
        assert event == PipelineEvent.COMMIT_SUCCESS
        return _proposal_refs(repo_root)

    _setup_proposals()
    tight_refs = _commit_with_policy(_policy_bundle_with_retention(1))

    _setup_proposals()
    wide_refs = _commit_with_policy(_policy_bundle_with_retention(10))

    policy_file = repo_root / ".agent" / "pipeline.toml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text("[commit_review]\nproposal_retention = 1\n", encoding="utf-8")
    _setup_proposals()
    workspace_refs = _commit_with_policy(None)

    assert rejection_ref in tight_refs
    assert rejection_ref in wide_refs
    assert rejection_ref in workspace_refs
    assert [ref for ref in tight_refs if ref != rejection_ref] == []
    assert [ref for ref in workspace_refs if ref != rejection_ref] == []
    assert "refs/ralph/proposals/run-x/1" in wide_refs


def test_a_rejected_proposal_ref_survives_a_later_commit_of_the_same_tree(
    tmp_path: Path,
) -> None:
    """A rejected ref stays protected even when its tree is committed later.

    The REJECTION is what makes the ref recoverable, not the tree it
    carries: a later iteration that reproduces the same content and gets
    approved must not make the rejected ref prunable. Without the explicit
    rejection mark the sweep would prune it, because its tree is now part of
    the commit history.
    """
    _init_test_git_repo(tmp_path, "main")
    tracked = tmp_path / "initial.txt"

    # --- iteration 1: the commit review rejects the work ------------------
    tracked.write_text("shared content\n", encoding="utf-8")
    rejected_seal = seal_pending(tmp_path, "run-reject", 1)
    rollback(tmp_path, rejected_seal.ref)
    assert tracked.read_text(encoding="utf-8") == "initial content"

    # --- iteration 2: a later iteration commits the SAME content ----------
    tracked.write_text("shared content\n", encoding="utf-8")
    approved_seal = seal_pending(tmp_path, "run-reject", 2)
    assert approved_seal.tree_sha == rejected_seal.tree_sha
    message_file = _create_commit_message_artifact(tmp_path, "feat: approved work")
    event = execute_commit_effect(
        CommitEffect(
            message_file=str(message_file),
            expected_tree_sha=approved_seal.tree_sha,
        ),
        tmp_path,
        branch="main",
    )
    assert event == PipelineEvent.COMMIT_SUCCESS

    # The rejected iteration's ref survived even though its tree was
    # committed by the later iteration.
    surviving = _proposal_refs(tmp_path)
    assert "refs/ralph/proposals/run-reject/1" in surviving

    # The documented recovery still restores the rejected tree.
    repo = Repo(tmp_path)
    try:
        repo.git.checkout(rejected_seal.ref, "--", ".")
    finally:
        repo.close()
    assert tracked.read_text(encoding="utf-8") == "shared content\n"


def test_the_proposal_identity_advances_with_every_rejection_attempt() -> None:
    """Two rejections must seal two DIFFERENT proposal refs.

    The iteration segment of a proposal ref must never be reused: a second
    rejection overwriting the first one's ref would destroy the recovery
    path (``git checkout <ref> -- .``) of the first rejection.
    """
    from ralph.pipeline.effect_router import commit_review_proposal_identity
    from ralph.pipeline.state import PipelineState

    first = commit_review_proposal_identity(PipelineState(phase="development"))
    second = commit_review_proposal_identity(
        PipelineState(phase="development", commit_review_rejections=1)
    )
    later_cycle = commit_review_proposal_identity(
        PipelineState(phase="development", outer_progress={"iteration": 1})
    )

    assert first[0] == second[0] == later_cycle[0]
    assert len({first[1], second[1], later_cycle[1]}) == 3


def test_two_rejections_seal_two_distinct_restorable_proposal_refs(tmp_path: Path) -> None:
    """Two rejections seal two refs that both stay resolvable and restorable."""
    _init_test_git_repo(tmp_path, "main")
    tracked = tmp_path / "initial.txt"

    tracked.write_text("first attempt\n", encoding="utf-8")
    first = seal_pending(tmp_path, "run", "1-1")
    rollback(tmp_path, first.ref)

    tracked.write_text("second attempt\n", encoding="utf-8")
    second = seal_pending(tmp_path, "run", "1-2")

    assert first.ref != second.ref
    repo = Repo(tmp_path)
    try:
        assert repo.git.rev_parse(first.ref)
        assert repo.git.rev_parse(second.ref)
        repo.git.checkout(first.ref, "--", ".")
    finally:
        repo.close()
    assert tracked.read_text(encoding="utf-8") == "first attempt\n"
