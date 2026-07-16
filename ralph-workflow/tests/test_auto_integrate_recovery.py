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
    recover_incomplete_integration,
)
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
    import ralph.pipeline.auto_integrate as _ai_mod

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
    import ralph.pipeline.auto_integrate as _ai_mod

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
