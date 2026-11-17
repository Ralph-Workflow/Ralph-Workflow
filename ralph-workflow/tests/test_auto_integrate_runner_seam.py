from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    import pytest
from ralph.git.merge import WORKTREE_FOUND
from ralph.pipeline import (
    auto_integrate,
    auto_integrate_backoff,
    auto_integrate_catchup,
    auto_integrate_ff,
    runner,
)
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome
from ralph.workspace.scope import WorkspaceScope


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_remote_enabled": False}}
    )


def _stub_ff_environment(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(
        auto_integrate_ff,
        "observe_branch_sha",
        lambda _root, _branch: ("old-main", True),
    )
    monkeypatch.setattr(auto_integrate_ff, "is_ancestor", lambda *_args: True)
    monkeypatch.setattr(auto_integrate_ff, "find_main_worktree_root", lambda _root: root)
    monkeypatch.setattr(
        auto_integrate_ff,
        "worktree_lookup",
        lambda _root, _branch: (WORKTREE_FOUND, root),
    )


def test_default_config_resolves_main_and_lands_via_ff_only(monkeypatch) -> None:
    config = _default_config()
    root = Path("/workspace/feature")
    monkeypatch.setattr(
        auto_integrate_catchup,
        "observe_branch_sha",
        lambda _root, _target: ("old-main", True),
    )
    target = auto_integrate.resolve_integration_target(config, root)

    _stub_ff_environment(monkeypatch, root)
    worktree_ff = MagicMock(return_value=True)
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "fast_forward_via_worktree", worktree_ff)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert target == "main"
    assert isinstance(
        auto_integrate_ff.fast_forward_target(root, target, "feature-head"), ResolvedOutcome
    )
    assert worktree_ff.call_args.args == (root, "feature-head")
    cas.assert_not_called()


def test_refused_ff_only_leaves_ref_untouched_and_records_loud_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/workspace/feature")
    _stub_ff_environment(monkeypatch, root)
    monkeypatch.setattr(auto_integrate_ff, "fast_forward_via_worktree", lambda *_args: False)
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    outcome = auto_integrate_ff.fast_forward_target(root, "main", "feature-head")
    assert isinstance(outcome, RetryLaterOutcome), (
        "AC-10/E2: a refused merge --ff-only on a checked-out target "
        "MUST NOT advance the shared ref -- the previous CAS fallback "
        "silently desynced the worktree's index and working tree"
    )
    assert outcome.on_change == auto_integrate_ff._TARGET_CHECKED_OUT_REFUSED, (
        f"AC-08/AC-10: the refusal reason must name merge --ff-only and "
        f"the checked-out sibling worktree, got {outcome.on_change!r}"
    )
    cas.assert_not_called()


def test_commit_seam_invokes_auto_integrate(monkeypatch) -> None:
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_after_commit", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=CommitEffect(message_file="message"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=SimpleNamespace(role="commit"),
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target == "main"
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_pending_remote_publish_keeps_phase_boundary_resolver(monkeypatch) -> None:
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_remote_enabled": True}}
    )

    state = RebaseState(last_push_status="non_fast_forward")
    monkeypatch.setattr(boundary, "pull_and_reconcile_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(boundary, "branch_sha", lambda *_args: "head")
    monkeypatch.setattr(boundary, "get_head_sha", lambda *_args: "head")
    called: list[Path] = []
    monkeypatch.setattr(
        boundary,
        "_reconcile_remote_publication",
        lambda _config, root, _target, **_kwargs: called.append(root) or RebaseState(),
    )

    boundary.boundary_freshness_outcome(
        config, Path("/workspace"), "main", state, rebase_stop_resolver=lambda *_: True
    )

    assert called == [Path("/workspace")]


def test_phase_transition_seam_invokes_auto_integrate(monkeypatch) -> None:
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=object(),
        event=PipelineEvent.AGENT_SUCCESS,
        commit_phase_def=None,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target == "main"
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def _install_retry_loop(
    monkeypatch,
    root: Path,
    *,
    retries: list[bool],
    events: list[str] | None = None,
) -> None:
    verdicts = iter(retries)
    monkeypatch.setattr(auto_integrate, "read_record", lambda _root: None)
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (root, "feature", "main", "refreshed"),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_check_skip_conditions",
        lambda _root, _branch, _target: None,
    )
    monkeypatch.setattr(auto_integrate, "observe_conflict_identity", lambda _root, _target: "id")
    monkeypatch.setattr(
        auto_integrate,
        "resolver_allowed",
        lambda _state, _target, _identity, **_kwargs: True,
    )

    def _refresh(_config, _root, _target) -> str:
        if events is not None:
            events.append("refresh")
        return "refreshed"

    def _integrate_once(*_args: object, **_kwargs: object) -> tuple[RebaseState, bool]:
        if events is not None:
            events.append("integrate")
        record = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
        return record, next(verdicts, False)

    monkeypatch.setattr(auto_integrate, "_refresh_target", _refresh)
    monkeypatch.setattr(auto_integrate, "_integrate_once", _integrate_once)


def _recorder(monkeypatch, root: Path, *, retries: list[bool], events=None):
    delays: list[float] = []
    _install_retry_loop(monkeypatch, root, retries=retries, events=events)

    def _sleep(seconds: float) -> None:
        if events is not None:
            events.append("sleep")
        delays.append(seconds)

    auto_integrate.auto_integrate_after_commit(
        _default_config(),
        WorkspaceScope(root),
        RebaseState(),
        sleep=_sleep,
        jitter=lambda: 1.0,
    )
    return delays


def test_a_first_attempt_that_lands_never_waits(monkeypatch, tmp_path: Path) -> None:
    assert _recorder(monkeypatch, tmp_path, retries=[False]) == []


def test_a_lost_compare_and_swap_waits_once_before_retrying(monkeypatch, tmp_path: Path) -> None:
    delays = _recorder(monkeypatch, tmp_path, retries=[True, False])

    assert len(delays) == 1
    assert 0 < delays[0] <= auto_integrate_backoff.RETRY_MAX_DELAY_SECONDS
    assert delays[0] >= auto_integrate_backoff.RETRY_BASE_DELAY_SECONDS * 0.5


def test_the_delay_grows_across_attempts_and_stays_capped(monkeypatch, tmp_path: Path) -> None:
    delays = _recorder(monkeypatch, tmp_path, retries=[True, True, True])

    assert len(delays) == 2
    assert delays[1] > delays[0], "the backoff must grow, not repeat"
    assert all(d <= auto_integrate_backoff.RETRY_MAX_DELAY_SECONDS for d in delays)


def test_the_wait_happens_before_the_pointer_is_re_read(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    _recorder(monkeypatch, tmp_path, retries=[True, False], events=events)

    assert events == ["integrate", "sleep", "refresh", "integrate"]


def test_a_sleep_that_raises_never_escapes_the_integration_step(
    monkeypatch, tmp_path: Path
) -> None:

    def _explode(_seconds: float) -> None:
        raise RuntimeError("interrupted")

    _install_retry_loop(monkeypatch, tmp_path, retries=[True, False])

    outcome = auto_integrate.auto_integrate_after_commit(
        _default_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        sleep=_explode,
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.last_action == "rebased"


def test_full_jitter_shortens_the_wait_rather_than_fixing_it(monkeypatch, tmp_path: Path) -> None:
    long_wait = _recorder(monkeypatch, tmp_path, retries=[True, False])
    delays: list[float] = []
    _install_retry_loop(monkeypatch, tmp_path, retries=[True, False])
    auto_integrate.auto_integrate_after_commit(
        _default_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        sleep=delays.append,
        jitter=lambda: 0.0,
    )

    assert delays[0] < long_wait[0], "jitter must actually vary the delay"


def test_direct_auto_integrate_entry_points_preserve_unresolved_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _default_config()
    workspace_scope = WorkspaceScope(Path("/workspace"))
    monkeypatch.setattr(auto_integrate, "read_record", lambda _root: None)
    for unresolved in (
        RebaseState(last_action="conflict", last_reason="resolver exhausted"),
        RebaseState(last_action="skipped", recovery_record_retained=True),
    ):
        skipped = RebaseState(last_action="skipped", last_reason="no commits beyond target")
        monkeypatch.setattr(
            auto_integrate,
            "_auto_integrate_after_commit_inner",
            MagicMock(return_value=skipped),
        )

        after_commit = auto_integrate.auto_integrate_after_commit(
            config, workspace_scope, unresolved
        )
        assert after_commit is not None
        assert after_commit.integration_unresolved is True
        assert after_commit.last_action == "skipped"

        on_transition = auto_integrate.auto_integrate_on_phase_transition(
            config, workspace_scope, unresolved
        )
        assert on_transition is not None
        assert on_transition.integration_unresolved is True


def test_a_landing_releases_the_unresolved_integration_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _default_config()
    workspace_scope = WorkspaceScope(Path("/workspace"))
    monkeypatch.setattr(auto_integrate, "read_record", lambda _root: None)
    exhausted = RebaseState(
        last_action="conflict",
        resolution_exhausted=True,
        resolution_exhaustion_reason="chain exhausted",
    )
    landed = RebaseState(last_action="fast_forwarded", fast_forwarded=True)
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_after_commit_inner",
        MagicMock(return_value=landed),
    )

    outcome = auto_integrate.auto_integrate_after_commit(config, workspace_scope, exhausted)

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert outcome.integration_unresolved is False
    assert outcome.resolution_exhausted is False
