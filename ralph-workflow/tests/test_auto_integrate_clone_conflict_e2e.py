"""Clone-topology conflict (AC-08) and uninstrumented worktree race (AC-09).

Two production paths that no existing test reached:

* Every clone-topology test takes a CLEAN rebase or fast-forward, so
  "the origin mainline moved with conflicting content" -- remote refresh
  composed with a real conflict -- was untested.
* Every concurrency test injects the concurrent target move by
  monkeypatching ``auto_integrate._fast_forward_target`` (the clearest
  example is
  ``tests/test_auto_integrate_worktree_sync.py::test_sibling_worktree_landing_mid_integration_is_retried_and_lands``),
  so the PRODUCTION detection inside
  :func:`ralph.pipeline.auto_integrate_ff.fast_forward_target` had never
  been exercised on its own, and no test moved the mainline *during*
  one ``auto_integrate_after_commit`` call and then proved that same
  call detects the move, retries and lands.

Every test here sequences its concurrency deterministically -- the
sibling lands at a fixed point in the test body, or on a real git
``post-rewrite`` hook boundary that git fires mid-integration -- with
no sleep, no thread and no polling, per
docs/ralph-workflow-policy/testing-policy.md. Every remote is a local
bare repository path: no test reaches a real network host.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step): every test drives real git.
``timeout_seconds(20)`` sizes the budget for a clone plus a conflicted
integration. This does not weaken any cap: the file stays out of the
60 s combined budget and inside the 60 s per-suite cap on ``make
test-subprocess-e2e``.

The ``_run`` / ``_commit`` / ``_make_clone`` / ``_seed_bare_origin`` /
``_add_worktree`` helpers are duplicated here to keep this file
standalone, matching the convention documented at
tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    is_retryable_fast_forward_failure,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_ORIGINAL_SHARED = "line one\n"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _base_branch(repo_root: Path) -> str:
    return (
        _run(repo_root, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
    )


def _build_config(*, fetch_enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_fetch_enabled": fetch_enabled,
                "auto_integrate_fetch_timeout_seconds": 5.0,
            }
        }
    )


def _make_clone(bare: Path, path: Path, main: str, *, branch: str) -> Path:
    """Clone-topology checkout with a materialized local ``main``."""
    path.mkdir()
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    assert _run(path, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(path, "fetch", "origin", main).returncode == 0
    assert _run(path, "checkout", "-b", main, f"origin/{main}").returncode == 0
    assert _run(path, "checkout", "-b", branch).returncode == 0
    return path


def _seed_bare_origin(tmp_git_repo: Path) -> tuple[Path, str]:
    """Return ``(bare_origin_path, main_branch_name)``."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / "origin.git"
    assert (
        _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode
        == 0
    )
    return bare, main


def _add_worktree(repo_root: Path, path: Path, branch: str) -> None:
    assert _run(repo_root, "worktree", "add", "-b", branch, str(path)).returncode == 0


def test_conflicting_origin_advance_rebases_conflicted_then_merges_and_lands(
    tmp_git_repo: Path,
) -> None:
    """AC-08: remote refresh and real conflict handling compose and land.

    The feature branch edits ``shared.txt`` and then restores it, so its
    NET content is unchanged from the merge base while its individual
    commits are not. Replaying those commits onto a mainline that edited
    the same line conflicts, while the single endpoint three-way merge
    is clean -- which is exactly the path a clone-topology agent takes
    when another agent pushed a conflicting mainline change.
    """
    _commit(tmp_git_repo, "shared.txt", _ORIGINAL_SHARED, "seed shared")
    bare, main = _seed_bare_origin(tmp_git_repo)
    agent = _make_clone(bare, tmp_git_repo.parent / "agent-a", main, branch="feature")
    other = _make_clone(bare, tmp_git_repo.parent / "agent-b", main, branch="other")

    # The OTHER agent pushes a CONFLICTING mainline change.
    assert _run(other, "checkout", main).returncode == 0
    other_sha = _commit(other, "shared.txt", "base edit\n", "base edit")
    assert _run(other, "push", "origin", main).returncode == 0

    stale_local_main = branch_sha(agent, main)
    assert stale_local_main is not None
    assert stale_local_main != other_sha, "the local ref must start out stale"

    _commit(agent, "shared.txt", "feature edit\n", "feature edit")
    _commit(agent, "shared.txt", _ORIGINAL_SHARED, "feature restores shared")

    outcome = auto_integrate_after_commit(
        _build_config(), WorkspaceScope(agent), RebaseState()
    )
    feature_head = _run(agent, "rev-parse", "HEAD").stdout.strip()

    assert outcome is not None
    # The rebase conflicted; the endpoint merge landed it.
    assert outcome.last_action == "merged"
    assert outcome.fast_forwarded is True
    # The moved mainline was fetched and is now contained in the feature.
    assert is_ancestor(agent, other_sha, feature_head) is True
    assert (agent / "shared.txt").read_text(encoding="utf-8") == "base edit\n"
    assert branch_sha(agent, main) == feature_head
    assert not (agent / ".git" / "MERGE_HEAD").exists()


def test_production_fast_forward_refuses_a_target_a_sibling_worktree_moved(
    tmp_git_repo: Path,
) -> None:
    """AC-09 (detection half): no monkeypatching anywhere in this test.

    A sibling worktree lands a real commit on the shared mainline, then
    the PRODUCTION fast-forward is asked to land a feature SHA observed
    before that landing. It must refuse -- and refuse with a reason the
    bounded retry loop treats as transient, which is what makes the
    re-integration in the next test possible.
    """
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-a"
    _add_worktree(tmp_git_repo, feature, "feature-a")
    try:
        stale_feature_sha = _commit(
            feature, "feature.txt", "feature\n", "feature change"
        )
        # The sibling worktree lands on the SHARED mainline ref.
        sibling_sha = _commit(tmp_git_repo, "main.txt", "main\n", "sibling change")
        assert branch_sha(tmp_git_repo, main) == sibling_sha

        landed, reason = fast_forward_target(feature, main, stale_feature_sha)

        assert landed is False
        assert is_retryable_fast_forward_failure(reason) is True
        # The shared ref was not moved backwards under the live checkout.
        assert branch_sha(tmp_git_repo, main) == sibling_sha
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_sibling_worktree_landing_is_reintegrated_and_lands_both_commits(
    tmp_git_repo: Path,
) -> None:
    """AC-09 (recovery half): the integration lands onto the moved tip.

    Same uninstrumented two-worktree topology; here the full production
    integration runs after the sibling landed, and the shared mainline
    must end up containing BOTH agents' commits.
    """
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-b"
    _add_worktree(tmp_git_repo, feature, "feature-b")
    try:
        feature_sha = _commit(feature, "feature.txt", "feature\n", "feature change")
        sibling_sha = _commit(tmp_git_repo, "main.txt", "main\n", "sibling change")

        outcome = auto_integrate_after_commit(
            _build_config(fetch_enabled=False),
            WorkspaceScope(feature),
            RebaseState(),
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
        # BOTH agents' work is on the shared mainline: the sibling's
        # commit by ancestry, the feature's by content (the rebase
        # rewrote its SHA, so the pre-rebase SHA is deliberately not
        # asserted as an ancestor).
        assert is_ancestor(feature, sibling_sha, feature_head) is True
        assert feature_head != feature_sha
        assert (feature / "feature.txt").exists()
        assert (feature / "main.txt").exists()
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_target_moved_mid_integration_is_detected_retried_and_lands_both(
    tmp_git_repo: Path,
) -> None:
    """AC-09 (composed): the move happens INSIDE one integration call.

    The two tests above prove the halves separately -- production
    refusal, and recovery when the sibling landed before the call. The
    production failure the user reported is the composition: the sibling
    lands *while* this agent is integrating, so the feature SHA the
    landing is about to publish was computed against a mainline that no
    longer exists.

    NO monkeypatching anywhere -- not ``_refresh_target``, not
    ``_fast_forward_target``, not any other production dependency. The
    move is sequenced by a real ``post-rewrite`` git hook, so the whole
    integration runs through production code untouched. Two facts make
    this deterministic:

    * The mainline is advanced ONCE before the call, so attempt 1's
      rebase actually replays the feature commit onto that advance.
      (A no-op rebase runs no ``git rebase`` subprocess and would fire
      no hook.)
    * ``git`` fires ``post-rewrite`` the instant it has finished
      rewriting the feature branch and BEFORE the production
      fast-forward observes the target -- the exact mid-integration
      instant. The hook lands a sibling commit on the shared mainline
      from the primary worktree, so the feature SHA attempt 1 is about
      to publish was computed against a tip that no longer exists.

    A guard file lands the sibling exactly once, so attempt 2's replay
    re-fires the hook harmlessly. The PRODUCTION fast-forward, the
    PRODUCTION retryable-reason classification and the PRODUCTION
    bounded retry loop are all exercised unmodified. No sleep, no
    thread, no polling: git's own execution order is the
    synchronization.
    """
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-c"
    _add_worktree(tmp_git_repo, feature, "feature-c")
    guard = tmp_git_repo.parent / "prw-guard"
    sibling_sha_file = tmp_git_repo.parent / "prw-sibling-sha"
    # Linked worktrees share the common git dir, so a hook installed on
    # the primary repo also fires for the feature worktree's rebase.
    hook = tmp_git_repo / ".git" / "hooks" / "post-rewrite"
    buf = io.StringIO()
    handler_id = logger.add(buf, level="INFO")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        # A prior mainline advance so attempt 1's rebase replays the
        # feature commit (and thus fires post-rewrite) rather than
        # short-circuiting as an already-up-to-date no-op.
        _commit(tmp_git_repo, "base.txt", "prior main advance\n", "prior main advance")

        hook.parent.mkdir(parents=True, exist_ok=True)
        # The hook lands the sibling ONCE (guarded), from the primary
        # worktree, clearing any inherited GIT_* env so the commit binds
        # to that repo rather than the in-flight rebase.
        hook.write_text(
            f"""#!/bin/sh
if [ -f "{guard}" ]; then exit 0; fi
touch "{guard}"
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_REFLOG_ACTION
cd "{tmp_git_repo}"
printf 'sibling\\n' > main.txt
git add main.txt
git commit -qm "sibling landing"
git rev-parse HEAD > "{sibling_sha_file}"
""",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        outcome = auto_integrate_after_commit(
            _build_config(fetch_enabled=False),
            WorkspaceScope(feature),
            RebaseState(),
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()
        sibling_sha = sibling_sha_file.read_text(encoding="utf-8").strip()

        assert outcome is not None
        # The sibling really landed mid-integration, exactly once.
        assert sibling_sha
        assert _run(tmp_git_repo, "log", "--oneline").stdout.count("sibling landing") == 1
        # Attempt 1's stale fast-forward was refused: the production
        # bounded loop logged the refusal before retrying.
        assert "fast-forward did not land on attempt 1" in buf.getvalue()
        # ... and the bounded retry re-integrated onto the moved tip.
        assert outcome.fast_forwarded is True, (
            f"retry must land, got action={outcome.last_action!r} reason={outcome.last_reason!r}"
        )
        # The shared mainline carries BOTH agents' work: the sibling's
        # commit by ancestry, the feature's by content (the rebase
        # rewrote its SHA).
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(feature, sibling_sha, feature_head) is True
        assert (feature / "feature.txt").exists()
        assert (feature / "main.txt").exists()
    finally:
        logger.remove(handler_id)
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))
