from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase import RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.pipeline.auto_integrate_rebase_merge import run_rebase_or_merge
from ralph.pipeline.integration_actions import (
    GitOps,
    create_backup_ref,
    current_agent_runner,
    current_git_ops,
    default_agent_runner,
    default_git_ops,
    delete_backup_ref,
    fast_forward_target,
    install_executors,
    reset_executors,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_TARGET = "main"
_OWNER = "/owner/worktree"


class _RecordingGitOps:
    def __init__(self, *, rebase_result: RebaseResult | None = None) -> None:
        self.calls: list[str] = []
        self._rebase_result: RebaseResult = (
            rebase_result if rebase_result is not None else RebaseSuccess()
        )

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.calls.append("rebase_onto")
        return self._rebase_result

    def abort_rebase(self, root: Path) -> None:
        self.calls.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        self.calls.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        self.calls.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        self.calls.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        self.calls.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        self.calls.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        self.calls.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        self.calls.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.calls.append("fast_forward_target")
        return True, ""

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.calls.append("land_feature")
        return True, ""

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        self.calls.append("create_backup_ref")
        return "refs/rebase-backup/fake"

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        self.calls.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        self.calls.append("reattach_head")
        return True


class _RecordingAgentRunner:
    def __init__(self, *, resolved: bool = True) -> None:
        self.calls: list[str] = []
        self._resolved = resolved

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        self.calls.append("resolve_rebase")
        return self._resolved


def _failing_git_probe(*_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr="unavailable")


def _stub_git_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.run_git",
        _failing_git_probe,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )


def test_the_production_and_the_fake_adapter_satisfy_the_git_ops_protocol() -> None:
    assert isinstance(default_git_ops(), GitOps)
    assert isinstance(_RecordingGitOps(), GitOps)


def test_the_installed_executors_are_the_ones_the_seams_resolve() -> None:
    fake_git_ops = _RecordingGitOps()
    fake_runner = _RecordingAgentRunner()

    token = install_executors(fake_git_ops, fake_runner)
    try:
        assert current_git_ops() is fake_git_ops
        assert current_agent_runner() is fake_runner
    finally:
        reset_executors(token)

    assert current_git_ops() is default_git_ops()
    assert current_agent_runner() is default_agent_runner()


def test_a_nested_scope_inherits_the_installed_executor() -> None:
    fake_git_ops = _RecordingGitOps()
    fake_runner = _RecordingAgentRunner()

    outer = install_executors(fake_git_ops, fake_runner)
    try:
        inner = install_executors()
        try:
            assert current_git_ops() is fake_git_ops
            assert current_agent_runner() is fake_runner
        finally:
            reset_executors(inner)
        assert current_git_ops() is fake_git_ops
    finally:
        reset_executors(outer)


def test_the_mutation_seams_route_to_the_installed_executor() -> None:
    fake_git_ops = _RecordingGitOps()
    root = Path("/repo")

    token = install_executors(fake_git_ops)
    try:
        assert create_backup_ref(root, "feature-sha", _OWNER) == "refs/rebase-backup/fake"
        assert fast_forward_target(root, _TARGET, "feature-sha") == (True, "")
        delete_backup_ref(root, "refs/rebase-backup/fake")
    finally:
        reset_executors(token)

    assert fake_git_ops.calls == [
        "create_backup_ref",
        "fast_forward_target",
        "delete_backup_ref",
    ]


def test_the_rebase_runs_before_any_resolver_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git_ops = _RecordingGitOps()
    fake_runner = _RecordingAgentRunner()
    _stub_git_probes(monkeypatch)

    result = run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        git_ops=fake_git_ops,
        agent_runner=fake_runner,
    )

    assert result.short_circuit is None
    assert fake_git_ops.calls == ["rebase_onto"]
    assert fake_runner.calls == []


def test_a_conflicted_rebase_dispatches_the_resolver_through_the_injected_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git_ops = _RecordingGitOps(rebase_result=RebaseConflicts(files=["conflict.txt"]))
    fake_runner = _RecordingAgentRunner(resolved=True)
    _stub_git_probes(monkeypatch)

    result = run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=lambda _root, _target, _stop: True,
        git_ops=fake_git_ops,
        agent_runner=fake_runner,
    )

    assert result.short_circuit is None
    assert fake_git_ops.calls == ["rebase_onto"]
    assert fake_runner.calls == ["resolve_rebase"]


def test_a_declined_resolution_aborts_through_the_injected_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git_ops = _RecordingGitOps(rebase_result=RebaseConflicts(files=["conflict.txt"]))
    fake_runner = _RecordingAgentRunner(resolved=False)
    _stub_git_probes(monkeypatch)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.merge_state",
        lambda *_args, **_kwargs: "none",
    )

    run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        git_ops=fake_git_ops,
        agent_runner=fake_runner,
    )

    assert fake_git_ops.calls[0] == "rebase_onto"
    assert "abort_rebase" in fake_git_ops.calls
