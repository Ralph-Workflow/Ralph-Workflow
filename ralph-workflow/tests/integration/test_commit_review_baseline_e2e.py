"""End-to-end proof that a rejection rollback restores the run-start baseline.

``HEAD`` is the restore point only for pipeline-made commits. A run that
starts on a tree with uncommitted user changes would lose EXACTLY that work
to a ``reset``-style restore, so the engine records a BASELINE SEAL at run
start whenever the tree is dirty, and a rejection rollback restores to that
baseline when one exists.

The proofs drive the public run-loop seam
(``ralph.pipeline.run_loop.resolve_initial_state``) and the public
``ralph.git.proposal_seal`` primitives against real temporary Git
repositories: no sleeps, no network, no private production imports, and no
mutation of any repository's real ``.git/index`` beyond what the rollback
itself performs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.proposal_seal import prune, seal_pending
from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.required_auto_integrate_e2e]

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"

#: The content the temporary repository's initial commit holds.
_HEAD_README = "test"
#: The user's pre-existing uncommitted edit of a TRACKED file.
_USER_README = "user's uncommitted edit\n"
#: The user's pre-existing uncommitted NEW file.
_USER_NOTES = "user's uncommitted new file\n"
#: The file the rejected iteration added.
_PROPOSAL_ONLY = "added by the rejected iteration\n"


def _bundle() -> PolicyBundle:
    """Return the bundled default policy."""
    return load_policy(_DEFAULTS_DIR)


def _head_sha(repo_root: Path) -> str:
    """Return the SHA HEAD points at, read from the loose ref file."""
    head_path = repo_root / ".git" / "HEAD"
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = repo_root / ".git" / head.removeprefix("ref: ")
        return ref_path.read_text(encoding="utf-8").strip()
    return head


def _ref_file(repo_root: Path, ref: str) -> Path:
    """Return the loose-ref file backing ``ref``."""
    return repo_root / ".git" / ref


def _start_run(repo_root: Path) -> PipelineState:
    """Start a fresh run against ``repo_root`` (the run-start baseline seam)."""
    return resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=_bundle(),
        initial_state=None,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=repo_root,
    )


def _reject(repo_root: Path, start_state: PipelineState, run_id: str) -> PipelineState:
    """Seal the pending state, reject it, and return the rollback-pending state.

    The developer changes a tracked file and adds a file of their own; the
    gate rejects the sealed proposal, so the reducer asks the pipeline to
    roll the tree back.
    """
    readme = repo_root / "README.md"
    proposal_only = repo_root / "proposal_only.txt"
    readme.write_text("developer change\n", encoding="utf-8")
    proposal_only.write_text(_PROPOSAL_ONLY, encoding="utf-8")

    seal = seal_pending(repo_root, run_id=run_id, iteration="0")
    rejected_state, _ = reduce(
        start_state.copy_with(phase="development_commit_review"),
        CommitReviewRejected(reason="rejected proposal", ref=seal.ref, hard=True),
        _bundle().pipeline,
    )
    assert rejected_state.rollback_pending is True
    assert rejected_state.rollback_ref == seal.ref
    return rejected_state


def _run_pending_rollback(repo_root: Path, state: PipelineState) -> PipelineState:
    """Execute the pending rollback through the public run-loop seam."""
    return resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=_bundle(),
        initial_state=state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=repo_root,
    )


def test_dirty_run_start_rollback_restores_the_baseline_not_head(tmp_git_repo: Path) -> None:
    """A run that starts dirty restores the user's work, not HEAD's content."""
    repo_root = Path(tmp_git_repo)
    readme = repo_root / "README.md"
    notes = repo_root / "user_notes.txt"
    proposal_only = repo_root / "proposal_only.txt"
    readme.write_text(_USER_README, encoding="utf-8")
    notes.write_text(_USER_NOTES, encoding="utf-8")
    user_readme_bytes = readme.read_bytes()
    user_notes_bytes = notes.read_bytes()

    # The run starts here, on the dirty tree: the baseline seal is captured.
    start_state = _start_run(repo_root)
    baseline_ref = start_state.rollback_baseline_ref
    assert baseline_ref is not None, "a dirty tree at run start must record a baseline seal"
    baseline_file = _ref_file(repo_root, baseline_ref)
    assert baseline_file.exists(), "the recorded baseline must resolve"

    rejected_state = _reject(repo_root, start_state, run_id="run-dirty")
    assert rejected_state.rollback_baseline_ref == baseline_ref

    head_before = _head_sha(repo_root)
    rolled_back = _run_pending_rollback(repo_root, rejected_state)
    assert rolled_back.rollback_pending is False

    # The user's pre-existing uncommitted work survives the rollback.
    assert readme.read_bytes() == user_readme_bytes
    assert notes.read_bytes() == user_notes_bytes
    # The file the rejected iteration added is gone.
    assert not proposal_only.exists()
    # The rollback restored the BASELINE, so the tracked file is NOT HEAD's.
    assert readme.read_text(encoding="utf-8") != _HEAD_README
    # The rollback created no commit: the baseline is never a commit source.
    assert _head_sha(repo_root) == head_before


def test_clean_run_start_rollback_restores_head(tmp_git_repo: Path) -> None:
    """A run that starts clean has no baseline and restores to HEAD."""
    repo_root = Path(tmp_git_repo)
    readme = repo_root / "README.md"
    proposal_only = repo_root / "proposal_only.txt"
    assert readme.read_text(encoding="utf-8") == _HEAD_README

    start_state = _start_run(repo_root)
    assert start_state.rollback_baseline_ref is None, "a clean tree records no baseline seal"

    rejected_state = _reject(repo_root, start_state, run_id="run-clean")
    rolled_back = _run_pending_rollback(repo_root, rejected_state)
    assert rolled_back.rollback_pending is False

    # The tracked file is back to HEAD's content.
    assert readme.read_text(encoding="utf-8") == _HEAD_README
    # The file the rejected iteration added is gone.
    assert not proposal_only.exists()


def test_baseline_seal_never_writes_the_real_index(tmp_git_repo: Path) -> None:
    """The baseline seal builds its tree through the temporary-index route."""
    repo_root = Path(tmp_git_repo)
    readme = repo_root / "README.md"
    notes = repo_root / "user_notes.txt"
    index_path = repo_root / ".git" / "index"
    # The tracked file carries the user's uncommitted edit, so a seal that
    # staged into the REAL index would change the index bytes: the entry for
    # the edited file would be rewritten with the new content.
    readme.write_text(_USER_README, encoding="utf-8")
    notes.write_text(_USER_NOTES, encoding="utf-8")
    index_before = index_path.read_bytes()

    start_state = _start_run(repo_root)

    assert start_state.rollback_baseline_ref is not None
    assert index_path.read_bytes() == index_before, (
        "the baseline seal must build its tree through a temporary index and "
        "never write the repository's real .git/index"
    )


def test_the_baseline_ref_survives_the_proposal_sweep(tmp_git_repo: Path) -> None:
    """The baseline ref lives outside the proposal namespace, so it is never pruned."""
    repo_root = Path(tmp_git_repo)
    notes = repo_root / "user_notes.txt"
    notes.write_text(_USER_NOTES, encoding="utf-8")

    start_state = _start_run(repo_root)
    baseline_ref = start_state.rollback_baseline_ref
    assert baseline_ref is not None
    assert not baseline_ref.startswith("refs/ralph/proposals/")

    proposal = seal_pending(repo_root, run_id="run-sweep", iteration="0")
    pruned = prune(repo_root)

    assert proposal.ref in pruned, "the proposal sweep must still prune the proposal refs it owns"
    baseline_file = _ref_file(repo_root, baseline_ref)
    assert baseline_file.exists(), "the baseline ref must be excluded from proposal pruning"
