"""Two linked worktrees, one shared git dir: the conflicted path, end to end.

This is the topology the feature exists for and the one the prompt
describes: several Ralph agents on one repository, each in its own
``git worktree``, all sharing a single git common directory and
therefore a single ``refs/heads/<target>``. There is no ``origin``.
Agent B lands on the mainline while agent A is mid-flight, and agent A's
feature branch conflicts on replay.

Nothing proved that end to end before. ``test_auto_integrate_rebase_
conflict_e2e.py`` proves in-place rebase resolution in ONE checkout, and
``test_auto_integrate_clone_conflict_e2e.py`` proves the clone topology
with a remote. Neither creates the situation where the ref another agent
just moved is the very ref this agent is landing on, in a checkout that
is not the one holding the target.

Every assertion is on OBSERVABLE GIT STATE read back from git itself --
rebase-in-progress directories, parent counts, ancestry, ref equality,
file content -- so there is no timing dependence and no assertion on
internal call counts.

The rebase-stop resolver is a deterministic STUB that edits only the
conflicted paths and never runs git, exactly as the real agent is
constrained to (the MCP exec policy denies it every vcs invocation and
Ralph alone stages and continues). The agent-driven path itself is
covered by ``tests/test_conflict_resolution_pipeline.py``; what is
proved here is that the plumbing carries a conflicted rebase across a
fleet and lands it.

File-level markers. ``subprocess_e2e`` keeps this file out of ``make
test``'s budget-tracked 60 s step -- it drives real git, including
``git worktree add``. It is wired into the ``test-auto-integrate-e2e``
Makefile target, whose verify step is deliberately absent from
``ralph/verify.py:_BUDGET_TRACKED_STEPS``, so it costs none of the
immutable combined budget. ``timeout_seconds(30)`` sizes one test for a
worktree creation plus a conflicted rebase and its fast-forward.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.conflict_resolution.graph import MAX_REBASE_CONFLICT_STOPS
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_SHARED_FILE = "shared.txt"
#: The resolver writes content carrying BOTH sides' intent, so the
#: assertion can prove a real reconciliation rather than one side winning.
_RESOLVED_CONTENT = "agent-a intent + agent-b intent\n"
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _run(
    repo_root: Path, *args: str, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(repo_root: Path) -> str:
    out = _run(repo_root, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(target: str) -> UnifiedConfig:
    """Fleet config: an explicit target and no remote to fetch from."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


class _Fleet:
    """A primary checkout on the mainline plus one linked feature worktree."""

    def __init__(
        self, *, agent_b: Path, agent_a: Path, target: str, agent_b_sha: str
    ) -> None:
        #: Agent B's checkout: the primary worktree, sitting on the target.
        self.agent_b = agent_b
        #: Agent A's checkout: a LINKED worktree on the feature branch.
        self.agent_a = agent_a
        self.target = target
        #: The commit agent B landed on the shared mainline ref.
        self.agent_b_sha = agent_b_sha


def _build_conflicting_fleet(tmp_git_repo: Path, tmp_path: Path) -> _Fleet:
    """Build the two-worktree fleet whose replay is guaranteed to conflict.

    Deliberately minimal -- one file, three commits, no remote -- so the
    scenario stays fast and has nothing to be flaky about.
    """
    target = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, _SHARED_FILE, "seed\n", "seed the shared file")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{target}").stdout.strip()

    # Agent A: its own linked worktree, sharing this git common directory,
    # forked from the seed so its edit lands on the SAME line as B's.
    agent_a = tmp_path / "agent-a"
    _run(tmp_git_repo, "worktree", "add", "-b", "feature", str(agent_a), seed)
    _commit(agent_a, _SHARED_FILE, "agent-a intent\n", "agent A edits shared")

    # Agent B lands on the shared mainline while A is mid-flight.
    agent_b_sha = _commit(
        tmp_git_repo, _SHARED_FILE, "agent-b intent\n", "agent B lands on mainline"
    )

    return _Fleet(
        agent_b=tmp_git_repo,
        agent_a=agent_a,
        target=target,
        agent_b_sha=agent_b_sha,
    )


def _resolving_stop_resolver(seen: list[RebaseStop]):
    """Resolve each stop by editing ONLY the conflicted paths.

    It never stages, never commits and never runs git -- the same
    constraints the real conflict-resolution agent runs under -- so a
    passing test proves Ralph does the staging and the continuation.
    """

    def _resolve(root: Path, _target: str, stop: RebaseStop) -> bool:
        seen.append(stop)
        for relative in stop.conflicted_files:
            (root / relative).write_text(_RESOLVED_CONTENT, encoding="utf-8")
        return True

    return _resolve


def _rebase_in_progress(worktree: Path) -> bool:
    """Whether git still has a rebase in flight for ``worktree``.

    Reads the per-worktree git dir rather than assuming ``.git`` is a
    directory: in a LINKED worktree ``.git`` is a file pointing at
    ``<common>/worktrees/<name>``, which is exactly where the
    ``rebase-merge`` / ``rebase-apply`` state lives.
    """
    git_dir_out = _run(worktree, "rev-parse", "--absolute-git-dir")
    git_dir = Path(git_dir_out.stdout.strip())
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _parent_count(worktree: Path, ref: str = "HEAD") -> int:
    """Number of parents of ``ref`` -- 2 would mean a merge commit."""
    out = _run(worktree, "rev-list", "--parents", "-n", "1", ref).stdout.split()
    return len(out) - 1


def test_a_conflicted_fleet_rebase_is_resolved_in_place_and_lands(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The headline claim, proved from git in the real fleet topology.

    Agent B landed on the shared ``refs/heads/<target>``; agent A's
    feature branch conflicts on replay; the stop is resolved in place;
    the rebase continues; and the shared mainline fast-forwards to A's
    resolved tip.
    """
    fleet = _build_conflicting_fleet(tmp_git_repo, tmp_path)
    seen: list[RebaseStop] = []

    outcome = auto_integrate_after_commit(
        _build_config(fleet.target),
        WorkspaceScope(fleet.agent_a),
        RebaseState(),
        rebase_stop_resolver=_resolving_stop_resolver(seen),
    )

    assert outcome is not None
    assert outcome.last_action == "rebased", (
        "a resolved rebase must land as a rebase, not fall back to a merge;"
        f" got last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    assert seen, "the conflicted stop must have reached the resolver"

    # The rebase really finished: no state left, nothing uncommitted.
    assert _rebase_in_progress(fleet.agent_a) is False
    assert (
        _run(fleet.agent_a, "status", "--porcelain").stdout.strip() == ""
    ), "agent A's worktree must be clean after a completed rebase"

    agent_a_tip = _run(fleet.agent_a, "rev-parse", "HEAD").stdout.strip()

    # Linear history: the replayed commit has ONE parent, so no merge
    # commit was created behind the rebase's back.
    assert _parent_count(fleet.agent_a) == 1
    assert _run(fleet.agent_a, "log", "--merges", "--oneline").stdout.strip() == ""

    # Agent B's landing was not lost -- it is now behind agent A's tip.
    assert (
        _run(
            fleet.agent_a,
            "merge-base",
            "--is-ancestor",
            fleet.agent_b_sha,
            agent_a_tip,
        ).returncode
        == 0
    ), "agent B's commit must be an ancestor of the rebased feature tip"

    # The SHARED ref advanced to agent A's tip: the fast-forward landed.
    assert branch_sha(fleet.agent_a, fleet.target) == agent_a_tip
    assert branch_sha(fleet.agent_b, fleet.target) == agent_a_tip, (
        "both worktrees read one shared refs/heads/<target>"
    )

    # The resolution itself: both sides' intent, zero markers.
    resolved = _run(fleet.agent_a, "show", f"HEAD:{_SHARED_FILE}").stdout
    assert resolved == _RESOLVED_CONTENT
    for marker in _CONFLICT_MARKERS:
        assert marker not in resolved, (
            f"a conflict marker {marker!r} survived into the landed content"
        )


def test_a_declining_resolver_leaves_both_refs_bit_for_bit_unchanged(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The fail-safe half: declining costs nothing and raises nothing.

    A resolver that cannot resolve must leave agent A's feature branch
    exactly where it was and must NOT move the mainline ref every other
    agent in the fleet is reading -- a partially-applied replay published
    to the shared ref store would corrupt the whole fleet, not just this
    agent.
    """
    fleet = _build_conflicting_fleet(tmp_git_repo, tmp_path)
    feature_before = _run(fleet.agent_a, "rev-parse", "HEAD").stdout.strip()
    target_before = branch_sha(fleet.agent_a, fleet.target)

    outcome = auto_integrate_after_commit(
        _build_config(fleet.target),
        WorkspaceScope(fleet.agent_a),
        RebaseState(),
        rebase_stop_resolver=lambda _root, _target, _stop: False,
    )

    assert outcome is not None, "a declined resolution records, it does not raise"
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False

    assert _rebase_in_progress(fleet.agent_a) is False, (
        "a declined resolution must abort the rebase cleanly"
    )
    assert (
        _run(fleet.agent_a, "rev-parse", "HEAD").stdout.strip() == feature_before
    ), "agent A's feature branch must be bit-for-bit unchanged"
    assert branch_sha(fleet.agent_a, fleet.target) == target_before, (
        "the shared mainline ref must not move when resolution declines"
    )


def _build_multi_commit_conflicting_fleet(
    tmp_git_repo: Path, tmp_path: Path
) -> _Fleet:
    """The same fleet, but agent A carries THREE commits to replay.

    Only the MIDDLE one touches the shared file, so exactly one stop is
    conflicted while the replay total is unambiguously three -- and
    unambiguously different from ``MAX_REBASE_CONFLICT_STOPS``.
    """
    target = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, _SHARED_FILE, "seed\n", "seed the shared file")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{target}").stdout.strip()

    agent_a = tmp_path / "agent-a-multi"
    _run(tmp_git_repo, "worktree", "add", "-b", "feature-multi", str(agent_a), seed)
    _commit(agent_a, "first.txt", "one\n", "agent A commit 1")
    _commit(agent_a, _SHARED_FILE, "agent-a intent\n", "agent A commit 2 (conflicts)")
    _commit(agent_a, "third.txt", "three\n", "agent A commit 3")

    agent_b_sha = _commit(
        tmp_git_repo, _SHARED_FILE, "agent-b intent\n", "agent B lands on mainline"
    )

    return _Fleet(
        agent_b=tmp_git_repo,
        agent_a=agent_a,
        target=target,
        agent_b_sha=agent_b_sha,
    )


def test_the_replay_counter_reports_the_real_commit_total_not_the_safety_cap(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The stop handed to the resolver must say where in the REPLAY it is.

    The regression: every stop used to be built with
    ``stop_cap=MAX_REBASE_CONFLICT_STOPS``, and the status bar rendered
    that directly, so the operator always read ``commit 1/10`` no matter
    how long the rebase was. Three commits replayed here, so a truthful
    counter says 3 and a broken one says 10.

    The safety bound is asserted alongside it, because the fix must add
    a display counter WITHOUT widening the bound that terminates the
    loop.
    """
    fleet = _build_multi_commit_conflicting_fleet(tmp_git_repo, tmp_path)
    seen: list[RebaseStop] = []

    outcome = auto_integrate_after_commit(
        _build_config(fleet.target),
        WorkspaceScope(fleet.agent_a),
        RebaseState(),
        rebase_stop_resolver=_resolving_stop_resolver(seen),
    )

    assert outcome is not None
    assert outcome.last_action == "rebased", (
        f"got last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert seen, "the conflicted stop must have reached the resolver"

    stop = seen[0]
    assert stop.replay_total == 3, (
        "the counter must report the commits actually being replayed, not "
        f"the {MAX_REBASE_CONFLICT_STOPS}-stop safety cap; got "
        f"{stop.replay_total!r}"
    )
    assert stop.replay_index is not None
    assert 1 <= stop.replay_index <= stop.replay_total
    assert stop.stop_cap == MAX_REBASE_CONFLICT_STOPS, (
        "the loop's termination bound must be untouched by the display counter"
    )

    # The landing itself, proved from git: replay finished, mainline
    # advanced, history stayed linear, worktree clean.
    assert _rebase_in_progress(fleet.agent_a) is False
    assert _run(fleet.agent_a, "status", "--porcelain").stdout.strip() == ""
    agent_a_tip = _run(fleet.agent_a, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(fleet.agent_a, fleet.target) == agent_a_tip
    assert _parent_count(fleet.agent_a) == 1
    assert _run(fleet.agent_a, "log", "--merges", "--oneline").stdout.strip() == ""
    assert (
        _run(fleet.agent_a, "show", f"HEAD~1:{_SHARED_FILE}").stdout
        == _RESOLVED_CONTENT
    ), "the resolved content must be carried by the replayed middle commit"
