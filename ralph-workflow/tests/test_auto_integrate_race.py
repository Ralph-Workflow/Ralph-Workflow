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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


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
    (:func:`tests.test_auto_integrate.test_compare_and_swap_branch_rejects_stale_expected_sha`)
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

    def _stale_observe_branch_sha(
        _repo_root: object, _name: str
    ) -> tuple[str | None, bool]:
        # The single observe_branch_sha call inside
        # auto_integrate_ff is the observation that decides the CAS
        # expected-oldvalue. Returning the pre-landing SHA -- with
        # query_ok=True, i.e. "git answered definitively" -- opens the
        # race window deterministically, with no dependency on how many
        # times the orchestrator read the ref beforehand. Every other
        # caller keeps the real function and sees the true current SHA.
        return pre_landing_base_sha, True

    monkeypatch.setattr(_ai_ff_mod, "observe_branch_sha", _stale_observe_branch_sha)

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(
        config, scope, RebaseState(), sleep=lambda _seconds: None, jitter=lambda: 0.0
    )
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
        outcome = auto_integrate_after_commit(
            config, scope, RebaseState(), sleep=lambda _seconds: None, jitter=lambda: 0.0
        )
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

    Its sibling reason for an UNREADABLE target -- the ``git rev-parse``
    that failed rather than reporting the branch absent -- is covered by
    :func:`test_unreadable_target_is_retryable_but_a_missing_one_is_not`.
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


def test_unreadable_target_is_retryable_but_a_missing_one_is_not(
    tmp_path: Path, tmp_git_repo: Path
) -> None:
    """A FAILED target read is a concurrency signal, not an absent branch.

    Defect: ``branch_sha`` returned ``None`` both when the branch did not
    exist AND when ``git rev-parse`` itself failed, and the fast-forward
    mapped that single ``None`` onto the NON-retryable 'target branch
    missing'. Under concurrency the commonest cause of a failed read is a
    ref lock held by the sibling agent currently landing on the same
    branch, so the bounded integration loop abandoned a situation one
    retry resolves.

    ``tmp_path`` is not a git repository at all, so ``git rev-parse``
    exits non-zero for a REASON OTHER than 'no such ref' -- the same
    shape a contended ref lock produces, without having to contend one.
    """
    from ralph.pipeline.auto_integrate_ff import (
        fast_forward_target,
        is_retryable_fast_forward_failure,
    )

    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    unreadable_ok, unreadable_reason = fast_forward_target(
        tmp_path, "main", feature_sha
    )
    assert unreadable_ok is False
    assert is_retryable_fast_forward_failure(unreadable_reason), (
        f"an unreadable target must be retried, got reason={unreadable_reason!r}"
    )

    _missing_ok, missing_reason = fast_forward_target(
        tmp_git_repo, "definitely-not-a-branch", feature_sha
    )
    assert missing_reason == "target branch missing at fast-forward time"
    assert not is_retryable_fast_forward_failure(missing_reason), (
        "a genuinely absent target must NOT burn the attempt budget"
    )


def test_exhausted_integration_attempts_are_recorded_and_not_over_promised(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attempt budget is spent truthfully, in the log and on the state.

    Two defects are pinned here, both in
    :func:`ralph.pipeline.auto_integrate._auto_integrate_after_commit_inner`:

    1. the ``re-integrating onto the moved target`` INFO line was emitted
       at the bottom of EVERY iteration including the last one, where the
       range then ends and nothing re-integrates -- the operator was
       promised a retry that never came;
    2. nothing recorded that the budget had been spent, so the returned
       state carried only the last fast-forward skip reason and a
       one-off concurrent move looked identical to a target that kept
       moving until the loop gave up.

    The scenario is the same deterministic CAS race the AC-08 test uses:
    a permanently stale observed SHA makes every compare-and-swap fail,
    which is retryable, so all three attempts are consumed.
    """
    from loguru import logger

    import ralph.pipeline.auto_integrate as _ai_mod
    import ralph.pipeline.auto_integrate_ff as _ai_ff_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    pre_landing_base_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    _run(tmp_git_repo, "checkout", "feature")

    monkeypatch.setattr(
        _ai_ff_mod,
        "observe_branch_sha",
        lambda _root, _name: (pre_landing_base_sha, True),
    )

    lines: list[str] = []
    sink_id = logger.add(lines.append, level="INFO", format="{message}")
    try:
        outcome = auto_integrate_after_commit(
            _build_config(tmp_git_repo, target=base),
            WorkspaceScope(tmp_git_repo),
            RebaseState(),
            sleep=lambda _seconds: None,
            jitter=lambda: 0.0,
        )
    finally:
        logger.remove(sink_id)

    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert outcome.last_reason is not None
    assert "exhausted 3 integration attempts" in outcome.last_reason, (
        "the recorded reason must say the attempt budget was spent, got"
        f" {outcome.last_reason!r}"
    )
    # The underlying concurrency cause survives as the headline.
    assert "advanced concurrently" in outcome.last_reason

    promised_retries = [
        line for line in lines if "re-integrating onto the moved target" in line
    ]
    assert len(promised_retries) == _ai_mod._MAX_INTEGRATION_ATTEMPTS - 1, (
        "the re-integration line must be emitted only when another attempt"
        f" will actually run, got {promised_retries!r}"
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


def test_dirty_target_worktree_leaves_ref_and_files_unchanged(
    tmp_git_repo: Path,
) -> None:
    """AC-10/E2: a dirty checked-out target worktree is LEFT UNTOUCHED.

    The previous behaviour CAS-advanced ``refs/heads/<target>`` while
    a sibling worktree held the target with uncommitted changes. That
    silently desynced the worktree's index and working tree -- a
    ``reset --hard`` there would destroy the operator's work, and
    ``git status`` described the freshly landed work as a local
    reverse diff. AC-10/E2 forbids that path.

    The new contract: when the target is checked out in a sibling
    worktree AND ``merge --ff-only`` refuses there (because the
    worktree's dirty changes would conflict with the merge, the
    requested SHA is not a descendant, or any other precondition
    fails), the shared ref is LEFT UNTOUCHED and the attempt returns
    a loud, retryable diagnostic. The next clean seam on that
    worktree will retry the same ``merge --ff-only`` and land once
    the operator's uncommitted work is resolved.

    To make ``merge --ff-only`` actually REFUSE, the dirty change in
    the linked worktree must conflict with the merge -- the rebased
    feature must modify a tracked file that the linked worktree has
    ALSO modified with a different content. A bare fast-forward of a
    branch that doesn't touch the dirty file would succeed (the
    fast-forward only moves the branch pointer, not the file
    content), so the test setup MUST introduce a file the rebased
    feature rewrites AND the dirty worktree also modifies.

    This test exercises the dirty-worktree refusal:

    1. Seed a tracked file ``conflict.txt`` on the base branch.
    2. Fork ``wt_branch`` off the seed base.
    3. Add a commit on the primary ``feature`` branch that rewrites
       ``conflict.txt`` to a different value, so ``feature`` is
       genuinely ahead of ``wt_branch`` AND the merge into wt_branch
       would conflict with the linked worktree's local modification.
    4. Link a SECOND worktree on ``wt_branch`` and modify
       ``conflict.txt`` (untracked changes are ignored by
       ``is_repo_clean`` which uses ``--untracked-files=no``).
    5. Configure the integration target as ``wt_branch`` and run.
    6. Assert: the shared target ref does NOT advance; the dirty
       worktree's files are UNTOUCHED; the integration surfaces a
       loud retryable skip carrying the
       ``_TARGET_CHECKED_OUT_REFUSED`` reason.

    Then the test cleans the dirty file and re-runs integration, and
    asserts the same ``merge --ff-only`` now lands the target into
    the previously-dirty worktree atomically -- proving the
    "next clean seam" self-resume contract holds.
    """
    base = _base_branch(tmp_git_repo)
    wt_branch = "wt-target"
    # Seed a tracked file so the rebased feature can conflict with
    # the linked worktree's local modification. Without a CONFLICT
    # in the merged content, ``merge --ff-only`` would happily move
    # the branch pointer past the dirty file (a fast-forward is
    # purely a branch-pointer move when the merged commit doesn't
    # touch the dirty file).
    _commit(tmp_git_repo, "conflict.txt", "base content\n", "seed base")
    _run(tmp_git_repo, "branch", wt_branch, base)
    # Add a commit on the primary base branch so wt_branch is behind.
    _commit(tmp_git_repo, "base_marker.txt", "base marker\n", "base marker")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(
        tmp_git_repo,
        "conflict.txt",
        "feature content\n",
        "feature rewrites conflict.txt",
    )
    wt_branch_sha_before = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
    ).stdout.strip()
    assert feature_sha != wt_branch_sha_before
    # Add a SECOND worktree on wt_branch.
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _run(tmp_git_repo, "worktree", "add", str(wt_path), wt_branch)
    try:
        # Modify conflict.txt in the worktree -- the rebased feature
        # rewrites the same file, so ``merge --ff-only`` of the
        # feature tip into the dirty worktree will refuse (the local
        # change to conflict.txt conflicts with the merge's intended
        # update).
        tracked_in_wt = wt_path / "conflict.txt"
        tracked_in_wt.write_text("worktree dirty content\n", encoding="utf-8")
        wt_status = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "conflict.txt" in wt_status, (
            f"preflight: linked worktree must report the tracked dirty"
            f" file, got {wt_status!r}"
        )
        config = _build_config(tmp_git_repo, target=wt_branch)
        scope = WorkspaceScope(tmp_git_repo)
        outcome = auto_integrate_after_commit(config, scope, RebaseState())
        assert outcome is not None
        # The integration rebase replay produced a feature tip that
        # IS a fast-forward of wt_branch; the live worktree's
        # ``merge --ff-only`` is the only landing path. The dirty
        # worktree causes ``merge --ff-only`` to refuse (the worktree
        # modified conflict.txt and the rebased feature rewrote the
        # same file with a different value), so the shared ref is
        # LEFT UNTOUCHED and the record carries a loud retryable
        # skip.
        assert outcome.last_action in {"rebased", "merged"}, (
            f"AC-10: integration must still reach the rebase phase"
            f" (last_action reflects the rebase outcome), got"
            f" last_action={outcome.last_action!r}"
        )
        assert outcome.last_target == wt_branch
        assert outcome.fast_forwarded is False, (
            "AC-10: shared target ref MUST NOT advance while the "
            "checked-out sibling worktree refused merge --ff-only -- "
            "the previous CAS fallback silently desynced that checkout"
        )
        assert outcome.last_reason is not None, (
            "AC-08: integration MUST surface a loud retryable skip "
            "when the target's checked-out worktree refused merge --ff-only"
        )
        assert (
            "merge --ff-only" in outcome.last_reason
            or "checked out" in outcome.last_reason
        ), (
            f"AC-08: skip reason must name merge --ff-only and the "
            f"checked-out sibling, got {outcome.last_reason!r}"
        )
        # The shared ref is BYTE-UNCHANGED while the dirty worktree
        # refuses -- AC-10's central invariant.
        wt_branch_sha_after_dirty = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_branch_sha_after_dirty == wt_branch_sha_before, (
            f"AC-10: target ref MUST be byte-unchanged while the "
            f"sibling worktree refuses merge --ff-only, before="
            f"{wt_branch_sha_before!r} after={wt_branch_sha_after_dirty!r}"
        )
        # The dirty worktree's files are UNTOUCHED.
        assert (wt_path / "conflict.txt").exists(), (
            "AC-10: dirty file in the target worktree must not be removed"
        )
        assert (wt_path / "conflict.txt").read_text(
            encoding="utf-8"
        ) == "worktree dirty content\n", (
            "AC-10: dirty file in the target worktree must not be modified"
        )
        wt_status_after = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "conflict.txt" in wt_status_after, (
            f"AC-10: linked worktree must remain dirty, got status="
            f"{wt_status_after!r}"
        )
        # Now clean the worktree and re-run integration -- the
        # next clean seam contract holds, and the same
        # ``merge --ff-only`` path lands atomically. The ref moves
        # AND the worktree's index+working tree advance together.
        tracked_in_wt.write_text("base content\n", encoding="utf-8")
        wt_status_clean = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert wt_status_clean == "", (
            f"preflight: worktree must be clean before next-seam test, "
            f"got {wt_status_clean!r}"
        )
        outcome_clean = auto_integrate_after_commit(
            config, scope, RebaseState(), sleep=lambda _seconds: None, jitter=lambda: 0.0
        )
        assert outcome_clean is not None
        assert outcome_clean.fast_forwarded is True, (
            f"AC-09: next clean seam MUST land via merge --ff-only "
            f"with fast_forwarded=True, got {outcome_clean!r}"
        )
        wt_branch_sha_after_clean = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_branch_sha_after_clean != wt_branch_sha_before, (
            "AC-09: clean seam MUST advance the target ref"
        )
        assert wt_branch_sha_after_clean == feature_sha, (
            f"AC-09: clean seam MUST land the target at the rebased "
            f"feature tip, got {wt_branch_sha_after_clean!r} vs "
            f"{feature_sha!r}"
        )
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))
