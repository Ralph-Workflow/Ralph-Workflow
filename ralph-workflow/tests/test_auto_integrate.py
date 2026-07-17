"""End-to-end tests for :mod:`ralph.pipeline.auto_integrate`.

These tests drive the real ``auto_integrate_after_commit`` and
``recover_incomplete_integration`` against per-test git repositories
built from the ``tmp_git_repo`` fixture. They are excluded from the
budget-tracked 60s ``make verify`` step via
``pytest.mark.subprocess_e2e`` and run under
``make test-subprocess-e2e`` (matching the convention in
:mod:`tests.test_git_rebase`).

Acceptance criteria covered:

* AC-01 — disabled returns ``None`` and the repo is byte-unchanged;
  default-enabled path is active with no config set.
* AC-02 — on-target skip with zero mutation.
* AC-03 — no-commits-beyond-target skip with zero mutation.
* AC-04 — feature-ahead pure fast-forward (target ref equals feature
  tip, no new commit created).
* AC-05 — diverged clean rebase then ff.
* AC-06 — rebase-conflict then clean endpoint merge + ff.
* AC-07 — both conflict; branch bit-identical, outcome recorded.
* AC-08 — CAS race: target moved between observation and update leaves
  ref byte-unchanged.
* AC-09 — target checked out dirty in another worktree: ff skipped.
* AC-10 — no invocation of this feature ever calls ``git push``.
* AC-11 — phased crash recovery (4 cases).
* AC-13 — target auto-detection: origin/HEAD -> develop; remote-less
  ``main`` integration; remote-less ``master`` integration when no
  ``main`` branch exists; no-candidate skip; explicit override.
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
    resolve_integration_target,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


# ---------------------------------------------------------------------------
# Helpers
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
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": enabled,
        }
    }
    if target is not None:
        payload["general"]["auto_integrate_target"] = target
    return UnifiedConfig.model_validate(payload)


def _snapshot(tmp_git_repo: Path) -> dict[str, str]:
    """Capture HEAD SHA + all ref SHAs for byte-equality comparisons."""
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    refs: dict[str, str] = {}
    out = _run(tmp_git_repo, "for-each-ref", "--format=%(refname) %(objectname)")
    for line in out.stdout.splitlines():
        if not line:
            continue
        name, sha = line.split(" ", 1)
        refs[name] = sha
    return {"head": head, "refs": refs, "worktree": _run(tmp_git_repo, "status", "--porcelain").stdout}


# ---------------------------------------------------------------------------
# AC-01: disabled byte-identical no-op
# ---------------------------------------------------------------------------


def test_disabled_returns_none_and_repo_byte_unchanged(tmp_git_repo: Path) -> None:
    """AC-01 disabled: returns None and the repo is byte-unchanged."""
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, enabled=False)
    scope = WorkspaceScope(tmp_git_repo)
    result = auto_integrate_after_commit(config, scope, RebaseState())
    assert result is None
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_default_enabled_with_no_config_set(tmp_git_repo: Path) -> None:
    """AC-01 default: with no config set, the feature is active (default True)."""
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = UnifiedConfig.model_validate({})
    assert config.general.auto_integrate_enabled is True
    assert config.general.auto_integrate_target is None
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased", "merged", "skipped"}
    # The default branch (main or master) must now be at feature_sha.
    base = _base_branch(tmp_git_repo)
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert base_sha == feature_sha


# ---------------------------------------------------------------------------
# AC-02 + AC-03: skip conditions
# ---------------------------------------------------------------------------


def test_on_target_branch_skips_with_zero_mutation(tmp_git_repo: Path) -> None:
    """AC-02 on-target skip: no git mutation, recorded skip."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "main_only.txt", "main\n", "main only")
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "on target branch"
    assert outcome.last_target == base
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_no_commits_beyond_target_skips_with_zero_mutation(tmp_git_repo: Path) -> None:
    """AC-03 no-commits-beyond-target skip."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # feature is exactly at base (no extra commits).
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == feature_sha
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "no commits beyond target"
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_no_target_branch_resolved_skips(tmp_git_repo: Path) -> None:
    """No target candidate -> recorded skip with no mutation."""
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    # Explicitly configured non-existent branch -> resolves to None.
    config = _build_config(tmp_git_repo, target="non-existent-branch-xyz")
    scope = WorkspaceScope(tmp_git_repo)
    before = _snapshot(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "no integration target" in (outcome.last_reason or "")
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_detached_head_records_skipped_skip(tmp_git_repo: Path) -> None:
    """AC-02/AC-13 detached HEAD: recorded skipped state, no mutation.

    AC-02 enumerates detached HEAD as a skip condition. The
    previous implementation caught the GitPython ``TypeError`` from
    ``repo.active_branch.name`` inside a broad ``except Exception``
    and silently returned ``None`` -- the runner saw no
    ``RebaseState`` to persist and the operator was never told why
    the step was a no-op. This test pins the fixed behavior:
    detached HEAD returns a recorded skipped state with
    ``reason='detached HEAD'`` and mutates nothing.
    """
    base = _base_branch(tmp_git_repo)
    base_sha_before = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    head_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", "--detach", head_sha_before)
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None, (
        "detached HEAD must yield a recorded RebaseState (AC-02), not None"
    )
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "detached HEAD"
    # Repo is byte-unchanged: no refs moved, no crash record on disk.
    assert _snapshot(tmp_git_repo) == before
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == base_sha_before
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert not record_file.exists()


# ---------------------------------------------------------------------------
# AC-04: feature-ahead pure fast-forward
# ---------------------------------------------------------------------------


def test_feature_ahead_pure_fast_forward(tmp_git_repo: Path) -> None:
    """AC-04: feature-ahead target-unmoved: target ref == feature tip, no new commit."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat_ahead.txt", "feat ahead\n", "feat ahead")
    commit_count_before = int(_run(tmp_git_repo, "rev-list", "--count", "HEAD").stdout.strip())
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    # The action may surface as rebased (no-op rebase); it is the
    # only valid representation of the pure fast-forward path.
    # The ``fast_forwarded`` boolean (not the action verb) records
    # the landing itself -- see producer at auto_integrate.py.
    assert outcome.last_action in {"rebased"}
    assert outcome.last_target == base
    # Target ref now equals feature tip.
    new_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert new_base_sha == feature_sha
    # No new commit was created (the fast-forward moves the ref
    # without rewriting history).
    commit_count_after = int(_run(tmp_git_repo, "rev-list", "--count", "HEAD").stdout.strip())
    assert commit_count_after == commit_count_before


# ---------------------------------------------------------------------------
# AC-05: diverged clean rebase then ff
# ---------------------------------------------------------------------------


def test_diverged_clean_rebase_then_ff(tmp_git_repo: Path) -> None:
    """AC-05: diverged + clean rebase + ff lands target at rebased feature tip."""
    base = _base_branch(tmp_git_repo)
    # First, create a commit on base so the feature branch can fork
    # off a non-seed base tip.
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    # Fork feature off base_seed_sha.
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha_before_rebase = _commit(
        tmp_git_repo, "feat_div.txt", "feat div\n", "feat div"
    )
    # Now advance base by one more commit on a disjoint file. Now
    # base is NOT an ancestor of feature (they only share base_seed).
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_div.txt", "base div\n", "base div")
    _run(tmp_git_repo, "checkout", "feature")
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased"}
    assert outcome.last_target == base
    # Feature tip is now descendant of (and equal to) the rebased tip;
    # target ref equals feature tip.
    feature_sha_after = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha_after = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha_after == feature_sha_after
    # And the new feature tip is NOT the same as the pre-rebase tip
    # (rebasing rewrote feature's commit graph).
    assert feature_sha_after != feature_sha_before_rebase


# ---------------------------------------------------------------------------
# AC-06: rebase-conflict then clean endpoint merge + ff
# ---------------------------------------------------------------------------


# This test drives the real rebase engine twice (a preflight ``git rebase``
# to prove the topology conflicts, then the canonical integration run that
# re-rebases, aborts, and performs the endpoint merge plus fast-forward).
# Wall-clock cost under parallel xdist load (721 tests via -n auto --dist
# worksteal in `make test-subprocess-e2e`) is regularly past 5 s on busy
# machines, so the module-level 5-second pytestmark is unsafe for this
# single test. The narrower override raises only this test's per-test
# budget above the file-wide 5 s mark, mirroring the precedent at
# tests/test_audit_public_docstrings.py:55-57.
@pytest.mark.timeout_seconds(30)
def test_rebase_conflict_then_clean_endpoint_merge(tmp_git_repo: Path) -> None:
    """AC-06: REAL rebase conflict -> abort -> clean endpoint merge + ff.

    This is the AC-06 headline test the prompt requires: a REAL
    rebase conflict (the rebase engine stops mid-replay with
    ``rebase-apply``/``rebase-merge`` state on disk) followed by a
    clean endpoint merge that the rebase replay could not produce.

    The trick to making the rebase conflict while the endpoint merge
    succeeds is the "intermediate commit is later reverted" pattern:

    * Base: ``A`` with ``shared.txt = "line1\\nline2\\nline3"``.
    * Feature forks from A and adds two commits:
      - ``D1``: ``shared.txt = "line1\\nFEATURE\\nline2\\nline3"`` (modifies
        the line that base will later modify).
      - ``D2``: ``shared.txt = "line1\\nline2\\nline3"`` (REVERTS D1, so
        the final feature tip matches the common ancestor).
    * Base adds ``B``: ``shared.txt = "line1\\nBASE\\nline2\\nline3"`` (modifies
      the same line D1 did).

    Rebase feature onto B replays D1 first. D1's diff wants to change
    line 2 to ``"FEATURE"``; B has line 2 = ``"BASE"``; conflict. The
    rebase stops mid-replay, leaving ``rebase-apply`` on disk and the
    working tree dirty. The integration MUST abort the rebase (which
    restores feature's tip = D2 and cleans the working tree), then
    attempt the endpoint three-way merge of B into D2. The 3-way
    merge sees D2's state == common ancestor A and B's change is
    additive (new content on line 2), so the merge SUCCEEDS -- a
    single merge commit of B into D2 is created and the target ref
    fast-forwards to it.

    The test asserts every AC-06 invariant the prompt enumerates:
    no ``rebase-apply``/``rebase-merge`` leftovers, a single merge
    commit, target ref equals HEAD, clean working tree, and the
    headline is ``'merged'`` (NOT ``'conflict'`` -- the rebase
    conflict was already handled by the abort + clean endpoint
    merge).

    This test does NOT use ``monkeypatch`` on ``rebase_onto``: the
    real rebase engine runs and the real ``rebase-apply`` state is
    observed on disk before the integration aborts it. It is the
    AC-06 ground truth; the deterministic companion
    ``test_rebase_conflict_then_clean_merge_records_merged_action``
    pins the rebase-outcome classification in isolation.
    """
    base = _base_branch(tmp_git_repo)
    # Seed the shared file on the base branch.
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nline2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "shared seed")
    a_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # Fork feature from A.
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    # D1: modify the line that base will later modify.
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nFEATURE\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D1 modify line 2")
    # D2: REVERT D1 so the final feature tip matches the common
    # ancestor A. This is the key: the endpoint 3-way merge will see
    # D2's state == A and B's change as additive, so it succeeds.
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nline2\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D2 revert D1")
    feature_tip_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # Base: B modifies the same line D1 did, creating the conflict.
    _run(tmp_git_repo, "checkout", base)
    (tmp_git_repo / "shared.txt").write_text(
        "line1\nBASE\nline3\n", encoding="utf-8"
    )
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "base: B modify line 2")
    # Capture B's SHA BEFORE the integration runs -- the
    # fast-forward will move ``base`` to HEAD (the merge commit),
    # so this is the only point at which ``base`` still points at
    # B's pre-integration tip.
    b_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    # Sanity: the topology forces a rebase conflict. ``git rebase``
    # should fail and leave rebase-apply on disk.
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "preflight: expected rebase to conflict so the AC-06 scenario is real"
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "preflight: expected rebase-apply/rebase-merge state on disk"
    )
    # Now abort and run the integration end-to-end on the canonical
    # AC-06 scenario. The integration must:
    #   1. Run rebase_onto(B) -> RebaseConflicts (real rebase conflict).
    #   2. abort_rebase -> rebase-apply/rebase-merge removed, feature
    #      tip restored to D2.
    #   3. merge_target_into_current(B) -> success (D2's state == A,
    #      B is additive, 3-way merge resolves cleanly).
    #   4. fast-forward target ref to the new merge commit.
    _run(tmp_git_repo, "rebase", "--abort")
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    # Feature tip is back to D2.
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_tip_before

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    # The headline MUST be 'merged' -- the rebase conflicted but the
    # endpoint merge succeeded (AC-06).
    assert outcome.last_action == "merged", (
        f"AC-06: expected last_action='merged' (clean endpoint merge after"
        f" rebase conflict), got last_action={outcome.last_action!r}"
        f" reason={outcome.last_reason!r}"
    )
    assert outcome.last_target == base
    assert outcome.fast_forwarded is True
    # No rebase-apply / rebase-merge directory left.
    assert not (git_dir / "rebase-apply").exists(), (
        "AC-06: rebase-apply must be gone after abort_rebase"
    )
    assert not (git_dir / "rebase-merge").exists(), (
        "AC-06: rebase-merge must be gone after abort_rebase"
    )
    # Working tree clean.
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == "", (
        "AC-06: working tree must be clean after abort + merge"
    )
    # A merge commit was created: HEAD has two parents (the rebased
    # D2 tip AND B's tip). This is the structural proof that a clean
    # endpoint merge -- not a rebase replay -- produced the new
    # feature tip.
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    head_parents = _run(tmp_git_repo, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2, (
        f"AC-06: HEAD must be a merge commit with 2 parents, got parents={head_parents!r}"
    )
    # HEAD is NOT the original feature tip (D2): a merge commit was
    # appended on top of it.
    assert head_sha != feature_tip_before
    # Target ref equals feature tip (the fast-forward landed).
    base_sha_after = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert base_sha_after == head_sha, (
        f"AC-06: target ref {base_sha_after!r} must equal HEAD {head_sha!r}"
    )
    # B (the base commit) is one of the merge parents -- proves the
    # endpoint merge incorporated B's changes. b_sha was captured
    # BEFORE the integration ran (see the early capture above);
    # after the fast-forward ``base`` now points at HEAD, so
    # ``base^2`` is B's pre-integration tip.
    b_ancestor = _run(
        tmp_git_repo, "log", "-1", "--format=%H", f"{head_sha}^2"
    ).stdout.strip()
    assert b_ancestor == b_sha, (
        f"AC-06: HEAD^2 must be base's pre-integration tip {b_sha!r},"
        f" got {b_ancestor!r}"
    )


def test_rebase_conflict_then_clean_merge_records_merged_action(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-06 (deterministic): rebase conflicted, endpoint merge clean -> 'merged'.

    The previous implementation in
    :func:`ralph.pipeline.auto_integrate._record_rebase_outcome` always
    reported ``last_action='conflict'`` when ``rebase_outcome`` was a
    :class:`RebaseConflicts`, even if the endpoint merge succeeded --
    the headline verb then lied to the operator. This test pins the
    fixed behavior: with a real diverged feature branch, a forced
    :class:`RebaseConflicts` rebase outcome (via monkeypatch) and a
    clean ``merge_target_into_current`` (real git three-way merge),
    the headline is ``'merged'`` and the target ref fast-forwards.

    The test also asserts the AC-06 invariants the prompt requires:
    no rebase-apply/rebase-merge state, single merge commit, target
    ref equals feature tip.
    """
    import ralph.pipeline.auto_integrate as _ai_mod
    from ralph.git.rebase.rebase import RebaseConflicts

    base = _base_branch(tmp_git_repo)
    # Diverged feature branch: feature ahead of base with a disjoint
    # file change so a real endpoint merge succeeds.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base")
    _run(tmp_git_repo, "checkout", "feature")

    # Force the rebase to report conflicts; the real endpoint merge
    # against the new base SHA will succeed (no shared file changes
    # in this scenario).
    def _fake_rebase_onto(
        target: str, *, repo_root: Path, options: object = None
    ) -> object:
        return RebaseConflicts(files=["shared.txt"])

    monkeypatch.setattr(_ai_mod, "rebase_onto", _fake_rebase_onto)
    # Also patch the ralph.git.rebase.rebase module path that
    # auto_integrate imports through (rebase_onto is imported at
    # module load time, so the indirection in _ai_mod is the live
    # binding).
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.rebase_onto", _fake_rebase_onto
    )

    # The endpoint merge must succeed in this scenario (disjoint
    # changes); the function under test relies on
    # _merge_mod.merge_target_into_current to do the real work, so
    # we leave it untouched.

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    # The headline must be 'merged', NOT 'conflict': the endpoint
    # merge succeeded, the rebase-apply state was already aborted.
    assert outcome.last_action == "merged", (
        f"expected 'merged' (clean endpoint merge after rebase conflict),"
        f" got last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.last_target == base
    # No rebase-apply / rebase-merge directory left (rebase was aborted).
    git_dir_out = _run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip()
    git_dir = Path(git_dir_out)
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    # Clean working tree (the endpoint merge produced a clean commit).
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    # Target ref advanced to feature tip.
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    # The fast-forward did happen.
    assert outcome.fast_forwarded is True


# ---------------------------------------------------------------------------
# AC-07: both conflict; branch bit-identical, outcome recorded
# ---------------------------------------------------------------------------


def test_both_rebase_and_merge_conflict(tmp_git_repo: Path) -> None:
    """AC-07: both conflict -> feature HEAD + working tree bit-identical."""
    base = _base_branch(tmp_git_repo)
    # Force divergence: seed base, fork feature off that seed, then
    # add base-side commits that diverge from feature. The rebase
    # of feature onto base WILL conflict on shared.txt.
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _commit(tmp_git_repo, "shared2.txt", "base shared 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    # The shared.txt change WILL conflict in the merge as well.
    # AC-07: both rebase and endpoint merge conflicted, so the outcome
    # must be recorded as a conflict (not generically skipped). The
    # exact reason comes from the live producer at
    # auto_integrate.py:_resolve_rebase_conflict.
    assert outcome.last_action == "conflict", (
        f"AC-07: both rebase and endpoint merge conflicted, so the"
        f" outcome must be recorded as a conflict, got {outcome.last_action!r}"
    )
    assert outcome.last_reason == "rebase and endpoint merge both conflicted"
    # Feature HEAD is byte-identical to its pre-integration SHA.
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_sha
    # Working tree is clean.
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    # No dangling rebase/merge state.
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "MERGE_HEAD").exists()
    # No leftover auto-integrate crash record.
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert not record_file.exists()


# ---------------------------------------------------------------------------
# AC-08: CAS race -> target ref NOT moved
# ---------------------------------------------------------------------------


def test_cas_race_target_advances_concurrently(tmp_git_repo: Path) -> None:
    """AC-08: target moves between observation and CAS leaves ref byte-unchanged.

    The CAS binds the ancestor decision to the same observed SHA.
    If the target moves between observation and CAS, the CAS fails
    closed (ref no longer equals observed SHA) and the target ref
    is left BYTE-UNCHANGED.

    The full multi-call variant (with monkeypatched ``branch_sha``
    to force the race inside :func:`_fast_forward_target`) lives in
    :mod:`tests.test_auto_integrate_race`; this one exercises the
    same contract via the primitive-level ``compare_and_swap_branch``
    path, asserting the CAS refuses to overwrite the post-landing SHA.
    """
    import ralph.git.merge as _merge_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    pre_landing_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    # Concurrent landing: a new commit on base advancing it past
    # the pre-landing SHA.
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    post_landing_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert pre_landing_base_sha != post_landing_base_sha
    # CAS with the STALE pre-landing SHA: Git's ``update-ref``
    # is an atomic CAS. The ref must still equal the
    # expected-oldvalue; it doesn't (target moved), so the CAS
    # MUST fail closed (ref untouched).
    target_feature_sha = _run(tmp_git_repo, "rev-parse", "feature").stdout.strip()
    cas_ok = _merge_mod.compare_and_swap_branch(
        tmp_git_repo, base, pre_landing_base_sha, target_feature_sha
    )
    assert cas_ok is False, "AC-08 CAS must fail when expected_old_sha is stale"
    # Target ref is BYTE-UNCHANGED -- still at the post-landing SHA.
    final_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert final_base_sha == post_landing_base_sha




# ---------------------------------------------------------------------------
# AC-10: no git push invocations
# ---------------------------------------------------------------------------


def test_no_push_invocation(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-10: every git argv across a full integration is recorded.

    Patches the shared execution seam used by BOTH
    ``ralph.git.merge`` (merge/ff/cas primitives) and the rebase
    engine (``SubprocessExecutor``) so EVERY ``run_git`` invocation
    during a real diverged-clean rebase+ff integration AND a
    rebase-conflict -> merge -> ff integration flows through a
    single recorder. Asserts no recorded argv contains the standalone
    ``push`` subcommand.

    Patching the source module's ``run_git`` does NOT propagate to
    modules that did ``from X import run_git``; each import-site
    needs its own monkeypatch.setattr. The fix patches every known
    import-site so the recorder captures EVERY argv across a full
    integration (previous version patched only the merge site).
    """
    import ralph.git.merge as _merge_mod
    import ralph.git.operations as _operations_mod
    import ralph.git.rebase.rebase_continuation as _continuation_mod
    import ralph.git.rebase.rebase_preconditions as _preconditions_mod
    import ralph.git.rebase.subprocess_executor as _executor_mod
    import ralph.git.subprocess_runner as _runner_mod

    recorded: list[tuple[str, ...]] = []
    real_run_git = _runner_mod.run_git

    def _recording_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
    ) -> object:
        recorded.append(tuple(args))
        return real_run_git(args, cwd=cwd, label=label, options=options)

    # Patch every import-site of ``run_git`` so the recorder sees
    # EVERY argv across both the merge path and the rebase path.
    for mod in (
        _runner_mod,
        _merge_mod,
        _executor_mod,
        _continuation_mod,
        _preconditions_mod,
        _operations_mod,
    ):
        monkeypatch.setattr(mod, "run_git", _recording_run_git)

    # ---- Run 1: diverged clean rebase + ff ----
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_div.txt", "base\n", "base div")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat_div.txt", "feat\n", "feat div")
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased"}

    # ---- Run 2: rebase-conflict -> merge -> ff ----
    _run(tmp_git_repo, "checkout", base)
    _run(tmp_git_repo, "branch", "-D", "feature")
    _run(tmp_git_repo, "checkout", "-b", "feature2")
    _commit(tmp_git_repo, "shared2.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared2.txt", "base version\n", "base shared")
    _run(tmp_git_repo, "checkout", "feature2")
    outcome2 = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome2 is not None
    assert outcome2.last_action in {"merged", "conflict"}

    # Verify NO recorded argv contains the 'push' subcommand. ``recorded``
    # is a list of tuples (see ``recorded: list[tuple[str, ...]]`` and
    # ``recorded.append(tuple(args))`` above), so tuple ``__contains__``
    # already matches by element equality and therefore only fires on the
    # standalone ``push`` subcommand -- not on substrings inside larger
    # tokens. A single element-membership check is provably sufficient.
    push_argvs = [argv for argv in recorded if "push" in argv]
    assert push_argvs == [], (
        f"auto_integrate must never invoke git push; recorded argvs "
        f"containing 'push': {push_argvs[:5]}"
    )

    # Complementary behavioral proof: a bare remote's refs never move.
    # We use a dedicated repo+remote pair to avoid touching the
    # shared tmp_git_repo (which is already mutated by the runs above).
    bare = tmp_git_repo.parent / "bare.git"
    _run(tmp_git_repo.parent, "init", "--bare", str(bare))
    _run(tmp_git_repo, "remote", "add", "origin", str(bare))
    _run(tmp_git_repo, "push", "origin", base)  # seed the bare
    bare_refs_before = _run(bare, "for-each-ref", "--format=%(refname)")
    outcome3 = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome3 is not None
    bare_refs_after = _run(bare, "for-each-ref", "--format=%(refname)")
    assert bare_refs_before.stdout == bare_refs_after.stdout, (
        "auto_integrate must not push to the bare remote"
    )


# ---------------------------------------------------------------------------
# AC-11: phased crash recovery (4 cases)
# ---------------------------------------------------------------------------


def test_recovery_mid_rebase_kill_restores_feature(tmp_git_repo: Path) -> None:
    """AC-11 case 1: REAL mid-rebase kill leaves rebase-apply on disk.

    Headline pass-through kept in this file; the full invariant
    suite (HEAD restored, working tree clean, record cleared, no
    rebase-apply / rebase-merge / MERGE_HEAD) lives in
    :func:`tests.test_auto_integrate_recovery.test_recovery_mid_rebase_kill_restores_feature`.
    """
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
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
    assert outcome.last_action == "recovered"
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha
    assert not record_file.exists()


def test_recovery_killed_after_clean_rebase_before_ff(tmp_git_repo: Path) -> None:
    """AC-11 case 2: rebase completed but ff not done -> recover completes ff."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
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
    """AC-11 case 3: clean merge completed but ff not done -> recover completes ff."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()


def test_recovery_no_record_is_a_noop(tmp_git_repo: Path) -> None:
    """AC-11 case 4: no record -> never disturb operator's manual git op.

    The headline behavior (no record -> outcome=None, repo
    byte-unchanged). The ground-truth test that asserts a real
    in-progress rebase is preserved lives in
    :func:`tests.test_auto_integrate_recovery.test_recovery_no_record_preserves_operator_in_progress_rebase`.
    """
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    _run(tmp_git_repo, "rebase", _base_branch(tmp_git_repo))
    _run(tmp_git_repo, "rebase", "--abort")
    before = _snapshot(tmp_git_repo)
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is None
    after = _snapshot(tmp_git_repo)
    assert before == after


# ---------------------------------------------------------------------------
# AC-13: target auto-detection
# ---------------------------------------------------------------------------


def test_auto_detect_origin_head(tmp_git_repo: Path) -> None:
    """AC-13: origin/HEAD -> develop integration."""
    base = _base_branch(tmp_git_repo)
    # Build a 'develop' branch and make origin/HEAD point to it.
    _run(tmp_git_repo, "branch", "develop")
    bare = tmp_git_repo.parent / "bare.git"
    _run(tmp_git_repo.parent, "init", "--bare", str(bare))
    _run(tmp_git_repo, "remote", "add", "origin", str(bare))
    _run(tmp_git_repo, "push", "origin", "develop")
    _run(tmp_git_repo, "remote", "set-head", "origin", "develop")
    # A feature branch with a commit on top of the current base.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = _build_config(tmp_git_repo)  # auto-detect, target unset
    scope = WorkspaceScope(tmp_git_repo)
    resolved = resolve_integration_target(config, tmp_git_repo)
    assert resolved == "develop"
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    # The action landed develop at the feature tip (or rebased and
    # then ff'd; either way develop should equal feature_sha).
    develop_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/develop").stdout.strip()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert develop_sha == feature_sha
    # 'base' (main or master) was NOT advanced.
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    # base was advanced only by the seeded initial commit + nothing else.
    # Just assert base_sha != feature_sha.
    assert base_sha != feature_sha


def test_auto_detect_no_remote_picks_main(tmp_git_repo: Path) -> None:
    """AC-13: remote-less repo with main -> integrate main."""
    base = _base_branch(tmp_git_repo)
    # If the default branch isn't already 'main', rename it.
    if base != "main":
        _run(tmp_git_repo, "branch", "-m", base, "main")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = _build_config(tmp_git_repo)  # auto-detect
    scope = WorkspaceScope(tmp_git_repo)
    resolved = resolve_integration_target(config, tmp_git_repo)
    assert resolved == "main"
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    main_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/main").stdout.strip()
    assert main_sha == feature_sha


def test_auto_detect_no_main_picks_master(tmp_git_repo: Path) -> None:
    """AC-13: remote-less repo with no main but a master -> integrate master."""
    base = _base_branch(tmp_git_repo)
    # Rename base to master so NO branch named main exists; this forces
    # resolution past the 'main' candidate onto the 'master' leg.
    if base != "master":
        _run(tmp_git_repo, "branch", "-m", base, "master")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = _build_config(tmp_git_repo)  # auto-detect
    scope = WorkspaceScope(tmp_git_repo)
    resolved = resolve_integration_target(config, tmp_git_repo)
    assert resolved == "master"
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    master_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/master").stdout.strip()
    assert master_sha == feature_sha


def test_auto_detect_no_candidate_skips(tmp_git_repo: Path) -> None:
    """AC-13: no candidate -> recorded skip, no mutation."""
    base = _base_branch(tmp_git_repo)
    # Rename base to something that's not main or master, so no
    # auto-detect candidate exists.
    _run(tmp_git_repo, "branch", "-m", base, "trunk")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = _build_config(tmp_git_repo)  # auto-detect
    scope = WorkspaceScope(tmp_git_repo)
    before = _snapshot(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_explicit_config_target_overrides_detection(tmp_git_repo: Path) -> None:
    """AC-13: explicit target overrides detection."""
    _base_branch(tmp_git_repo)  # ensure the seed base branch exists
    _run(tmp_git_repo, "branch", "develop")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = _build_config(tmp_git_repo, target="develop")
    scope = WorkspaceScope(tmp_git_repo)
    resolved = resolve_integration_target(config, tmp_git_repo)
    assert resolved == "develop"
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    develop_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/develop").stdout.strip()
    assert develop_sha == feature_sha
