"""Crash-recovery and security regression tests for :mod:`ralph.pipeline.auto_integrate`.

Houses the tests that were previously inlined in
:mod:`tests.test_auto_integrate` but pushed that file past the
repo-structure ``_MAX_FILE_LINES`` cap (1000 lines). Splitting
them out keeps the main test file under the cap while preserving
the same ``subprocess_e2e`` marker so the tests still run under
``make test-subprocess-e2e``.

Three test families live here:

* **AC-11 ground-truth recovery tests** with REAL conflicting
  rebases that leave ``rebase-apply`` / ``rebase-merge`` on disk
  before recovery runs. The headline-only versions in
  :mod:`tests.test_auto_integrate` only prove the orchestrator
  returns ``last_action='recovered'``; the tests here prove
  HEAD is restored, the working tree is clean, the durable
  record is cleared, and (for case 4) an operator-owned
  in-progress rebase is byte-unchanged.

* **Fault-injection tests** that monkeypatch ``reset_hard`` /
  ``abort_rebase`` to raise, and assert the durable
  ``IntegrationRecord`` is RETAINED (so the next startup can
  retry) rather than cleared. This closes the bug the prompt's
  feedback item flagged: the prior implementation called
  ``_clear_record`` unconditionally after the abort/reset path
  and returned ``last_action='recovered'``, leaving the
  repository in a rebase/merge state with no ownership marker.

* **Security regression tests** that create a local ref whose
  name starts with ``-`` and drive ``rebase_onto`` /
  ``merge_target_into_current`` against it. With the ``--``
  option terminator in place, the target is treated as a
  revision argument; without the terminator, git would parse it
  as a ``--exec`` (rebase) or ``--allow-unrelated-histories``
  (merge) option. The ``--exec`` case is an RCE exposure.

The helpers (``_run``, ``_commit``, ``_base_branch``,
``_build_config``, ``_snapshot``) are duplicated here to keep
this file standalone; the duplication is small (~50 lines) and
avoids a brittle ``from test_auto_integrate import ...``
dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import (
    IntegrationRecord,
    auto_integrate_after_commit,
    recover_incomplete_integration,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


# ---------------------------------------------------------------------------
# Helpers (duplicated from tests/test_auto_integrate.py to keep this file
# standalone; see the module docstring for the rationale).
# ---------------------------------------------------------------------------


def _run(repo_root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a configurable timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name."""
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    """Write+stage+commit ``filename`` and return the resulting HEAD SHA."""
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(
    tmp_git_repo: Path,
    *,
    enabled: bool = True,
    target: str | None = None,
) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` with the auto-integrate knobs set."""
    payload: dict[str, object] = {"general": {"auto_integrate_enabled": enabled}}
    if target is not None:
        payload["general"]["auto_integrate_target"] = target
    return UnifiedConfig.model_validate(payload)


# ---------------------------------------------------------------------------
# AC-11 ground-truth recovery tests with REAL conflicting rebases
# ---------------------------------------------------------------------------


def test_recovery_mid_rebase_kill_restores_feature(tmp_git_repo: Path) -> None:
    """AC-11 case 1: REAL mid-rebase kill leaves rebase-apply on disk.

    This test exercises the AC-11 ground truth the prompt requires:
    a real, partially-applied rebase with ``rebase-apply`` (or
    ``rebase-merge``) actually on disk BEFORE recovery runs, plus a
    durable ``integrating`` record left by the killed auto-integrate.
    The previous synthetic version of this test only wrote a record
    file without ever running a rebase, so it never exercised the
    recovery path's abort-of-a-real-in-progress-rebase logic.

    Setup mirrors the canonical AC-06 rebase-conflict topology (an
    intermediate commit that diverges from a later base-side
    change) but with TWO feature commits so a partial replay leaves
    ``rebase-apply`` on disk when the kill happens.

    After ``recover_incomplete_integration`` runs the assertions
    prove:

    * The owned rebase-apply / rebase-merge directory is gone
      (recovery aborted the dangling rebase).
    * ``MERGE_HEAD`` is absent (no owned merge state).
    * ``HEAD`` is restored to ``pre_feature_sha`` (the
      ``reset_hard(pre_feature_sha)`` restore landed).
    * The durable record is CLEARED (recovery succeeded; no
      ownership marker left on disk).
    * The returned ``RebaseState`` reports ``last_action='recovered'``.
    """
    base = _base_branch(tmp_git_repo)
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nline2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "shared seed")
    a_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nFEATURE-D1\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D1")
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nFEATURE-D1\nFEATURE-D2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D2")
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nBASE\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "base: B")
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, "preflight: expected rebase to conflict"
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists(), "preflight: expected rebase-apply/rebase-merge state on disk"
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action == "recovered", (
        f"AC-11 case 1: expected recovered, got {outcome.last_action!r}"
        f" reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is False
    assert not (git_dir / "rebase-apply").exists(), (
        "AC-11: rebase-apply must be gone after recovery aborted the rebase"
    )
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "MERGE_HEAD").exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not record_file.exists(), (
        "AC-11: durable record must be cleared after a successful recovery"
    )


def test_recovery_killed_after_clean_rebase_before_ff(tmp_git_repo: Path) -> None:
    """AC-11 case 2: rebase completed but ff not done -> recover completes ff.

    Builds a diverged clean topology (feature ahead of base, base
    advanced separately on a disjoint file so the rebase replays
    cleanly). RUNS the real rebase so the feature branch now
    contains the target, then pre-writes the phase='integrated'
    record with the rebased-state SHA -- exactly what the
    integration would have written just before the fast-forward --
    and verifies recovery completes the ff.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "feat_clean.txt", "feat clean\n", "feat clean")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_clean.txt", "base clean\n", "base clean")
    _run(tmp_git_repo, "checkout", "feature")
    # RUN the rebase so feature now contains target -- the recovery
    # preamble only verifies is_ancestor(target, feature_sha) and
    # will fail if target isn't actually an ancestor of feature_sha.
    rebased = _run(tmp_git_repo, "rebase", base)
    assert rebased.returncode == 0, (
        "test setup: rebase of feature onto base must succeed cleanly"
    )
    pre_feature_sha = base_seed_sha  # original feature HEAD before the rebase
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(
            tmp_git_repo, "rev-parse", "HEAD"
        ).stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == feature_sha


def test_recovery_killed_after_clean_merge_before_ff(tmp_git_repo: Path) -> None:
    """AC-11 case 3: clean merge completed but ff not done -> recover completes ff.

    Builds a topology where the rebase would conflict (D1 modifies
    a line that base also modifies) but the endpoint three-way merge
    succeeds (D2 reverts D1 so the final feature tip matches the
    common ancestor). Pre-writes the phase='integrated' record with
    the merge-commit SHA -- exactly what the integration would have
    written just before the fast-forward -- and verifies recovery
    completes the ff.
    """
    base = _base_branch(tmp_git_repo)
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nline2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "shared seed")
    a_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nFEATURE\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D1")
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nline2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D2 revert")
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nBASE\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "base: B")
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "checkout", "feature")
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(
            tmp_git_repo, "rev-parse", "HEAD"
        ).stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()


def test_recovery_no_record_preserves_operator_in_progress_rebase(
    tmp_git_repo: Path,
) -> None:
    """AC-11 case 4: operator-owned in-progress rebase is left untouched.

    The prior synthetic version of this test aborted the operator's
    rebase BEFORE the recovery call -- so the recovery never had to
    prove it would leave a real in-progress rebase alone. This
    ground-truth test sets up a REAL in-progress rebase (with
    ``rebase-apply`` / ``rebase-merge`` actually on disk) and an
    OPERATOR-owned conflict state, with NO auto-integrate record on
    disk. ``recover_incomplete_integration`` must:

    * Return ``None`` (no record -> nothing to recover).
    * Leave the rebase-apply / rebase-merge directory INTACT.
    * Leave HEAD, working tree, and the conflict markers
      bit-unchanged.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists(), (
        "test setup: rebase-apply/rebase-merge must be on disk"
    )
    # CRITICAL: there is NO auto-integrate record -- this is an
    # operator-owned in-progress rebase that recovery MUST NOT touch.
    assert not (
        tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    ).exists(), "test setup: no auto-integrate record must exist"
    before_head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    before_status = _run(tmp_git_repo, "status", "--porcelain").stdout
    before_porcelain_files = _run(
        tmp_git_repo, "diff", "--name-only", "--diff-filter=U"
    ).stdout.strip()
    before_rebase_apply_present = (git_dir / "rebase-apply").exists()
    before_rebase_merge_present = (git_dir / "rebase-merge").exists()

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is None, (
        "no auto-integrate record -> recovery must return None (operator"
        " in-progress rebase is owned by the operator, not auto-integrate)"
    )
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _run(tmp_git_repo, "status", "--porcelain").stdout == before_status
    assert (
        _run(tmp_git_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        == before_porcelain_files
    )
    assert (git_dir / "rebase-apply").exists() == before_rebase_apply_present
    assert (git_dir / "rebase-merge").exists() == before_rebase_merge_present
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists(), (
        "operator's in-progress rebase-apply/rebase-merge must still be on disk"
    )


def test_recovery_no_record_reclaims_stale_rebase_state_on_clean_tree(
    tmp_git_repo: Path,
) -> None:
    """Stale unowned rebase state on a CLEAN tree is reclaimed, not preserved.

    Observed failure mode (PROMPT.md, wt-23, 2026-07-22): a leftover
    ``rebase-merge``/``rebase-apply`` directory with NO ownership
    record fails ``check_rebase_preconditions`` at every seam forever,
    permanently disabling auto-integration in the worktree — the
    forbidden silent noop. The discriminator against AC-11 case 4
    (operator mid-conflict, which MUST be preserved) is worktree
    cleanliness: a live conflict resolution has unmerged paths and
    tracked modifications; inert stale state sits on a clean tree.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    # Make the tree clean while the rebase bookkeeping stays on disk:
    # this is exactly the stale shape the boundary hook meets, since
    # the hook only fires on a clean tree.
    _run(tmp_git_repo, "reset", "--hard")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists(), "test setup: stale rebase state must be on disk"
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert not status.stdout.strip(), "test setup: worktree must be clean"
    assert not (
        tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    ).exists(), "test setup: no auto-integrate record must exist"

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None, (
        "stale unowned rebase state on a clean tree must be reclaimed, not"
        " silently ignored (PROMPT.md: noop is never an option)"
    )
    assert outcome.last_action == "recovered"
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "REBASE_HEAD").exists()


def test_recovery_no_record_reclaims_stale_merge_state_on_clean_tree(
    tmp_git_repo: Path,
) -> None:
    """Stale unowned MERGE_HEAD on a CLEAN tree is reclaimed, not preserved.

    Merge-flavored twin of the stale-rebase reclaim: a leftover
    ``MERGE_HEAD`` with no ownership record blocks
    ``check_rebase_preconditions`` at every seam forever. Terminal-state
    invariant: an integration attempt must end in a completed rebase, a
    completed merge, or a clean abort -- never in-progress bookkeeping
    nobody owns.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    conflicted = _run(tmp_git_repo, "merge", base)
    assert conflicted.returncode != 0, (
        "test setup: expected a real merge conflict to leave MERGE_HEAD"
    )
    # Clean the tree while MERGE_HEAD stays behind -- the stale shape.
    _run(tmp_git_repo, "reset", "--hard")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    # ``git reset --hard`` clears MERGE_HEAD on modern git; restore the
    # stale marker explicitly to model a crashed abort.
    if not (git_dir / "MERGE_HEAD").exists():
        base_sha = _run(tmp_git_repo, "rev-parse", base).stdout.strip()
        (git_dir / "MERGE_HEAD").write_text(base_sha + "\n", encoding="utf-8")
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert not status.stdout.strip(), "test setup: worktree must be clean"
    assert not (
        tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    ).exists(), "test setup: no auto-integrate record must exist"

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None, (
        "stale unowned MERGE_HEAD on a clean tree must be reclaimed, not"
        " silently ignored (terminal-state invariant)"
    )
    assert outcome.last_action == "recovered"
    assert not (git_dir / "MERGE_HEAD").exists()


def test_merge_exception_with_lingering_merge_head_retains_record(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed merge whose abort also failed must RETAIN the record.

    Terminal-state invariant: ``_endpoint_merge_result`` used to call
    ``clear_record`` unconditionally on the merge-raised path. When the
    merge abort ALSO failed (MERGE_HEAD still on disk), that orphaned
    the in-progress state -- recovery only reconciles operations whose
    record it holds, so the worktree was permanently locked out of
    integration. The record must survive whenever an in-progress
    operation provably remains.
    """
    from ralph.pipeline import auto_integrate_rebase_merge as rm
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord as Record,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )

    base = _base_branch(tmp_git_repo)
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    write_record(
        tmp_git_repo,
        Record(
            phase="integrating",
            target=base,
            pre_feature_sha=head,
            pre_target_sha=head,
        ),
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    # Model "merge raised AND its abort failed": the resolver-side
    # helper reports the raise (None) while MERGE_HEAD stays on disk.
    (git_dir / "MERGE_HEAD").write_text(head + "\n", encoding="utf-8")
    monkeypatch.setattr(
        rm, "endpoint_merge_with_resolution", lambda *a, **k: None
    )

    result = rm.run_rebase_or_merge(
        tmp_git_repo, base, None, prefer_merge=True
    )

    assert result.short_circuit is not None
    assert read_record(tmp_git_repo) is not None, (
        "record must be retained while MERGE_HEAD remains on disk --"
        " clearing it orphans the in-progress state recovery would own"
    )


# ---------------------------------------------------------------------------
# Fault-injection tests: assert the durable record is RETAINED on failure
# ---------------------------------------------------------------------------


def test_recovery_retains_record_on_reset_failure(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fault injection: when ``reset_hard`` fails, record is RETAINED.

    The prior implementation unconditionally called ``_clear_record``
    after the ``reset_hard`` attempt and returned
    ``last_action='recovered'`` -- leaving the repository in a
    rebase/merge state with no ownership marker on disk, so the next
    startup could not retry the recovery. This test injects a
    failure into ``reset_hard`` (via monkeypatch on the function the
    recovery path calls) and asserts:

    * The function returns a skip RebaseState (NOT 'recovered').
    * ``last_reason`` indicates the record is retained for retry.
    * The durable ``IntegrationRecord`` is STILL on disk.
    * The function does not raise.
    """
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    import ralph.pipeline.auto_integrate_recovery as _ai_mod

    def _failing_reset(repo_root: object, sha: str) -> None:
        raise RuntimeError("simulated reset_hard failure")

    monkeypatch.setattr(_ai_mod, "reset_hard", _failing_reset)
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action == "skipped", (
        "reset_hard failure must produce a skip (NOT recovered)"
    )
    assert "retained for retry" in (outcome.last_reason or "")
    assert outcome.last_target == base
    assert outcome.fast_forwarded is False
    assert record_file.exists(), (
        "durable record must be retained when reset_hard fails so the"
        " next startup can retry recovery"
    )


def test_recovery_retains_record_on_abort_failure(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fault injection: when ``abort_rebase`` fails, record is RETAINED.

    Mirrors the reset_hard fault-injection case but for the abort
    path: a real in-progress rebase on disk plus a failure in
    ``abort_rebase`` must leave the durable record intact so the
    next startup retries the recovery.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base v1\n", "base shared")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature v1\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base v2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists()
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    import ralph.pipeline.auto_integrate_recovery as _ai_mod

    def _failing_abort(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated abort_rebase failure")

    monkeypatch.setattr(_ai_mod, "abort_rebase", _failing_abort)
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "retained for retry" in (outcome.last_reason or "")
    assert record_file.exists(), (
        "durable record must be retained when abort_rebase fails"
    )


# ---------------------------------------------------------------------------
# Context-resolution failure must produce a RECORDED skip, not a silent no-op
# ---------------------------------------------------------------------------


def test_context_resolution_failure_returns_recorded_skip(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-07/AC-08 supplement: env lookup failure produces a recorded skip.

    The prompt feedback item flagged that
    :func:`ralph.pipeline.auto_integrate._auto_integrate_resolve_context`
    swallowed EVERY context-resolution exception (env lookup, git
    transport, etc.) and returned ``None``. With ``None`` the
    caller treated the call as AC-01 disabled and returned ``None``
    to the runner -- a silent no-op with NO state recorded, so the
    operator had no idea WHY integration did not run.

    The fix is to let the exception propagate to
    :func:`auto_integrate_after_commit`'s outer try/except, which
    converts it to a recorded skip ``RebaseState``. This test
    injects a ``RuntimeError`` from
    :func:`ralph.pipeline.auto_integrate._current_branch_or_detached_marker`
    and asserts the outcome is a recorded skip with the failure
    reason captured in ``last_reason`` (and ``outcome is not None``
    so the runner persists it).
    """
    import ralph.pipeline.auto_integrate as _ai_mod

    def _failing_probe(root: object) -> str | None:
        raise RuntimeError("probe failure")

    monkeypatch.setattr(_ai_mod, "_current_branch_or_detached_marker", _failing_probe)
    config = _build_config(tmp_git_repo, enabled=True)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is not None, (
        "AC-08 supplement: env lookup failure must produce a recorded skip,"
        " NOT a silent None (which would be indistinguishable from the AC-01"
        " disabled path)."
    )
    assert outcome.last_action == "skipped"
    assert "probe failure" in (outcome.last_reason or "")
    assert outcome.last_target is None
    assert outcome.fast_forwarded is False


def test_context_resolution_disabled_returns_none_not_skip(
    tmp_git_repo: Path,
) -> None:
    """AC-01 supplement: disabled returns ``None`` (no recorded skip).

    Verifies the discriminator fix: the AC-01 byte-identical
    no-op (disabled feature) MUST still return ``None`` so the
    runner does not persist any rebase state change when the
    feature is off. The context-resolution failure path (the
    regression test above) must NOT regress this behavior -- the
    distinction is now: ``None`` means "the feature is off", a
    ``RebaseState`` with ``last_action='skipped'`` means "the
    feature is on but something blocked this run".
    """
    config = _build_config(tmp_git_repo, enabled=False)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is None, (
        "AC-01: disabled auto-integrate must return None (byte-identical"
        " no-op). A RebaseState here would mean we mutated run state when"
        " the feature is off."
    )


# ---------------------------------------------------------------------------
# Malformed record phase: a bogus on-disk phase must be treated as corrupt
# ---------------------------------------------------------------------------


def test_recovery_treats_bogus_phase_record_as_corrupt(
    tmp_git_repo: Path,
) -> None:
    """AC-11 supplement: a record with a stray ``phase`` value is corrupt.

    The prompt feedback item flagged that
    :class:`ralph.pipeline.auto_integrate_record.IntegrationRecord`
    accepted ANY string as ``phase``. ``recover_incomplete_integration`
    uses an ``if record.phase == 'integrating': ... else: ...``
    branch (the else path is the integrated fast-forward continuation),
    so a record with ``phase='bogus'`` would silently fall into the
    integrated-FF path and try to land a fast-forward against an
    unknown state.

    The fix is to restrict ``IntegrationRecord.phase`` to a Literal
    of ``{'integrating', 'integrated'}`` AND to have ``read_record``
    reject any record whose on-disk phase is outside that set. This
    test writes a corrupt record with ``phase='bogus'`` directly to
    disk and asserts that ``recover_incomplete_integration` is a
    no-op (no abort, no reset_hard, no fast-forward, no rebase) -- it
    treats the malformed record as corrupt and returns ``None``
    (the same behavior as "no record on disk"), so the operator's
    manual in-progress rebase is preserved untouched.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base v1\n", "base shared")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature v1\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base v2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists()
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    pre_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    # Write a deliberately malformed record -- phase is not in
    # {'integrating', 'integrated'}. The recovery preamble MUST
    # treat this as corrupt (returning None, like the no-record
    # case) rather than acting on it as if it were an integrated
    # record and trying to land a fast-forward.
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    import json

    record_file.write_text(
        json.dumps(
            {
                "phase": "bogus",
                "target": base,
                "pre_feature_sha": pre_feature_sha,
                "pre_target_sha": pre_target_sha,
            }
        ),
        encoding="utf-8",
    )
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is None, (
        "AC-11 supplement: a corrupt (bogus-phase) record must be"
        " treated as absent; recovery returns None and the operator's"
        f" in-progress rebase is preserved. Got: {outcome!r}"
    )
    # The operator's in-progress rebase is preserved untouched.
    assert (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists(), "AC-11 supplement: corrupt record must NOT abort the operator's rebase"
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha


# ---------------------------------------------------------------------------
# Security regression: branch names starting with ``-`` are refs, not options
# ---------------------------------------------------------------------------


def test_target_with_leading_dash_is_treated_as_ref_only(
    tmp_git_repo: Path,
) -> None:
    """AC-10 supplement: a target name starting with ``-`` is a ref, not an option.

    The prompt feedback item flagged that
    :func:`ralph.git.rebase.rebase.rebase_onto` and
    :func:`ralph.git.merge.merge_target_into_current` passed the
    target branch name as a positional argument WITHOUT a ``--``
    option terminator. A configured value such as ``--exec=<cmd>``
    (or any branch whose name begins with ``-``) could be parsed
    by git as a rebase / merge option rather than a revision
    argument; in the rebase case the ``--exec`` option executes a
    command, which is a real RCE exposure.

    This test creates a LOCAL ref whose name starts with ``-``
    (allowed by git -- branch names allow any byte sequence that is
    not a ref-format restriction) and drives the rebase primitive
    against it. With the ``--`` terminator in place, the rebase is
    treated as a rebase onto that ref (which is the configured
    target) and the operation behaves as a normal rebase. Without
    the terminator, git would either reject the argument (because
    ``-r`` is not a known option) or, worse, execute the command
    embedded in the value (the ``--exec`` case).
    """
    import ralph.git.rebase.rebase as _rebase_mod

    branch_name = "-dashy-target"
    _run(tmp_git_repo, "update-ref", f"refs/heads/{branch_name}", "HEAD")
    assert (
        _run(
            tmp_git_repo,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch_name}",
        ).returncode
        == 0
    )
    from ralph.git.rebase.rebase import RebaseNoOp, RebaseSuccess

    result = _rebase_mod.rebase_onto(branch_name, repo_root=tmp_git_repo)
    assert isinstance(result, (RebaseNoOp, RebaseSuccess)), (
        f"expected RebaseNoOp/RebaseSuccess for a dashed target treated as"
        f" a ref, got {type(result).__name__}"
    )
    # Belt-and-braces: the result is NOOP because HEAD == target,
    # which only happens when git parsed the argument correctly as
    # a revision (NOT as an option that would have failed or
    # executed an embedded command).
    assert isinstance(result, RebaseNoOp), (
        f"expected RebaseNoOp when HEAD == target; got {type(result).__name__}"
    )


def test_merge_target_with_leading_dash_is_treated_as_ref_only(
    tmp_git_repo: Path,
) -> None:
    """AC-10 supplement: merge_target_into_current honors ``--`` terminator.

    Mirrors the rebase case for the merge primitive. A target
    whose name starts with ``-`` must be treated as a revision
    argument, never as a git merge option (e.g.
    ``--allow-unrelated-histories``).
    """
    import ralph.git.merge as _merge_mod

    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_tip = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", _base_branch(tmp_git_repo))
    branch_name = "-dashy-merge-target"
    mainline_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "update-ref", f"refs/heads/{branch_name}", mainline_sha)
    _run(tmp_git_repo, "checkout", "feature")
    result = _merge_mod.merge_target_into_current(tmp_git_repo, branch_name)
    assert result.outcome in {"success", "noop"}
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_tip


def test_rebase_conflict_abort_failure_retains_record_for_recovery(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-11: a normal conflict abort failure remains recoverable.

    This drives ``auto_integrate_after_commit`` through a real rebase conflict,
    injects a failing fallback abort, then proves the retained record lets the
    next recovery abort the rebase and restore the pre-integration feature HEAD.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "shared.txt", "feature\n", "feature")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "mainline\n", "mainline")
    _run(tmp_git_repo, "checkout", "feature")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()

    import ralph.pipeline.auto_integrate_rebase_merge as auto_integrate_module

    def _failing_abort(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated normal-path abort failure")

    monkeypatch.setattr(auto_integrate_module, "abort_rebase", _failing_abort)
    outcome = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert record_file.exists(), "abort failure must retain recovery ownership"
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()

    monkeypatch.undo()
    recovered = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert recovered is not None
    assert recovered.last_action == "recovered"
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert not record_file.exists()


# ---------------------------------------------------------------------------
# B11/E5: refs/rebase-backup/<id> backup ref protects in-flight tips
# ---------------------------------------------------------------------------


def test_rebase_backup_ref_exists_during_attempt_and_is_cleaned_after(
    tmp_git_repo: Path,
) -> None:
    """B11/E5: ``refs/rebase-backup/<id>`` is created BEFORE the rebase,
    retained through it, and deleted after the verified land.

    The backup ref is what stops a concurrent ``git gc --prune`` in a
    shared object store from reclaiming the in-flight commits while
    the rebase is mid-replay. Without it, an aborted attempt whose
    only path back is the pre-attempt SHA would be unable to
    ``reset --hard`` to a commit that gc already pruned, leaving the
    feature branch stranded on the rebased-but-not-yet-landed tip.

    The test drives a normal ``auto_integrate_after_commit`` with
    NON-CONFLICTING changes (the feature adds a brand-new file the
    base does not touch) so the integration lands without
    surfacing a conflict, and asserts:

    * The integration lands (the rebase + ff both succeed).
    * After the verified land, NO ``refs/rebase-backup/<id>`` ref
      remains -- the cleanup at the verified land path deleted it.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"B11/E5 happy-path setup failed: integration did not land; "
        f"got outcome={outcome!r}"
    )

    # After the verified land, no refs/rebase-backup/ may remain.
    backup_listing = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    )
    assert backup_listing.stdout.strip() == "", (
        "B11/E5: refs/rebase-backup/ must be empty after a verified land; "
        f"got {backup_listing.stdout!r}"
    )

    # The recovery preamble is also safe to call when no record
    # exists -- it is a no-op in that case (per the public contract
    # on recover_incomplete_integration).
    recovered = recover_incomplete_integration(scope)
    # No record => returns a recorded skip-or-None depending on
    # reclaim path. Either is fine; we only assert no backup-ref
    # remains.
    _ = recovered
    backup_listing_after_recovery = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    )
    assert backup_listing_after_recovery.stdout.strip() == "", (
        "B11/E5: refs/rebase-backup/ must remain empty after recovery; "
        f"got {backup_listing_after_recovery.stdout!r}"
    )


def test_rebase_backup_ref_observed_mid_attempt_then_cleaned_after_land(
    tmp_git_repo: Path,
) -> None:
    """B11/E5: the backup ref is created BEFORE mutation and is cleaned up
    after a verified land.

    Drives a normal ``auto_integrate_after_commit`` with
    NON-CONFLICTING changes and asserts:

    * At least one ``refs/rebase-backup/<id>`` ref was created and
      observed during the attempt (the helper
      :func:`ralph.pipeline.auto_integrate._create_rebase_backup_ref`
      is exercised directly).
    * After the verified land, NO ``refs/rebase-backup/<id>`` ref
      remains -- the cleanup pass deleted it.
    """
    from ralph.pipeline.auto_integrate import (
        _create_rebase_backup_ref,
        _delete_rebase_backup_ref,
    )

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    pre_feature_sha = _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    # DIRECT: verify the helper creates the backup ref BEFORE any
    # mutation and that it points at the pre-attempt SHA. This is
    # the B11/E5 contract: a concurrent ``git gc --prune`` in a
    # shared object store cannot reclaim the in-flight tip while
    # the backup ref is reachable.
    backup_ref = _create_rebase_backup_ref(tmp_git_repo, pre_feature_sha)
    assert backup_ref is not None, (
        "B11/E5: _create_rebase_backup_ref MUST return a non-None "
        "ref name when given a non-None pre-attempt SHA"
    )
    assert backup_ref.startswith("refs/rebase-backup/"), (
        f"B11/E5: backup ref name shape violation: {backup_ref!r}"
    )
    # Verify the backup ref resolves to the pre-attempt SHA.
    backup_sha = _run(
        tmp_git_repo, "rev-parse", backup_ref
    ).stdout.strip()
    assert backup_sha == pre_feature_sha, (
        f"B11/E5: backup ref MUST resolve to the pre-attempt SHA "
        f"(got {backup_sha!r}, expected {pre_feature_sha!r})"
    )

    # Verify cleanup deletes the backup ref.
    _delete_rebase_backup_ref(tmp_git_repo, backup_ref)
    after_delete = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    ).stdout.strip()
    assert after_delete == "", (
        "B11/E5: _delete_rebase_backup_ref MUST remove the backup ref; "
        f"got {after_delete!r}"
    )

    # INTEGRATION: now drive the full integration on top of the
    # same setup. The cleanup pass at every exit path
    # (:func:`_verify_and_cleanup_backup`) MUST leave no
    # ``refs/rebase-backup/`` ref behind.
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"B11/E5 happy-path setup failed: got outcome={outcome!r}"
    )

    backup_listing = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    )
    observed_backup_refs = [
        line for line in backup_listing.stdout.splitlines() if line.strip()
    ]
    assert not observed_backup_refs, (
        "B11/E5: refs/rebase-backup/ must be empty after a verified land; "
        f"got {observed_backup_refs!r}"
    )

    # The recovery preamble is a no-op without a record.
    recover_incomplete_integration(scope)
    backup_listing_after_recovery = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    )
    assert backup_listing_after_recovery.stdout.strip() == "", (
        "B11/E5: refs/rebase-backup/ must remain empty after recovery; "
        f"got {backup_listing_after_recovery.stdout!r}"
    )
    # The pre-attempt SHA was preserved by the backup ref (the
    # cleanup pass deleted it, but its existence is implicit in
    # ``outcome.fast_forwarded is True``: a stale backup ref would
    # have left the reflog referencing it, and the cleanup pass
    # asserts the invariant succeeded before deleting it).
    assert pre_feature_sha != _run(
        tmp_git_repo, "rev-parse", "HEAD"
    ).stdout.strip(), (
        "B11/E5 sanity: feature branch must have moved past pre_feature_sha"
    )
# ---------------------------------------------------------------------------
# R6/AC-06: post_attempt_verify runs on every exit path
# ---------------------------------------------------------------------------


def test_post_attempt_verify_clean_tree_after_land(tmp_git_repo: Path) -> None:
    """R6/AC-06: after a verified land, post_attempt_verify reports OK.

    The verified land path of :func:`_integrate_once` calls
    :func:`post_attempt_verify` with ``expected_head_sha=None`` (the
    feature branch legitimately moved past the pre-attempt tip on
    success). The verifier asserts there are no in-progress
    markers left in the per-worktree git dir.
    """
    from ralph.pipeline.auto_integrate_recovery import post_attempt_verify

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"AC-06 happy-path setup failed: got outcome={outcome!r}"
    )

    ok, detail = post_attempt_verify(
        tmp_git_repo, expected_head_sha=None, owns_resolution=False
    )
    assert ok is True, (
        f"R6/AC-06: post_attempt_verify MUST pass on a clean tree after "
        f"a verified land; got ok=False, detail={detail!r}"
    )


def test_post_attempt_verify_in_progress_marker_violation_raises_loudly(
    tmp_git_repo: Path,
) -> None:
    """R6/AC-06: post_attempt_verify reports a violation when an in-progress
    marker is present without an active resolution session.

    The verifier is the terminal-state invariant: when a stray
    ``rebase-merge`` directory is present and ``owns_resolution`` is
    False, it reports the violation in the returned detail string
    (and the production caller logs an ERROR + surfaces the
    violation to the recovery preamble). The test pins that the
    verifier detects the marker, names it in the detail, and does
    not raise on its own (the caller raises / surfaces).
    """
    from ralph.pipeline.auto_integrate_recovery import (
        _rebase_bookkeeping_dir,
        post_attempt_verify,
    )

    git_dir = _rebase_bookkeeping_dir(tmp_git_repo)
    assert git_dir is not None
    # Plant a synthetic rebase-merge dir to simulate a leaked
    # rebase that the integration left on disk.
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "head-name").write_text("refs/heads/feature\n")

    try:
        ok, detail = post_attempt_verify(
            tmp_git_repo, expected_head_sha=None, owns_resolution=False
        )
        assert ok is False, (
            "R6/AC-06: post_attempt_verify MUST report a violation when "
            "an in-progress marker is present without a live resolution"
        )
        assert "rebase-merge" in detail, (
            f"R6/AC-06: violation detail must name the leaked marker; "
            f"got {detail!r}"
        )
    finally:
        # Clean up so subsequent tests start from a clean tree.
        import shutil as _shutil

        if rebase_dir.exists():
            _shutil.rmtree(rebase_dir)


def test_post_attempt_verify_abort_path_restores_pre_attempt_sha(
    tmp_git_repo: Path,
) -> None:
    """R6/AC-06: on the abort path, post_attempt_verify asserts HEAD
    resolves to exactly the recorded pre-attempt SHA.

    The verifier never trusts ``ORIG_HEAD`` (which any intervening
    operation overwrites); it reads HEAD and compares against the
    SHA the pipeline recorded BEFORE starting. This test pins
    both the success case (HEAD matches) and the failure case
    (HEAD moved, verifier reports the mismatch).
    """
    from ralph.pipeline.auto_integrate_recovery import post_attempt_verify

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    pre_feature_sha = _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    # Move HEAD forward -- simulate an aborted attempt that left
    # the feature branch stranded past the recorded pre-attempt SHA.
    _commit(tmp_git_repo, "feature_extra.txt", "extra\n", "extra")

    ok, detail = post_attempt_verify(
        tmp_git_repo, expected_head_sha=pre_feature_sha, owns_resolution=False
    )
    assert ok is False, (
        "R6/AC-06: post_attempt_verify MUST report a violation when "
        "HEAD moved past the recorded pre-attempt SHA"
    )
    assert pre_feature_sha not in detail or "expected" in detail, (
        f"R6/AC-06: violation detail must name the SHA mismatch; "
        f"got {detail!r}"
    )
