"""Auto-integrate race / concurrency tests.

Houses the AC-08 (CAS race in the orchestrator) and AC-09
(dirty-worktree fast-forward skip) tests, which were previously
inlined in :mod:`tests.test_auto_integrate` but pushed that file
past the repo-structure ``_MAX_FILE_LINES`` cap. Splitting them out
keeps the main test file under the cap while preserving the same
subprocess_e2e marker so the tests still run under
``make test-subprocess-e2e``.

The helpers (``_run``, ``_commit``, ``_base_branch``,
``_build_config``) are duplicated here to keep this file standalone;
the duplication is small (~30 lines) and avoids a brittle
``from test_auto_integrate import ...`` dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
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


# ---------------------------------------------------------------------------
# AC-08: orchestrator CAS race
# ---------------------------------------------------------------------------


def test_cas_race_target_advances_concurrently_via_orchestration(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-08 orchestration race: target moves between observation and CAS.

    The earlier AC-08 test
    (:func:`tests.test_auto_integrate.test_cas_race_target_advances_concurrently`)
    only exercised the leaf primitive ``compare_and_swap_branch``
    directly. It did NOT exercise the ORCHESTRATION race inside
    :func:`ralph.pipeline.auto_integrate_ff.fast_forward_target`,
    where the order is:

        1. observe the target SHA via ``branch_sha(target)``
        2. ``is_ancestor(observed_target_sha, feature_sha)`` -- ancestor guard
        3. ``compare_and_swap_branch(target, observed_target_sha, feature_sha)``

    The fix binds the ancestor decision to the same observed SHA
    the CAS uses: the ancestor check is performed against the SHA
    the orchestrator actually observed, so a concurrent landing
    between observation and CAS is caught by the CAS itself
    (the ref no longer equals the observed SHA).

    The test forces the race by monkeypatching ``branch_sha`` so
    the call inside
    :func:`ralph.pipeline.auto_integrate_ff.fast_forward_target`
    returns the pre-landing SHA. Because
    :mod:`ralph.pipeline.auto_integrate_ff` binds ``branch_sha``
    at import time, patching the attribute on that module is the
    single binding that affects the call site that matters; every
    other caller keeps the real function and sees the true current
    SHA. With the fix the orchestrator's ancestor check is bound
    to the observed SHA and the CAS is attempted with the
    post-landing SHA as the expected-oldvalue, which fails closed.
    """
    import ralph.pipeline.auto_integrate_ff as _ai_ff_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    pre_landing_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    # Concurrent landing: a new commit on base, advancing base past
    # the pre-landing SHA. The integration's rebase will replay feat
    # on top of the new base.
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    post_landing_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert pre_landing_base_sha != post_landing_base_sha
    _run(tmp_git_repo, "checkout", "feature")

    def _stale_branch_sha(_repo_root: object, _name: str) -> str | None:
        # The single branch_sha call inside
        # auto_integrate_ff.fast_forward_target is the observation
        # that decides the CAS expected-oldvalue. Returning the
        # pre-landing SHA opens the race window deterministically,
        # with no dependency on how many times the orchestrator
        # called branch_sha beforehand. Every other caller keeps
        # the real function and sees the true current SHA.
        return pre_landing_base_sha

    monkeypatch.setattr(_ai_ff_mod, "branch_sha", _stale_branch_sha)

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.fast_forwarded is False, (
        f"AC-08 race: fast_forwarded must be False when the target"
        f" moves between observation and CAS, got {outcome.fast_forwarded!r}"
    )
    assert outcome.last_reason is not None and (
        "advanced concurrently" in outcome.last_reason
        or "CAS mismatch" in outcome.last_reason
    ), (
        f"AC-08 race: last_reason must mention the concurrent landing,"
        f" got {outcome.last_reason!r}"
    )
    # Target ref is UNCHANGED -- still at the post-landing SHA.
    final_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert final_base_sha == post_landing_base_sha, (
        f"AC-08 race: target ref must be byte-unchanged, expected"
        f" {post_landing_base_sha!r}, got {final_base_sha!r}"
    )


# ---------------------------------------------------------------------------
# AC-09: target checked out clean in another worktree -> ff succeeds
# ---------------------------------------------------------------------------


def test_clean_target_worktree_fast_forward_succeeds(tmp_git_repo: Path) -> None:
    """AC-09 (clean twin): target checked out CLEAN in a linked worktree -> ff succeeds.

    The dirty twin
    (:func:`test_dirty_target_worktree_skips_fast_forward` in this
    module) covers the refusal side. This test covers the
    PRODUCTION success path: ``auto_integrate_after_commit``
    routes through ``_fast_forward_via_target_worktree`` and lands
    the target ref via the worktree's own ``git merge --ff-only``.

    TRAP: the SHA captured for ``feature`` in setup action (e) is the
    PRE-REBASE SHA. The integration rebases ``feature`` onto the
    target tip, rewriting the feature tip, so that SHA
    will NOT equal the post-integration tip. Every assertion
    below compares against a freshly read ``git rev-parse HEAD``,
    never against that captured value. (The dirty twin sidesteps
    this trap only because it never reaches a successful ff.)
    """
    base = _base_branch(tmp_git_repo)
    wt_branch = "wt-target"
    _commit(tmp_git_repo, "seed_tracked.txt", "seed content\n", "seed tracked")
    _run(tmp_git_repo, "branch", wt_branch, base)
    _commit(tmp_git_repo, "base_marker.txt", "base marker\n", "base marker")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    # (e) The captured commit SHA above was the PRE-REBASE SHA.
    # See TRAP above -- do not key an assertion off it.
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    # Link a SECOND worktree on wt_branch. The worktree stays
    # CLEAN -- do not modify any tracked file.
    _run(tmp_git_repo, "worktree", "add", str(wt_path), wt_branch)
    try:
        config = _build_config(tmp_git_repo, target=wt_branch)
        scope = WorkspaceScope(tmp_git_repo)
        outcome = auto_integrate_after_commit(config, scope, RebaseState())
        assert outcome is not None
        # Integration reached the ff phase (rebase replay
        # produced a feature tip that is a fast-forward of wt_branch).
        assert outcome.last_action in {"rebased", "merged"}
        assert outcome.last_target == wt_branch
        # Headline success: the fast-forward landed.
        assert outcome.fast_forwarded is True, (
            f"AC-09 success: fast_forwarded must be True when the"
            f" target worktree is clean, got {outcome.fast_forwarded!r}"
        )
        # Plan step 6 contract: on a successful clean-worktree
        # fast-forward, last_reason MUST be None -- the boolean
        # ``fast_forwarded`` is the headline signal, and the
        # producer scrubs any residual rebase/ff reason (including
        # the benign NoOp "Branch is already up-to-date with
        # upstream" that the rebase engine may surface) once the
        # ff has actually landed. Allowing non-None reasons here
        # would silently re-hide the regression where ff succeeds
        # but a stale reason leaks through.
        assert outcome.last_reason is None, (
            f"AC-09 success: last_reason must be None on a clean"
            f" worktree fast-forward, got {outcome.last_reason!r}"
        )
        # Post-integration feature tip (refreshed; see TRAP above).
        feature_tip_after = _run(
            tmp_git_repo, "rev-parse", "HEAD"
        ).stdout.strip()
        # Target ref moved to the post-integration feature tip.
        wt_ref_after = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_ref_after == feature_tip_after, (
            f"AC-09 success: target ref {wt_ref_after!r} must equal"
            f" the post-integration HEAD {feature_tip_after!r}"
        )
        # The linked worktree's own HEAD advanced with the ff --
        # this proves the move went through ``git merge --ff-only``
        # INSIDE the worktree, not via a ref move behind a live
        # checkout's back.
        wt_head_after = _run(wt_path, "rev-parse", "HEAD").stdout.strip()
        assert wt_head_after == feature_tip_after, (
            f"AC-09 success: linked worktree HEAD {wt_head_after!r}"
            f" must equal the post-integration feature tip"
            f" {feature_tip_after!r} -- proves the move went through"
            f" ``git merge --ff-only`` inside the worktree"
        )
        # Worktree is still clean.
        wt_status = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert wt_status == "", (
            f"AC-09 success: linked worktree must remain clean after"
            f" the ff, got status={wt_status!r}"
        )
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


# ---------------------------------------------------------------------------
# fast_forward_target defensive skip reasons (auto_integrate_ff.py)
# ---------------------------------------------------------------------------


def test_fast_forward_target_missing_branch_is_reported(tmp_git_repo: Path) -> None:
    """AC-08 ff layer: ``branch_sha`` returning None -> ``ok=False`` with reason.

    Direct mock-free test of
    :func:`ralph.pipeline.auto_integrate_ff.fast_forward_target` --
    the function reads ``branch_sha(target)`` (auto_integrate_ff.py:60)
    and that call returns None when the branch does not exist. The
    function MUST report ``ok=False`` and
    ``reason='target branch missing at fast-forward time'`` -- never
    attempt a CAS or worktree ff on a missing branch.

    The third reason, 'target advanced concurrently (ff-only refused)'
    (auto_integrate_ff.py:110), is deliberately NOT covered here: it
    is unreachable without injection because the caller's ancestor
    guard at :68 rejects non-ancestors first; it is intentional
    defence-in-depth behind ``git merge --ff-only``.
    """
    from ralph.pipeline.auto_integrate_ff import fast_forward_target

    _base_branch(tmp_git_repo)  # ensure the default branch exists
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    ok, reason = fast_forward_target(
        tmp_git_repo, "definitely-not-a-branch", feature_sha
    )
    assert ok is False, (
        f"AC-08 ff: missing target branch must report ok=False,"
        f" got ok={ok!r}"
    )
    assert reason == "target branch missing at fast-forward time", (
        f"AC-08 ff: defensive reason for missing branch must match"
        f" the producer literal, got {reason!r}"
    )


def test_fast_forward_target_non_ancestor_is_reported(tmp_git_repo: Path) -> None:
    """AC-08 ff layer: target not an ancestor of feature -> ``ok=False``.

    Directly exercises
    :func:`ralph.pipeline.auto_integrate_ff.fast_forward_target`
    against a deliberately diverged target. The ancestor guard at
    auto_integrate_ff.py:68 must reject the move and report
    ``reason='target advanced concurrently (not an ancestor of feature)'``;
    the target ref is then proven byte-unchanged -- AC-08's
    no-force-move contract at the ff layer.
    """
    from ralph.pipeline.auto_integrate_ff import fast_forward_target

    base = _base_branch(tmp_git_repo)
    # Genuine divergence: feature and base BOTH advance past a
    # shared ancestor, so base is NOT an ancestor of feature.
    base_seed_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_only.txt", "base only\n", "base only")
    base_ref_before = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert base_ref_before != base_seed_sha, (
        "setup: base must have advanced past the seed for the"
        " divergence to be real"
    )
    feature_sha = _run(
        tmp_git_repo, "rev-parse", "refs/heads/feature"
    ).stdout.strip()

    ok, reason = fast_forward_target(tmp_git_repo, base, feature_sha)
    assert ok is False, (
        f"AC-08 ff: non-ancestor target must report ok=False,"
        f" got ok={ok!r}"
    )
    assert reason == (
        "target advanced concurrently (not an ancestor of feature)"
    ), (
        f"AC-08 ff: defensive reason for non-ancestor target must"
        f" match the producer literal, got {reason!r}"
    )
    # AC-08 no-force-move contract at the ff layer: the target ref
    # is BYTE-UNCHANGED.
    base_ref_after = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    assert base_ref_after == base_ref_before, (
        f"AC-08 ff: target ref must be byte-unchanged on a refused"
        f" non-ancestor ff, before={base_ref_before!r}"
        f" after={base_ref_after!r}"
    )


# ---------------------------------------------------------------------------
# AC-09: target checked out dirty in another worktree
# ---------------------------------------------------------------------------


def test_dirty_target_worktree_skips_fast_forward(tmp_git_repo: Path) -> None:
    """AC-09: target checked out dirty in a LINKED worktree -> ff skipped.

    The test exercises the real ``_fast_forward_via_target_worktree``
    branch in :mod:`ralph.pipeline.auto_integrate`, not the CAS path
    or the ``no commits beyond target`` skip. Steps:

    1. Seed a tracked file on the base branch.
    2. Fork ``wt_branch`` off the seed base.
    3. Add a commit on the primary ``feature`` branch so ``feature``
       is genuinely ahead of ``wt_branch``.
    4. Link a SECOND worktree on ``wt_branch`` and modify the seeded
       tracked file (untracked changes are ignored by
       ``is_repo_clean`` which uses ``--untracked-files=no``).
    5. Configure the integration target as ``wt_branch`` and run.
    6. Assert: ``fast_forwarded is False``, ``last_reason ==
       'target worktree dirty'``, target ref UNCHANGED, dirty
       worktree's files UNTOUCHED.
    """
    base = _base_branch(tmp_git_repo)
    wt_branch = "wt-target"
    # Seed a tracked file so the linked worktree has something to
    # modify (untracked changes are ignored by ``is_repo_clean``).
    _commit(tmp_git_repo, "seed_tracked.txt", "seed content\n", "seed tracked")
    _run(tmp_git_repo, "branch", wt_branch, base)
    # Add a commit on the primary base branch so wt_branch is behind.
    _commit(tmp_git_repo, "base_marker.txt", "base marker\n", "base marker")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    wt_branch_sha_before = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
    ).stdout.strip()
    assert feature_sha != wt_branch_sha_before
    # Add a SECOND worktree on wt_branch.
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _run(tmp_git_repo, "worktree", "add", str(wt_path), wt_branch)
    try:
        # Modify the seeded tracked file in the worktree.
        tracked_in_wt = wt_path / "seed_tracked.txt"
        tracked_in_wt.write_text("uncommitted tracked change\n", encoding="utf-8")
        wt_status = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "seed_tracked.txt" in wt_status, (
            f"preflight: linked worktree must report the tracked dirty"
            f" file, got {wt_status!r}"
        )
        config = _build_config(tmp_git_repo, target=wt_branch)
        scope = WorkspaceScope(tmp_git_repo)
        outcome = auto_integrate_after_commit(config, scope, RebaseState())
        assert outcome is not None
        # Integration must reach the ff phase (the rebase replay
        # produced a feature tip that is a fast-forward of wt_branch
        # before the dirty guard kicked in).
        assert outcome.last_action in {"rebased", "merged"}, (
            f"AC-09: integration must reach the ff phase (last_action"
            f" reflects the rebase outcome), got last_action="
            f"{outcome.last_action!r}"
        )
        assert outcome.last_target == wt_branch
        assert outcome.fast_forwarded is False, (
            f"AC-09: fast_forwarded must be False when target worktree"
            f" is dirty, got {outcome.fast_forwarded!r}"
        )
        assert outcome.last_reason == "target worktree dirty", (
            f"AC-09: last_reason must be 'target worktree dirty',"
            f" got {outcome.last_reason!r}"
        )
        # Target ref (wt_branch) is UNCHANGED.
        wt_branch_sha_after = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_branch_sha_after == wt_branch_sha_before, (
            f"AC-09: target ref must be byte-unchanged, before="
            f"{wt_branch_sha_before!r} after={wt_branch_sha_after!r}"
        )
        # The dirty worktree's files are UNTOUCHED.
        assert (wt_path / "seed_tracked.txt").exists(), (
            "AC-09: dirty file in the target worktree must not be removed"
        )
        assert (wt_path / "seed_tracked.txt").read_text(
            encoding="utf-8"
        ) == "uncommitted tracked change\n", (
            "AC-09: dirty file in the target worktree must not be modified"
        )
        wt_status_after = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "seed_tracked.txt" in wt_status_after, (
            f"AC-09: linked worktree must remain dirty, got status={wt_status_after!r}"
        )
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))
