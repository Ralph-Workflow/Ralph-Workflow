"""Fault-tolerance tests for the auto-integrate resolution + retry paths.

Covers the behaviors added on top of the wt-038 baseline:

* A FAILED rebase (not just a conflicted one) falls back to the
  endpoint merge.
* A conflicted endpoint merge is handed to the ``conflict_resolver``
  seam; on success the merge is committed deterministically and the
  fast-forward proceeds. Resolver failure / exception aborts the
  merge bit-identically and records a conflict.
* A fast-forward lost to a concurrent target move triggers an
  immediate bounded re-integration instead of waiting for the next
  commit.
* The phase-transition hook integrates at clean-worktree phase
  boundaries, records a skip when uncommitted TRACKED changes defer a
  catch-up the target actually had, and stays silent when there is
  nothing to do.

Same conventions as :mod:`tests.test_auto_integrate` (real per-test git
repositories; helpers duplicated to avoid brittle test-module imports).
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


def _snapshot(tmp_git_repo: Path) -> dict[str, object]:
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


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    """Set up feature/base divergence with a guaranteed shared.txt conflict."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def test_rebase_failed_falls_back_to_endpoint_merge(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebase failure (not just a conflict) still tries the endpoint merge."""
    import ralph.pipeline.auto_integrate as _ai_mod
    from ralph.git.rebase._rebase_kind import RebaseKind
    from ralph.git.rebase.rebase import RebaseFailed
    from ralph.git.rebase.rebase_kinds import RebaseErrorKind

    base = _base_branch(tmp_git_repo)
    # Disjoint changes: the endpoint merge will succeed cleanly.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base")
    _run(tmp_git_repo, "checkout", "feature")

    def _fake_rebase_onto(
        target: str, *, repo_root: Path, options: object = None
    ) -> object:
        return RebaseFailed(
            kind=RebaseErrorKind(
                kind=RebaseKind.UNKNOWN, metadata={"details": "simulated failure"}
            )
        )

    monkeypatch.setattr(_ai_mod, "rebase_onto", _fake_rebase_onto)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"a failed rebase must fall back to the endpoint merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_conflict_resolver_resolves_commits_and_fast_forwards(
    tmp_git_repo: Path,
) -> None:
    """A resolver that fixes the conflict yields a merge commit + ff."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    feature_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    def _resolver(root: Path, target: str) -> bool:
        (root / "shared.txt").write_text("resolved version\n", encoding="utf-8")
        _run(root, "add", "shared.txt")
        return True

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"resolved conflicts must complete as a merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    # The merge was committed automatically: HEAD is a 2-parent commit.
    head_parents = _run(tmp_git_repo, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    # The resolved content landed.
    assert (tmp_git_repo / "shared.txt").read_text() == "resolved version\n"
    # Target fast-forwarded to the merge commit.
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    # No merge state or crash record left behind.
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "MERGE_HEAD").exists()
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists()


def test_conflict_resolver_failure_aborts_and_records_conflict(
    tmp_git_repo: Path,
) -> None:
    """A failing resolver leaves the branch bit-identical and records conflict."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> bool:
        return False

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.last_reason is not None
    assert "conflict resolution failed" in outcome.last_reason
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"]
    assert after["worktree"] == before["worktree"]
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "MERGE_HEAD").exists()


def test_conflict_resolver_exception_aborts_and_records_conflict(
    tmp_git_repo: Path,
) -> None:
    """A resolver that raises is contained: abort, bit-identical, recorded."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> bool:
        raise RuntimeError("resolver blew up")

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.last_action == "conflict"
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"]
    assert after["worktree"] == before["worktree"]


def test_auto_integrate_regression_deterministic_ff_refusal_does_not_retry(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analysis feedback: a safety refusal makes exactly one integration pass."""
    import ralph.pipeline.auto_integrate as _ai_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    calls: list[int] = []

    def _dirty_target_ff(
        repo_root: Path, target: str, feature_sha: str
    ) -> tuple[bool, str]:
        calls.append(1)
        return False, "target worktree dirty"

    monkeypatch.setattr(_ai_mod, "_fast_forward_target", _dirty_target_ff)

    outcome = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert outcome.last_reason == "target worktree dirty"
    assert calls == [1]


def test_ff_race_retries_integration_onto_moved_target(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent target move during ff triggers an immediate re-integration.

    The first fast-forward attempt is forced to fail (simulating the
    AC-08 CAS race); the step must re-run the rebase onto the moved
    target and fast-forward successfully instead of giving up until
    the next commit.
    """
    import ralph.pipeline.auto_integrate as _ai_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")

    real_ff = _ai_mod._fast_forward_target
    calls: list[int] = []
    intervening_shas: list[str] = []

    def _flaky_ff(repo_root: Path, target: str, feature_sha: str) -> tuple[bool, str]:
        calls.append(1)
        if len(calls) == 1:
            _run(repo_root, "checkout", target)
            intervening_sha = _commit(
                repo_root, "intervening.txt", "intervening\n", "concurrent target move"
            )
            _run(repo_root, "checkout", "feature")
            intervening_shas.append(intervening_sha)
            assert intervening_sha == _run(
                repo_root, "rev-parse", f"refs/heads/{target}"
            ).stdout.strip()
            return False, "target advanced concurrently (CAS mismatch)"
        return real_ff(repo_root, target, feature_sha)

    monkeypatch.setattr(_ai_mod, "_fast_forward_target", _flaky_ff)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert len(calls) == 2, "ff race must trigger exactly one immediate retry"
    assert outcome.fast_forwarded is True, (
        f"retry must land the fast-forward, got action={outcome.last_action!r}"
        f" reason={outcome.last_reason!r}"
    )
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert _run(
        tmp_git_repo, "merge-base", "--is-ancestor", intervening_shas[0], "HEAD"
    ).returncode == 0


def test_phase_transition_integrates_when_target_moved(tmp_git_repo: Path) -> None:
    """A clean worktree + moved target at a phase boundary re-integrates."""
    from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base moved")
    _run(tmp_git_repo, "checkout", "feature")

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action in {"rebased", "merged"}
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha


def test_phase_transition_records_a_skip_on_a_dirty_worktree(
    tmp_git_repo: Path,
) -> None:
    """A dirty boundary that suppressed real catch-up work is RECORDED.

    Supersedes the former ``silent_on_dirty_worktree`` expectation on
    both halves.

    * AC-01: the cleanliness probe now runs
      ``--untracked-files=no``, so this test's original fixture (an
      untracked ``wip.txt``) no longer makes the worktree dirty at
      all. Deferring requires an uncommitted TRACKED modification,
      which is what the fixture now creates.
    * AC-02: a deferral whose resolved target carries commits this
      checkout lacks suppressed a genuine cross-agent catch-up, so it
      is recorded instead of returning ``None``. An invisible
      suppression is indistinguishable from a feature that does not
      work, which is exactly how the defect was reported.

    The repository must still be byte-identical afterwards: recording a
    diagnostic is not permission to mutate anything.
    """
    from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base moved")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "feat.txt").write_text("uncommitted work\n", encoding="utf-8")
    before = _snapshot(tmp_git_repo)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_target == base
    assert outcome.last_reason is not None
    assert "worktree not clean" in outcome.last_reason
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_phase_transition_silent_when_nothing_to_integrate(
    tmp_git_repo: Path,
) -> None:
    """Target at feature tip: the boundary hook does nothing, silently."""
    from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    # feature == target tip exactly.
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is None
    assert _snapshot(tmp_git_repo) == before


# ---------------------------------------------------------------------------


def test_resolver_that_only_edits_files_still_lands_the_merge(
    tmp_git_repo: Path,
) -> None:
    """AC-02: a resolver issuing NO git command still lands the merge.

    Ralph's own MCP exec policy denies every git invocation, so a
    resolver agent can never stage anything. Ralph must therefore stage
    the previously-conflicted paths itself; before that fix this
    resolver produced ``resolution_failed``.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    feature_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    def _resolver(root: Path, target: str) -> bool:
        (root / "shared.txt").write_text("edited only\n", encoding="utf-8")
        return True

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"a git-less resolver must still land the merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    assert (tmp_git_repo / "shared.txt").read_text() == "edited only\n"
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha


def test_resolver_leaving_conflict_markers_is_rejected(tmp_git_repo: Path) -> None:
    """AC-02: a marker-bearing "resolution" must never be committed.

    ``git add`` on a file that still holds ``<<<<<<<`` markers silently
    clears its unmerged state, so the git-authoritative unmerged-path
    check alone cannot catch this.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> bool:
        (root / "shared.txt").write_text(
            "<<<<<<< HEAD\nfeature version\n=======\nbase version 1\n>>>>>>> main\n",
            encoding="utf-8",
        )
        return True

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"], "no merge commit may be created"
    before_refs = before["refs"]
    after_refs = after["refs"]
    assert isinstance(before_refs, dict)
    assert isinstance(after_refs, dict)
    assert after_refs[f"refs/heads/{base}"] == before_refs[f"refs/heads/{base}"]


@pytest.mark.parametrize(
    "half_resolved",
    [
        "<<<<<<< HEAD\nfeature version\nbase version 1\n",
        "feature version\nbase version 1\n>>>>>>> main\n",
    ],
    ids=["opening-fence-only", "closing-fence-only"],
)
def test_resolver_regression_lone_conflict_marker_is_rejected(
    tmp_git_repo: Path, half_resolved: str
) -> None:
    """A resolver that deletes only one conflict fence is still rejected.

    The marker scan is the only gate left once Ralph stages the
    previously-conflicted paths -- ``git add`` clears the unmerged bit
    -- and it used to demand BOTH fences before reporting a file. A
    resolution that removed just one of them therefore passed both
    gates and was committed with the conflict text intact.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> bool:
        (root / "shared.txt").write_text(half_resolved, encoding="utf-8")
        return True

    outcome = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"], "no merge commit may be created"
    before_refs = before["refs"]
    after_refs = after["refs"]
    assert isinstance(before_refs, dict)
    assert isinstance(after_refs, dict)
    assert after_refs[f"refs/heads/{base}"] == before_refs[f"refs/heads/{base}"]
    assert not (tmp_git_repo / ".git" / "MERGE_HEAD").exists()
