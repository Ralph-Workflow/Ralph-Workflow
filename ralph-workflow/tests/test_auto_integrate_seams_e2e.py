"""All four integration seams, in both topologies agents actually run in.

Auto-integration is reachable from four places -- run startup, the
post-commit boundary, every phase transition, and the fan-out join --
and Ralph agents share one mainline in two different repository
layouts. Before this file, only the after-commit seam had a real-git
proof, and only in a single-repository layout, so "auto-rebase does not
work" could be true for three of the four seams while every suite
stayed green.

The two topologies are not interchangeable:

* **linked worktree**: several agents share ONE common git dir via
  ``git worktree add``. ``refs/heads/<target>`` is shared, so the local
  ref IS the authoritative pointer and the origin refresh correctly
  reports ``REFRESH_NO_ORIGIN`` -- see
  :mod:`ralph.pipeline.auto_integrate_sync`. Landing here must go
  through the sibling worktree that has the target checked out.
* **clone**: several agents hold separate clones of one origin. The
  local target ref goes stale the moment another agent pushes, so the
  refresh is what keeps the landing correct.

Every "origin" here is a local bare repository addressed by filesystem
path: no test reaches a real network host. No agent process is launched
-- none of these cases conflict, so the seams are called with
``conflict_resolver=None`` or with the seam's own resolver over a
non-conflicting integration.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step): every test drives real git.
``timeout_seconds(20)`` sizes the budget for a clone or worktree
fixture plus a full integration. This does not weaken any cap: the file
stays out of the 60 s combined budget, and it runs under the bounded
``make test-auto-integrate-e2e`` target that ``make verify`` invokes.

The ``_run`` / ``_commit`` / ``_build_config`` helpers are duplicated
here to keep this file standalone, matching the convention documented
at tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import MagicMock

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.operations import get_head_sha
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_sync import REFRESH_LOCAL_FLEET
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_TARGET = "main"


class _Topology(NamedTuple):
    """One agent's checkout plus the collaborators that move the mainline.

    Attributes:
        root: The repository root the integration seams run against.
        origin: Bare origin, or ``None`` in the linked-worktree layout.
        sibling: The other agent's checkout: the primary worktree, or
            the second clone.
    """

    root: Path
    origin: Path | None
    sibling: Path


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _init_repo(path: Path) -> None:
    """Seed a repository whose default branch is named ``main``.

    ``check_rebase_preconditions`` refuses a repository with no
    committer identity, so it is set per repository rather than relying
    on a HOME-level config the test cannot control.
    """
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", _TARGET).returncode == 0


def _clone_for_agent(origin: Path, path: Path, *, branch: str | None) -> Path:
    """Clone-layout checkout with a materialized local ``main``."""
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    assert _run(path, "remote", "add", "origin", str(origin)).returncode == 0
    assert _run(path, "fetch", "origin", _TARGET).returncode == 0
    assert (
        _run(path, "checkout", "-b", _TARGET, f"origin/{_TARGET}").returncode == 0
    )
    if branch is not None:
        assert _run(path, "checkout", "-b", branch).returncode == 0
    return path


def _linked_worktree_topology(tmp_path: Path) -> _Topology:
    """One common git dir, the target checked out in a sibling worktree."""
    primary = tmp_path / "primary"
    _init_repo(primary)
    root = tmp_path / "agent-a"
    assert _run(primary, "worktree", "add", "-b", "feature", str(root)).returncode == 0
    # The sibling agent lands on the shared mainline ref.
    _commit(primary, "mainline.txt", "mainline\n", "mainline work")
    # This agent has its own commit to integrate.
    _commit(root, "feature.txt", "feature\n", "feature work")
    return _Topology(root=root, origin=None, sibling=primary)


def _clone_topology(tmp_path: Path) -> _Topology:
    """Two clones of one bare origin; the sibling pushes a mainline commit."""
    upstream = tmp_path / "upstream"
    _init_repo(upstream)
    origin = tmp_path / "origin.git"
    assert (
        _run(upstream, "clone", "--bare", str(upstream), str(origin)).returncode == 0
    )
    root = _clone_for_agent(origin, tmp_path / "agent-a", branch="feature")
    sibling = _clone_for_agent(origin, tmp_path / "agent-b", branch=None)
    _commit(sibling, "mainline.txt", "mainline\n", "mainline work")
    assert _run(sibling, "push", "origin", _TARGET).returncode == 0
    # Agent A's local 'main' is now genuinely stale.
    _commit(root, "feature.txt", "feature\n", "feature work")
    return _Topology(root=root, origin=origin, sibling=sibling)


@pytest.fixture(params=["linked-worktree", "clone"])
def topology(request: pytest.FixtureRequest, tmp_path: Path) -> _Topology:
    """A diverged two-agent repository in each supported layout."""
    if request.param == "linked-worktree":
        return _linked_worktree_topology(tmp_path)
    return _clone_topology(tmp_path)


@pytest.fixture(autouse=True)
def _isolated_boundary_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test its own dirty-boundary refresh budget.

    The production throttle is a process-wide singleton, so one test's
    permitted refresh would otherwise suppress the next test's.
    """
    import ralph.pipeline.auto_integrate as auto_integrate_module

    monkeypatch.setattr(
        auto_integrate_module,
        "BOUNDARY_REFRESH_THROTTLE",
        BoundaryRefreshThrottle(),
    )


def _build_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_fetch_enabled": True,
                "auto_integrate_fetch_timeout_seconds": 10.0,
            }
        }
    )


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _assert_landed(topology: _Topology) -> None:
    """The catch-up ran: both agents' commits are present and the ref landed."""
    root = topology.root
    head = get_head_sha(root)
    assert branch_sha(root, _TARGET) == head, "mainline ref did not land"
    assert (root / "feature.txt").exists(), "this agent's commit was lost"
    assert (root / "mainline.txt").exists(), "the sibling's commit was not caught up"


def test_after_commit_seam_rebases_and_lands(topology: _Topology) -> None:
    """AC-02: the post-commit seam integrates in both topologies."""
    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(topology.root),
        RebaseState(),
        conflict_resolver=None,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    _assert_landed(topology)


def test_phase_transition_seam_rebases_and_lands(topology: _Topology) -> None:
    """AC-02: the boundary seam integrates a CLEAN worktree in both topologies."""
    outcome = auto_integrate_on_phase_transition(
        _build_config(),
        WorkspaceScope(topology.root),
        RebaseState(),
        conflict_resolver=None,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    _assert_landed(topology)


def test_startup_seam_rebases_and_lands(topology: _Topology) -> None:
    """AC-02: a run started on a stale branch begins from the target tip."""
    ctx = MagicMock()
    ctx.config = _build_config()
    ctx.workspace_scope = WorkspaceScope(topology.root)
    ctx.pipeline_deps = None

    outcome = run_loop_module._run_startup_integration(ctx)

    assert outcome is not None
    assert outcome.fast_forwarded is True
    _assert_landed(topology)


def test_fan_out_join_seam_rebases_and_lands(topology: _Topology) -> None:
    """AC-02: the fan-out join integrates against a REAL repository."""
    config = _build_config()
    policy_bundle = _default_policy_bundle()
    state = PipelineState.from_policy(policy_bundle.pipeline)

    joined = runner_module._integrate_after_fan_out(
        state=state,
        config=config,
        workspace_scope=WorkspaceScope(topology.root),
        display=MagicMock(),
        policy_bundle=policy_bundle,
        registry=AgentRegistry.from_config(config),
        pipeline_deps=None,
        display_context=None,
    )

    assert joined.rebase.fast_forwarded is True
    _assert_landed(topology)


def test_linked_worktree_refresh_reports_the_local_fleet_outcome(
    tmp_path: Path,
) -> None:
    """The shared-ref layout has no origin, but it DOES have a fresh pointer.

    ``refs/heads/main`` lives in the git common directory and is shared
    across every linked worktree, so the local ref IS the authoritative
    pointer and re-reading it observes whatever a sibling agent landed a
    moment ago.

    This used to record ``REFRESH_NO_ORIGIN``, which conflated "there is
    nothing to fetch" with "the pointer could not be observed" -- the
    same token an operator sees when freshness genuinely could not be
    established. ``REFRESH_LOCAL_FLEET`` says the pointer WAS observed,
    just not from a remote; ``REFRESH_NO_ORIGIN`` now means the stronger
    thing, and is covered by
    ``tests/test_auto_integrate_local_fleet_target_e2e.py``.
    """
    layout = _linked_worktree_topology(tmp_path)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(layout.root),
        RebaseState(),
        conflict_resolver=None,
    )

    assert outcome is not None
    assert outcome.last_refresh == REFRESH_LOCAL_FLEET
    _assert_landed(layout)


def test_clone_topology_picks_up_a_main_advanced_after_the_fixture_was_built(
    tmp_path: Path,
) -> None:
    """The user's 'ALWAYS get the latest main pointer' requirement.

    Every commit the sibling agent pushed to origin -- including one
    pushed AFTER this agent's checkout was already stale by one commit
    -- must be an ancestor of the feature tip after a single boundary
    integration. The pointer the integration reasons about therefore has
    to be the one origin holds at that moment, not the one this clone
    happened to fetch earlier.
    """
    layout = _clone_topology(tmp_path)
    config = _build_config()
    first_mainline_sha = _run(
        layout.sibling, "rev-parse", "HEAD"
    ).stdout.strip()

    # A second mainline commit lands on origin while this agent is idle,
    # so the local 'main' ref is now two commits behind.
    late_sha = _commit(layout.sibling, "late.txt", "late\n", "late mainline work")
    assert _run(layout.sibling, "push", "origin", _TARGET).returncode == 0
    assert branch_sha(layout.root, _TARGET) != late_sha, (
        "fixture precondition: the local target ref must still be stale"
    )

    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(layout.root), RebaseState(), conflict_resolver=None
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    head = get_head_sha(layout.root)
    for sha, label in ((first_mainline_sha, "first"), (late_sha, "late")):
        assert is_ancestor(layout.root, sha, head), (
            f"the {label} mainline commit on origin was not picked up"
        )
    assert branch_sha(layout.root, _TARGET) == head
    assert (layout.root / "late.txt").exists()
    assert (layout.root / "feature.txt").exists()
