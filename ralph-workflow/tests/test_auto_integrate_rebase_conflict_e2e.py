"""End-to-end proof that a CONFLICTED REBASE is resolved in place (AC-01, AC-06).

Before this landed, a rebase that stopped on a conflict was never
resolved as a rebase. ``_fallback_to_endpoint_merge`` aborted it on the
first stop and retried the whole thing as a single endpoint three-way
merge, so the rebase-conflict-resolution pipeline -- status bar, prompt,
declare_complete contract and all -- was only ever reachable for that
follow-up merge. The operator watching auto-rebase fail on a conflict was
watching a code path that could not, by construction, succeed.

Every assertion here is on OBSERVABLE GIT STATE (branch SHAs, the absence
of ``.git/rebase-merge``, file contents, the commit graph) and on the
returned :class:`RebaseState`, never on internal call counts.

The resolver is a deterministic STUB that writes merged content: no
agent, no MCP session, no model. That is deliberate -- these tests must
prove the PLUMBING (does the loop reach the resolver, stage its work,
continue the rebase, repeat, and land?) and plumbing is exactly what a
model in the loop would make non-deterministic.

File-level markers. ``subprocess_e2e`` keeps this file out of ``make
test``'s budget-tracked 60 s step, because every test drives real git
through :func:`auto_integrate_after_commit`. ``timeout_seconds(30)``
sizes the budget for a two-stop rebase plus its fast-forward; no shared
suite cap is raised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_RESOLVED_MARKER = "resolved by the stub resolver\n"

_BYSTANDER_CONTENT = "nobody asked for this file to change\n"


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


def _base_branch(tmp_git_repo: Path) -> str:
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
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _one_conflicting_commit(tmp_git_repo: Path) -> str:
    """feature and base each edit ``shared.txt``; replaying feature conflicts."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature edit")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version\n", "base edit")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def _one_conflicting_commit_with_a_bystander(tmp_git_repo: Path) -> str:
    """As :func:`_one_conflicting_commit`, plus a tracked file nobody touches.

    ``bystander.txt`` is identical on both branches and is not part of
    the conflict, so it is exactly the kind of path a resolver is
    forbidden to edit.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "bystander.txt", _BYSTANDER_CONTENT, "seed bystander")
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature edit")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version\n", "base edit")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def _two_conflicting_commits(tmp_git_repo: Path) -> str:
    """Two feature commits that BOTH conflict, so the replay stops twice."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature one\n", "feature edit one")
    _commit(tmp_git_repo, "shared.txt", "feature two\n", "feature edit two")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version\n", "base edit")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def _stub_resolver(seen: list[RebaseStop]):
    """A resolver that edits files and NOTHING else, as the contract requires.

    It never stages, never commits and never runs git: proving the loop
    works with a resolver this restricted is proving that Ralph -- not the
    agent -- does the staging and the continuation.
    """

    def _resolve(root: Path, _target: str, stop: RebaseStop) -> bool:
        seen.append(stop)
        for relative in stop.conflicted_files:
            (root / relative).write_text(_RESOLVED_MARKER, encoding="utf-8")
        return True

    return _resolve


def _capture_warnings() -> tuple[list[str], int]:
    """Attach a WARNING-level loguru sink; return its buffer and sink id."""
    captured: list[str] = []
    sink_id = logger.add(
        lambda message: captured.append(str(message)),
        level="WARNING",
        format="{message}",
    )
    return captured, sink_id


def _rebase_dirs(tmp_git_repo: Path) -> list[Path]:
    git_dir = tmp_git_repo / ".git"
    return [
        path
        for path in (git_dir / "rebase-merge", git_dir / "rebase-apply")
        if path.exists()
    ]


def test_a_conflicted_rebase_is_resolved_and_reported_as_rebased(
    tmp_git_repo: Path,
) -> None:
    """The headline: the integration lands as ``rebased``, not ``conflict``."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    assert outcome is not None
    assert outcome.last_action == "rebased"


def test_the_rebase_really_continued_and_left_no_rebase_state(
    tmp_git_repo: Path,
) -> None:
    """``git rebase --continue`` ran: no rebase directory survives."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    assert _rebase_dirs(tmp_git_repo) == []


def test_the_resolved_content_is_what_landed_on_the_feature_tip(
    tmp_git_repo: Path,
) -> None:
    """The replayed commit carries the RESOLVER's content, not either side's."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    landed = _run(tmp_git_repo, "show", "HEAD:shared.txt").stdout
    assert landed == _RESOLVED_MARKER


def test_the_target_is_fast_forwarded_to_the_resolved_feature_tip(
    tmp_git_repo: Path,
) -> None:
    """AC-04's other half: the mainline ref actually moves."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, base) == head


def test_the_replay_is_linear_history_not_a_merge_commit(
    tmp_git_repo: Path,
) -> None:
    """A rebase that landed as a rebase leaves no merge commit behind."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    merges = _run(tmp_git_repo, "log", "--merges", "--oneline").stdout.strip()
    assert merges == ""


def test_the_resolver_is_told_which_commit_is_being_replayed(
    tmp_git_repo: Path,
) -> None:
    """The rebase-scoped context that a merge conflict cannot supply."""
    base = _one_conflicting_commit(tmp_git_repo)
    seen: list[RebaseStop] = []

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    assert [stop.subject for stop in seen] == ["feature edit"]


def test_two_conflicting_commits_drive_two_stops_and_still_land(
    tmp_git_repo: Path,
) -> None:
    """The LOOP, not a single resolution: each replayed commit gets a stop."""
    base = _two_conflicting_commits(tmp_git_repo)
    seen: list[RebaseStop] = []

    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_stub_resolver(seen),
    )

    assert [stop.stop_index for stop in seen] == [1, 2]
    assert outcome is not None
    assert outcome.last_action == "rebased"
    assert outcome.fast_forwarded is True


def test_a_declining_resolver_leaves_the_feature_branch_bit_identical(
    tmp_git_repo: Path,
) -> None:
    """AC-06 fail-safe: declining must cost nothing, not corrupt the branch."""
    base = _one_conflicting_commit(tmp_git_repo)
    feature_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=lambda _root, _target, _stop: False,
    )

    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_before
    assert _rebase_dirs(tmp_git_repo) == []


def test_with_no_resolver_at_all_the_rebase_is_still_aborted(
    tmp_git_repo: Path,
) -> None:
    """The pre-existing contract survives untouched when nothing is injected."""
    base = _one_conflicting_commit(tmp_git_repo)
    feature_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    outcome = auto_integrate_after_commit(
        _build_config(base), WorkspaceScope(tmp_git_repo), RebaseState()
    )

    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_before
    assert _rebase_dirs(tmp_git_repo) == []
    assert outcome is not None
    assert outcome.last_action == "conflict"


def test_a_resolver_editing_an_unrequested_path_is_refused(
    tmp_git_repo: Path,
) -> None:
    """The prompt forbids editing unlisted paths; this is the enforcement.

    ``_stage_and_prove`` stages only ``stop.conflicted_files``, so an
    unrelated edit is never replayed into the commit. Left unchecked it
    reaches ``git rebase --continue``, which refuses it with the
    thoroughly misleading "You must edit all merge conflicts" -- the one
    diagnostic guaranteed to send an operator looking at the file that
    was resolved correctly. The loop therefore names the real cause
    itself, before staging anything.

    The warning sink is the discriminating assertion: the outcome below
    is what git's own refusal produces too, so only the named path proves
    that Ralph, not git, rejected this.
    """
    base = _one_conflicting_commit_with_a_bystander(tmp_git_repo)
    feature_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    def _overreaching_resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        for relative in stop.conflicted_files:
            (root / relative).write_text(_RESOLVED_MARKER, encoding="utf-8")
        (root / "bystander.txt").write_text("tidied up too\n", encoding="utf-8")
        return True

    captured, sink_id = _capture_warnings()
    try:
        outcome = auto_integrate_after_commit(
            _build_config(base),
            WorkspaceScope(tmp_git_repo),
            RebaseState(),
            rebase_stop_resolver=_overreaching_resolver,
        )
    finally:
        logger.remove(sink_id)

    assert outcome is not None
    assert outcome.last_action != "rebased"
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_before
    assert _rebase_dirs(tmp_git_repo) == []
    # The whole point of failing closed: the unrequested edit does not
    # survive the abort, in the worktree or in the commit graph.
    assert (tmp_git_repo / "bystander.txt").read_text(
        encoding="utf-8"
    ) == _BYSTANDER_CONTENT
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    rejections = [line for line in captured if "unrequested path" in line]
    assert rejections, captured
    assert "bystander.txt" in rejections[0]


def test_a_resolver_leaving_conflict_markers_is_refused(
    tmp_git_repo: Path,
) -> None:
    """Ralph's own scan is the judge; a false success claim cannot land."""
    base = _one_conflicting_commit(tmp_git_repo)
    feature_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    def _lying_resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        for relative in stop.conflicted_files:
            (root / relative).write_text(
                "<<<<<<< HEAD\nfeature\n=======\nbase\n>>>>>>> other\n",
                encoding="utf-8",
            )
        return True

    auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        rebase_stop_resolver=_lying_resolver,
    )

    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_before
    assert _rebase_dirs(tmp_git_repo) == []
