"""Real-Git proof that auto-rebase is idempotent and resumes a rebase already underway.

Two guarantees, each driven through ``integrate_before_planning`` against
real repositories:

* Running it again on a branch it already caught up changes nothing.
* Starting inside a paused, conflicted rebase hands the stop to the
  conflict resolver instead of ignoring, aborting or looping on it; a
  resolver that declines leaves the branch unlanded and the next run asks
  again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.branch_relation import BranchRelation, branch_relation
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

_TARGET = "main"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ("git", *args), cwd=str(repo), capture_output=True, text=True, check=False, timeout=20
    )
    return done.stdout.strip()


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"{name}: {content.strip()}")


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _commit(repo, "shared.txt", "base\n")
    _git(repo, "branch", "-M", _TARGET)
    _git(repo, "checkout", "-q", "-b", "feature")


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _diverge(repo: Path, *, conflicting: bool) -> None:
    """Feature and main each add a commit; ``conflicting`` makes them touch one file."""
    _commit(repo, "shared.txt" if conflicting else "feature.txt", "feature side\n")
    _git(repo, "checkout", "-q", _TARGET)
    _commit(repo, "shared.txt" if conflicting else "target.txt", "target side\n")
    _git(repo, "checkout", "-q", "feature")


class _Resolver:
    """Rebase-stop resolver that records its calls and optionally settles the stop."""

    def __init__(self, *, resolves: bool) -> None:
        self.resolves = resolves
        self.calls = 0

    def __call__(self, root: Path, _target: str, _stop: object) -> bool:
        self.calls += 1
        if self.resolves:
            (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
        return self.resolves


def _integrate(repo: Path, resolver: _Resolver) -> RebaseState | None:
    return integrate_before_planning(
        _config(),
        WorkspaceScope(repo),
        RebaseState(),
        conflict_resolver=lambda _root, _target: False,
        rebase_stop_resolver=resolver,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_second_run_after_catch_up_changes_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _diverge(repo, conflicting=False)
    resolver = _Resolver(resolves=True)

    _integrate(repo, resolver)
    settled = _git(repo, "rev-parse", "HEAD")
    again = _integrate(repo, resolver)

    assert not branch_relation(repo, _TARGET).needs_catch_up
    assert _git(repo, "rev-parse", "HEAD") == settled
    assert _git(repo, "status", "--porcelain") == ""
    assert again is None or again.integration_unresolved is False


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_starting_inside_a_conflicted_rebase_sends_the_stop_to_the_resolver(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _diverge(repo, conflicting=True)
    _git(repo, "rebase", _TARGET)
    assert rebase_in_progress(repo), "fixture must leave a rebase paused on a conflict"
    resolver = _Resolver(resolves=True)

    _integrate(repo, resolver)

    assert resolver.calls >= 1
    assert not rebase_in_progress(repo)
    assert not branch_relation(repo, _TARGET).needs_catch_up
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "resolved\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_a_declining_resolver_never_reports_the_rebase_landed_and_is_asked_again(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _diverge(repo, conflicting=True)
    _git(repo, "rebase", _TARGET)
    resolver = _Resolver(resolves=False)

    first = _integrate(repo, resolver)
    asked = resolver.calls
    _integrate(repo, resolver)

    assert asked >= 1
    assert resolver.calls > asked
    assert first is None or first.fast_forwarded is False
    assert rebase_in_progress(repo)
    assert branch_relation(repo, _TARGET) is BranchRelation.UNKNOWN


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
@pytest.mark.parametrize("shape", ["target_ahead", "diverged"])
def test_every_starting_shape_converges_to_target_inside_the_branch(
    tmp_path: Path, shape: str
) -> None:
    """Whatever the branch's relation to main, planning integration ends caught up.

    ``target_ahead`` is the regression: the feature sits wholly inside a
    ``main`` that moved on, which the idempotent-land shortcut used to
    declare landed while the planning gate still blocked.
    """
    repo = tmp_path / "repo"
    _init(repo)
    if shape == "diverged":
        _commit(repo, "feature.txt", "feature side\n")
    _git(repo, "checkout", "-q", _TARGET)
    _commit(repo, "target.txt", "target side\n")
    _git(repo, "checkout", "-q", "feature")

    outcome = _integrate(repo, _Resolver(resolves=True))

    assert not branch_relation(repo, _TARGET).needs_catch_up, outcome
