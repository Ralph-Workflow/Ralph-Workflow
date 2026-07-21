"""An unresolvable conflict must stop re-invoking the dev-agent resolver.

When the rebase conflicts, the endpoint merge conflicts and the
conflict resolver cannot fix it, auto-integration records
``last_action='conflict'`` and changes nothing -- and then the next
commit seam plus every qualifying phase boundary repeats the whole
sequence, each time paying up to two full agent invocations bounded at
900 s apiece. Nothing bounded that and nothing escalated, so from the
operator's seat the run appeared to stall on the conflict.

These tests pin the bounded budget, its reset-on-success rule, the
boundary that the FIRST attempt is never suppressed, and the invariant
that must survive the change: with the agent suppressed the endpoint
merge still runs and still aborts cleanly, leaving the feature branch
bit-identical.

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

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

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
