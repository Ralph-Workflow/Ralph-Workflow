"""Real-git proof that nothing in the worktree can strand a branch behind its target.

Regression 2026-10-05: 22 untracked files that ``main`` also tracks made
``git merge main`` refuse to start (exit 2). No merge ran, so no resolver
could act, and planning began 40 commits behind ``main``. Every refusal
shape is driven through real git here: identical leftovers are cleared,
differing local work remains untouched, and safe integrations land the
committed feature tip on the target.

Registered in ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` so ``make verify``
runs it even though it crosses the real git boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

from ralph.git.merge import merge_in_progress, merge_target_into_current

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _behind_with_untracked_copy(tmp_git_repo: Path, content: str) -> str:
    """Feature branch behind ``base``, holding an untracked ``incoming.txt``."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature.txt", "feature body\n", "feature work")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "incoming.txt", "mainline body\n", "mainline adds incoming")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "incoming.txt").write_text(content, encoding="utf-8")
    return base


def test_merge_lands_over_untracked_copy_identical_to_target(tmp_git_repo: Path) -> None:
    """Regression 2026-10-05: an identical untracked leftover no longer blocks the merge."""
    base = _behind_with_untracked_copy(tmp_git_repo, "mainline body\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "success"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_merge_refusal_over_differing_untracked_file_keeps_it_and_names_it(
    tmp_git_repo: Path,
) -> None:
    """Local untracked work is never deleted; the refusal names the path."""
    base = _behind_with_untracked_copy(tmp_git_repo, "local work\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "conflict"
    assert result.reason == "merge refused: untracked files would be overwritten: incoming.txt"
    assert (tmp_git_repo / "incoming.txt").read_text(encoding="utf-8") == "local work\n"
    assert merge_in_progress(tmp_git_repo) is False


@pytest.mark.parametrize("target_advanced", [False, True])
def test_planning_lands_commits_without_committing_dirty_edits(
    tmp_git_repo: Path, target_advanced: bool
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_planning import integrate_before_planning
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "tracked.txt", "base\n", "seed tracked")
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    _commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature work")
    if target_advanced:
        assert _run(tmp_git_repo, "checkout", base).returncode == 0
        _commit_file(tmp_git_repo, "incoming.txt", "mainline\n", "mainline work")
        assert _run(tmp_git_repo, "checkout", "feature").returncode == 0
    (tmp_git_repo / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    (tmp_git_repo / "loose.txt").write_text("untracked\n", encoding="utf-8")

    outcome = integrate_before_planning(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert outcome is not None and outcome.fast_forwarded
    assert _run(tmp_git_repo, "rev-parse", base).stdout == _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "feature\n"
    assert _run(tmp_git_repo, "show", f"{base}:tracked.txt").stdout == "base\n"
    assert (tmp_git_repo / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert (tmp_git_repo / "loose.txt").read_text(encoding="utf-8") == "untracked\n"


@pytest.mark.parametrize("resolve_rebase", [False, True])
def test_verified_resolution_using_target_version_lands_and_synchronizes(
    tmp_git_repo: Path, resolve_rebase: bool,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "shared.txt", "original\n", "seed")
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    _commit_file(tmp_git_repo, "shared.txt", "feature work\n", "feature work")
    _commit_file(tmp_git_repo, "feature.txt", "nonconflicting feature\n", "additional feature")
    assert _run(tmp_git_repo, "checkout", base).returncode == 0
    _commit_file(tmp_git_repo, "shared.txt", "mainline\n", "mainline")
    assert _run(tmp_git_repo, "checkout", "feature").returncode == 0

    def discard_feature(root: Path, _target: str) -> bool:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return True

    def choose_target_at_stop(root: Path, _target: str, _stop: RebaseStop) -> bool:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return True

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=discard_feature,
        rebase_stop_resolver=choose_target_at_stop if resolve_rebase else None,
    )

    assert outcome is not None and outcome.fast_forwarded
    if resolve_rebase:
        assert outcome.last_action == "rebased"
    assert _run(tmp_git_repo, "rev-parse", base).stdout == _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    assert _run(tmp_git_repo, "show", f"{base}:shared.txt").stdout == "mainline\n"
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "nonconflicting feature\n"
    from ralph.git.rebase.rebase import rebase_in_progress

    assert not rebase_in_progress(tmp_git_repo)
    assert not merge_in_progress(tmp_git_repo)


def test_integration_transactions_exclude_overlapping_worktree_mutations(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_transaction import integration_transaction

    with integration_transaction(tmp_git_repo) as first:
        assert first
        with integration_transaction(tmp_git_repo) as second:
            assert not second
    with integration_transaction(tmp_git_repo) as later:
        assert later


def test_interrupted_integration_recovery_keeps_unfinished_tracked_work(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    sha = _commit_file(tmp_git_repo, "tracked.txt", "committed\n", "seed tracked")
    (tmp_git_repo / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    write_record(tmp_git_repo, IntegrationRecord(
        phase="integrating", target=base, pre_feature_sha=sha, pre_target_sha=sha,
    ))

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None and outcome.last_action == "recovered"
    assert (tmp_git_repo / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert _run(tmp_git_repo, "show", "HEAD:tracked.txt").stdout == "committed\n"


def test_unexpected_integration_failure_retains_recovery_ownership(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    feature = _commit_file(tmp_git_repo, "feature.txt", "keep me\n", "feature")

    def fail_after_record(*args: object, **kwargs: object) -> None:
        raise TimeoutError("integration interrupted")

    monkeypatch.setattr(integration, "_run_rebase_or_merge", fail_after_record)
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_target": base, "auto_integrate_remote_enabled": False,
    }})
    outcome = integration.auto_integrate_after_commit(
        config, WorkspaceScope(tmp_git_repo), RebaseState(),
    )
    assert outcome is not None and outcome.recovery_record_retained
    record = read_record(tmp_git_repo)
    assert record is not None and record.pre_feature_sha == feature
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature
    assert (tmp_git_repo / "feature.txt").read_text(encoding="utf-8") == "keep me\n"


def test_failed_landing_recovers_and_fast_forwards_on_next_seam(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    feature = _commit_file(tmp_git_repo, "feature.txt", "keep me\n", "feature")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}})
    scope = WorkspaceScope(tmp_git_repo)
    with monkeypatch.context() as failure:
        failure.setattr(integration, "_fast_forward_target", lambda *a, **kw: (False, "ref locked"))
        outcome = integration.auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(tmp_git_repo) is not None
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature
    recovered = integration.auto_integrate_after_commit(config, scope, outcome)
    assert recovered is not None and recovered.fast_forwarded
    assert read_record(tmp_git_repo) is None
    assert _run(tmp_git_repo, "rev-parse", base).stdout.strip() == feature
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "keep me\n"
