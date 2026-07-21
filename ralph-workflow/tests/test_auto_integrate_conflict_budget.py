"""An unresolvable conflict must stop re-invoking the dev-agent resolver.

When the rebase conflicts, the endpoint merge conflicts and the
conflict resolver cannot fix it, auto-integration records
``last_action='conflict'`` and changes nothing -- and then the next
commit seam plus every qualifying phase boundary repeats the whole
sequence, each time paying up to two full agent invocations bounded at
900 s apiece. Nothing bounded that and nothing escalated, so from the
operator's seat the run appeared to stall on the conflict.

These tests pin the bounded budget, its reset-on-success rule, the
boundary that the FIRST attempt is never suppressed, the scoping rule
that the bound applies to ONE conflict rather than to a branch NAME,
and the invariant that must survive the change: with the agent
suppressed the endpoint merge still runs and still aborts cleanly,
leaving the feature branch bit-identical.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step) because
:func:`auto_integrate_after_commit` calls
``_current_branch_or_detached_marker``, which opens a real GitPython
``Repo``. ``timeout_seconds(10)`` sizes the budget for several real
conflicted integrations per test. This does not weaken any cap: the
file stays out of the 60 s combined budget and inside the 60 s
per-suite cap on ``make test-subprocess-e2e``.

The ``_run`` / ``_base_branch`` / ``_commit`` / ``_build_config`` /
``_diverged_conflicting_repo`` helpers are duplicated here to keep this
file standalone, matching the convention documented at
tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _run(
    repo_root: Path, *args: str, timeout: float = 10.0
) -> subprocess.CompletedProcess[str]:
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


def _build_config(target: str) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` with the auto-integrate knobs set."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    """Set up feature/base divergence with a guaranteed shared.txt conflict."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def _exhaust_budget(
    tmp_git_repo: Path, config: UnifiedConfig, resolver: ConflictResolver
) -> RebaseState:
    """Drive unresolved conflicts until the resolver budget is spent.

    Returns the resulting :class:`RebaseState` -- what a real run
    carries into the next integration seam.
    """
    state = RebaseState()
    for _ in range(MAX_CONSECUTIVE_RESOLVER_ATTEMPTS + 1):
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=resolver,
        )
        assert result is not None
        assert result.last_action == "conflict"
        state = result
    return state


def _snapshot(tmp_git_repo: Path) -> tuple[str, str]:
    """Capture HEAD SHA and porcelain status for bit-identity comparisons."""
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    status = _run(tmp_git_repo, "status", "--porcelain").stdout
    return head, status


def test_auto_integrate_regression_unresolvable_conflict_stops_reinvoking_the_resolver(
    tmp_git_repo: Path,
) -> None:
    """The resolver is invoked a bounded number of times, then escalated."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    state = RebaseState()
    outcomes: list[RebaseState] = []
    for _ in range(MAX_CONSECUTIVE_RESOLVER_ATTEMPTS + 2):
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=_never_resolves,
        )
        assert result is not None
        outcomes.append(result)
        state = result

    assert len(invocations) == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    final = outcomes[-1]
    assert final.last_action == "conflict"
    assert final.last_reason is not None
    assert "budget" in final.last_reason
    assert base in final.last_reason


def test_first_conflict_attempt_is_never_suppressed(tmp_git_repo: Path) -> None:
    """The boundary case: a fresh state always gets its first attempt."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base]
    assert result is not None
    assert result.consecutive_conflicts == 1


def test_successful_land_resets_the_conflict_budget(tmp_git_repo: Path) -> None:
    """A resolver that succeeds clears the counter for later conflicts."""
    base = _diverged_conflicting_repo(tmp_git_repo)

    def _resolves(repo_root: Path, target: str) -> bool:
        (repo_root / "shared.txt").write_text(
            "feature version\nbase version 1\n", encoding="utf-8"
        )
        return True

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(
            last_action="conflict",
            last_target=base,
            consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS - 1,
        ),
        conflict_resolver=_resolves,
    )

    assert result is not None
    assert result.fast_forwarded is True
    assert result.consecutive_conflicts == 0


def test_exhausted_budget_still_aborts_the_endpoint_merge_bit_identically(
    tmp_git_repo: Path,
) -> None:
    """Suppressing the agent must not weaken the both-conflicted contract."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    before = _snapshot(tmp_git_repo)

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(
            last_action="conflict",
            last_target=base,
            consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        ),
        conflict_resolver=_never_resolves,
    )

    assert invocations == []
    assert result is not None
    assert result.last_action == "conflict"
    assert _snapshot(tmp_git_repo) == before
    assert not (tmp_git_repo / ".git" / "MERGE_HEAD").exists()


def test_new_feature_commit_gets_a_fresh_resolver_budget(
    tmp_git_repo: Path,
) -> None:
    """A changed feature tip is a NEW conflict, even against the same target.

    The bound is per unresolved conflict, not per mainline branch name.
    Without a durable conflict identity the exhausted budget of an
    older conflict would silently suppress the resolver for work the
    agent has never seen -- and because nothing has landed, nothing
    would ever reset it.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    assert spent.last_conflict_feature_sha is not None
    assert spent.last_conflict_target_sha is not None

    # New feature work against the SAME target, with nothing landed in
    # between: the conflict is different, so the budget starts over.
    new_feature_sha = _commit(
        tmp_git_repo, "shared.txt", "feature version 2\n", "feature shared 2"
    )

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * (MAX_CONSECUTIVE_RESOLVER_ATTEMPTS + 1)
    assert result is not None
    assert result.last_action == "conflict"
    assert result.consecutive_conflicts == 1
    assert result.last_conflict_feature_sha == new_feature_sha
    assert result.last_reason is not None
    assert "budget" not in result.last_reason


def test_moved_mainline_tip_gets_a_fresh_resolver_budget(
    tmp_git_repo: Path,
) -> None:
    """Another agent landing on the target also makes it a new conflict."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS

    # A concurrent agent advances the shared mainline; the feature tip
    # is untouched but the conflict is now against a different commit.
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    moved_target_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "checkout", "feature")

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * (MAX_CONSECUTIVE_RESOLVER_ATTEMPTS + 1)
    assert result is not None
    assert result.last_action == "conflict"
    assert result.consecutive_conflicts == 1
    assert result.last_conflict_target_sha == moved_target_sha


def test_unchanged_conflict_after_a_skip_stays_suppressed(
    tmp_git_repo: Path,
) -> None:
    """An unrelated skip must neither consume nor forgive the budget.

    An early skip short-circuits before the budget is applied, so
    without an explicit carry the skip record would arrive at the next
    seam holding the model defaults and REFUND the exhausted budget.
    The count is the assertion that bites here; the identity is
    carried alongside it so the two never disagree.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        return False

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS

    # An uncommitted edit to a TRACKED file makes the next seam a
    # recorded skip, not a conflict; the count and the identity both
    # have to survive it. (An untracked file would not: the commit
    # seam's relaxed preconditions permit those.)
    (tmp_git_repo / "base_seed.txt").write_text("dirty\n", encoding="utf-8")
    skipped = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )
    assert skipped is not None
    assert skipped.last_action == "skipped"
    assert skipped.consecutive_conflicts == spent.consecutive_conflicts
    assert skipped.last_conflict_feature_sha == spent.last_conflict_feature_sha
    assert skipped.last_conflict_target_sha == spent.last_conflict_target_sha

    (tmp_git_repo / "base_seed.txt").write_text("base seed\n", encoding="utf-8")
    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        skipped,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    assert result is not None
    assert result.last_reason is not None
    assert "budget" in result.last_reason


def test_a_raising_resolver_is_still_bounded(tmp_git_repo: Path) -> None:
    """A resolver that RAISES is bounded like any unresolved conflict.

    ``endpoint_merge_with_resolution`` contains resolver exceptions and
    reports ``resolution_failed``, so a crashing agent reaches the seam
    as an ordinary unresolved conflict. This pins that the containment
    still routes into the budget -- an agent that reliably crashes must
    hit the cap rather than being re-invoked at every seam forever.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _raises(repo_root: Path, target: str) -> bool:
        invocations.append(target)
        raise RuntimeError("simulated resolver crash")

    state = RebaseState()
    for _ in range(MAX_CONSECUTIVE_RESOLVER_ATTEMPTS + 2):
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=_raises,
        )
        assert result is not None
        state = result

    assert len(invocations) == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    assert state.last_target == base
    assert state.consecutive_conflicts >= MAX_CONSECUTIVE_RESOLVER_ATTEMPTS


def test_unexpected_failure_mid_attempt_does_not_refund_the_budget(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash inside the attempt keeps the budget scoped to the target.

    An exception raised anywhere in the integration attempt used to be
    caught only by the outermost guard, which recorded a skip built
    from the model defaults -- ``consecutive_conflicts=0`` and
    ``last_target=None``. A count scoped to ``None`` is discarded at
    the next seam's target comparison, so every crash handed the
    resolver a fresh budget for a conflict it had already failed.
    """
    import ralph.pipeline.auto_integrate as auto_integrate_module

    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)

    def _never_resolves(repo_root: Path, target: str) -> bool:
        return False

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated mid-attempt crash")

    monkeypatch.setattr(auto_integrate_module, "_write_record", _boom)

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert result is not None
    assert result.last_action == "skipped"
    assert result.last_reason is not None
    assert "simulated mid-attempt crash" in result.last_reason
    # Scoped to the real target, so the next seam still recognises the
    # count as its own instead of discarding it.
    assert result.last_target == base
    assert result.consecutive_conflicts >= spent.consecutive_conflicts
    assert result.last_conflict_feature_sha == spent.last_conflict_feature_sha
