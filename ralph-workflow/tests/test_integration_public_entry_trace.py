"""Public-entry trace: every integration mutation traverses the executor.

The integration boundary is entered through
:func:`ralph.pipeline.auto_integrate.auto_integrate_after_commit`. This
module drives that PUBLIC entry with doubles for the mutation seams
(:class:`~ralph.pipeline.integration_actions.GitOps` and
:class:`~ralph.pipeline.integration_actions.AgentRunner`) and no Git, no
network, no subprocess and no sleep at all.

The doubles record the mutations they are asked to perform, so the trace
is the ORDER of the attempt. The assertions pin three things:

* **No bypass.** Every mutation the attempt performs appears in the
  recorded trace, and the production helpers a bypass would call are
  wired to raise, so a mutation outside the executor fails the test
  instead of passing silently.
* **Rebase first.** The replay is the first mutation; the resolver is
  dispatched only AFTER a replay that actually conflicted.
* **No agent on an environmental failure.** A replay that cannot even
  run (an unreadable repository, a locked ref) must not buy an agent
  dispatch: the failure is environmental, not a conflict.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import RebaseSuccess
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.integration_actions import (
    AgentRunner,
    GitOps,
    current_agent_runner,
    current_git_ops,
    install_executors,
    reset_executors,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_TARGET = "main"
_FEATURE_SHA = "feature-sha"
_TARGET_SHA = "target-sha"


class _TraceRecorder:
    """The double that plays BOTH injected executor roles.

    It satisfies the :class:`GitOps` and :class:`AgentRunner` protocols,
    records every mutation it is asked to perform in :attr:`mutations`,
    and records every resolver dispatch in :attr:`dispatches`. It never
    touches a repository.
    """

    def __init__(self, *, rebase_result: RebaseResult | None = None) -> None:
        """Bind the recorder to the answer the fake replay reports."""
        self.mutations: list[str] = []
        self.dispatches: list[str] = []
        self._rebase_result: RebaseResult = (
            rebase_result if rebase_result is not None else RebaseSuccess()
        )
        self.landed = True

    # --- GitOps -------------------------------------------------------------

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        """Record the replay and report the configured outcome."""
        self.mutations.append("rebase_onto")
        return self._rebase_result

    def abort_rebase(self, root: Path) -> None:
        """Record the abort."""
        self.mutations.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        """Record the continuation."""
        self.mutations.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        """Record the merge abort."""
        self.mutations.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        """Record the merge continuation."""
        self.mutations.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        """Record the cherry-pick abort."""
        self.mutations.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        """Record the cherry-pick continuation."""
        self.mutations.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        """Record the staging."""
        self.mutations.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        """Record the commit."""
        self.mutations.append("commit")
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
        """Record the landing and report the configured answer."""
        self.mutations.append("fast_forward_target")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        """Record the landing and report the configured answer."""
        self.mutations.append("land_feature")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        """Record the backup-ref creation and report the ref name."""
        self.mutations.append("create_backup_ref")
        return "refs/rebase-backup/trace"

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        """Record the backup-ref deletion."""
        self.mutations.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        """Record the HEAD reattachment."""
        self.mutations.append("reattach_head")
        return True

    # --- AgentRunner --------------------------------------------------------

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        """Record the resolver dispatch and report success."""
        self.dispatches.append("resolve_rebase")
        return True


def _forbid_production_mutation(*_args: object, **_kwargs: object) -> object:
    """Fail the test when a production mutation runs outside the executor."""
    raise AssertionError("a mutation bypassed the injected executor")


def _stub_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every READ of the repository with a fixed answer.

    The seam reads the branch, the target, the durable record and the
    conflict identity before it mutates anything. Those reads are not
    the subject of this test -- the MUTATIONS are -- so they are
    answered from fixed values and no repository is needed.
    """
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda *_args, **_kwargs: _TARGET,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._current_branch_or_detached_marker",
        lambda *_args, **_kwargs: "feature",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._refresh_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.branch_sha",
        lambda *_args, **_kwargs: _TARGET_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.get_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.check_rebase_preconditions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.observe_conflict_identity",
        lambda *_args, **_kwargs: ConflictIdentity(
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._recover_before_attempt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.contained_land",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.ABSENT,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._clear_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )


def _stub_replay_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the replay driver's topology probes and obstruction cleanup.

    The topology probe is a READ; the stub answers "unknown" so the
    driver falls through to the replay instead of routing to the
    endpoint merge. Answering it here also keeps the suite free of a
    real subprocess.
    """
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.run_git",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="unavailable",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )


def _forbid_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the production mutation helpers to fail the test when called.

    A mutation that bypasses the executor is the one failure this suite
    exists to catch: it is invisible to the recorder, so the trace would
    look complete while the repository was mutated behind the seam.
    """
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_backup_refs.create_rebase_backup_ref",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_backup_refs.delete_rebase_backup_ref",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_ff.fast_forward_target",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.rebase_onto",
        _forbid_production_mutation,
    )


def _build_config() -> UnifiedConfig:
    """Build the configuration the seam reads."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": False,
            },
        },
    )


def test_the_public_entry_routes_every_mutation_through_the_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempt's whole mutation order is visible in the recorded trace."""
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.mutations == [
        "create_backup_ref",
        "rebase_onto",
        "fast_forward_target",
        "delete_backup_ref",
    ]
    assert recorder.dispatches == []
    assert outcome.fast_forwarded is True


def test_the_executor_scope_does_not_leak_past_the_public_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed executors are restored when the entry returns."""
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert current_git_ops() is not recorder
    assert current_agent_runner() is not recorder


def test_an_environmental_failure_buys_no_agent_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replay that cannot run is environmental: no resolver is dispatched."""
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    def _unreadable_repository(root: Path, target: str) -> RebaseResult:
        raise OSError("the repository could not be read")

    monkeypatch.setattr(recorder, "rebase_onto", _unreadable_repository)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.dispatches == []
    assert "abort_rebase" not in recorder.mutations
    assert "fast_forward_target" not in recorder.mutations


def test_a_landing_that_lost_the_race_keeps_the_backup_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fast-forward that did not land leaves the protective ref in place."""
    recorder = _TraceRecorder()
    recorder.landed = False
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.mutations == [
        "create_backup_ref",
        "rebase_onto",
        "fast_forward_target",
    ]
    assert outcome.fast_forwarded is False


def test_the_ambient_executor_scope_reaches_the_public_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that owns one step can install the executors ambiently."""
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    token = install_executors(recorder, recorder)
    try:
        auto_integrate_after_commit(
            _build_config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
    finally:
        reset_executors(token)

    assert "rebase_onto" in recorder.mutations


def test_the_injected_double_satisfies_both_executor_protocols() -> None:
    """The double really is a Git-ops and an AgentRunner."""
    recorder = _TraceRecorder()
    assert isinstance(recorder, GitOps)
    assert isinstance(recorder, AgentRunner)
