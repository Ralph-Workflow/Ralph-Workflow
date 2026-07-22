"""Deterministic runner-seam regressions for default auto-integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.git.merge import WORKTREE_FOUND
from ralph.pipeline import (
    auto_integrate,
    auto_integrate_backoff,
    auto_integrate_ff,
    runner,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _stub_ff_environment(monkeypatch, root: Path) -> None:
    """Point every fast-forward lookup at a single in-memory worktree."""
    # The fast-forward now reads the target through ``observe_branch_sha``,
    # which reports (sha, query_ok) so a FAILED ``git rev-parse`` can be
    # retried instead of being mistaken for an absent branch. The stub
    # answers "read successfully", which is the environment these tests
    # always described.
    monkeypatch.setattr(
        auto_integrate_ff,
        "observe_branch_sha",
        lambda _root, _branch: ("old-main", True),
    )
    monkeypatch.setattr(auto_integrate_ff, "is_ancestor", lambda *_args: True)
    monkeypatch.setattr(auto_integrate_ff, "find_main_worktree_root", lambda _root: root)
    # The fast-forward now consults ``worktree_lookup``, which reports
    # found / not-checked-out / query-failed instead of collapsing the
    # last two into ``None``. The stub answers "found", which is the
    # same environment these tests always described.
    monkeypatch.setattr(
        auto_integrate_ff,
        "worktree_lookup",
        lambda _root, _branch: (WORKTREE_FOUND, root),
    )


def test_default_config_resolves_main_and_lands_via_ff_only(monkeypatch) -> None:
    """AC-04: a checked-out target lands through ``merge --ff-only``, not the CAS.

    ``merge --ff-only`` advances the ref, the index and the working tree
    together, so it is tried first no matter how dirty that checkout is;
    the CAS advances the ref alone and must stay a fallback.
    """
    config = _default_config()
    root = Path("/workspace/feature")
    monkeypatch.setattr(auto_integrate, "resolve_origin_head_branch", lambda _root: None)
    monkeypatch.setattr(
        auto_integrate,
        "branch_exists",
        lambda _root, branch: branch == "main",
    )

    target = auto_integrate.resolve_integration_target(config, root)

    _stub_ff_environment(monkeypatch, root)
    worktree_ff = MagicMock(return_value=True)
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "fast_forward_via_worktree", worktree_ff)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert target == "main"
    assert auto_integrate_ff.fast_forward_target(root, target, "feature-head") == (True, "")
    assert worktree_ff.call_args.args == (root, "feature-head")
    cas.assert_not_called()


def test_refused_ff_only_falls_back_to_observed_sha_cas(monkeypatch) -> None:
    """AC-04/AC-08: when git refuses the merge, the CAS still lands the ref.

    The CAS oldvalue must remain the SAME observed target SHA the
    ancestry check was bound to, so a concurrent landing between the two
    fails closed instead of overwriting.
    """
    root = Path("/workspace/feature")
    _stub_ff_environment(monkeypatch, root)
    monkeypatch.setattr(
        auto_integrate_ff, "fast_forward_via_worktree", lambda *_args: False
    )
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert auto_integrate_ff.fast_forward_target(root, "main", "feature-head") == (True, "")
    assert cas.call_args.args == (root, "main", "old-main", "feature-head")


def test_commit_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful commit uses the unset-target config path."""
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
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_phase_transition_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful phase transition uses the unset-target path."""
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
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


# ---------------------------------------------------------------------------
# Bounded jittered backoff between landing retries
# ---------------------------------------------------------------------------
#
# Every seam here is injected -- no wait ever happens, and this file never
# imports ``random``. The production defaults (``time.sleep`` /
# ``random.random``) are only exercised by the real-git e2e suite.


def _install_retry_loop(
    monkeypatch,
    root: Path,
    *,
    retries: list[bool],
    events: list[str] | None = None,
) -> None:
    """Drive the bounded landing loop with a scripted retry verdict.

    ``retries[i]`` is what attempt ``i`` reports as ``retry_ff`` -- True
    meaning the fast-forward lost the compare-and-swap and the loop must
    re-integrate onto the moved tip.
    """
    verdicts = iter(retries)
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
    monkeypatch.setattr(
        auto_integrate, "observe_conflict_identity", lambda _root, _target: "id"
    )
    monkeypatch.setattr(
        auto_integrate, "resolver_allowed", lambda _state, _target, _identity: True
    )

    def _refresh(_config, _root, _target) -> str:
        if events is not None:
            events.append("refresh")
        return "refreshed"

    def _integrate_once(
        *_args: object, **_kwargs: object
    ) -> tuple[RebaseState, bool]:
        if events is not None:
            events.append("integrate")
        record = RebaseState(
            last_action="rebased", last_target="main", fast_forwarded=True
        )
        return record, next(verdicts, False)

    monkeypatch.setattr(auto_integrate, "_refresh_target", _refresh)
    monkeypatch.setattr(auto_integrate, "_integrate_once", _integrate_once)


def _recorder(monkeypatch, root: Path, *, retries: list[bool], events=None):
    """Run one integration and return the delays the loop asked to sleep."""
    delays: list[float] = []
    _install_retry_loop(monkeypatch, root, retries=retries, events=events)

    def _sleep(seconds: float) -> None:
        if events is not None:
            events.append("sleep")
        delays.append(seconds)

    auto_integrate.auto_integrate_after_commit(
        _default_config(),
        SimpleNamespace(root=str(root)),
        RebaseState(),
        sleep=_sleep,
        jitter=lambda: 1.0,
    )
    return delays


def test_a_first_attempt_that_lands_never_waits(monkeypatch, tmp_path: Path) -> None:
    """The common case must cost nothing: no collision, no backoff."""
    assert _recorder(monkeypatch, tmp_path, retries=[False]) == []


def test_a_lost_compare_and_swap_waits_once_before_retrying(
    monkeypatch, tmp_path: Path
) -> None:
    """One collision, one bounded wait, inside the documented schedule."""
    delays = _recorder(monkeypatch, tmp_path, retries=[True, False])

    assert len(delays) == 1
    assert 0 < delays[0] <= auto_integrate_backoff.RETRY_MAX_DELAY_SECONDS
    assert delays[0] >= auto_integrate_backoff.RETRY_BASE_DELAY_SECONDS * 0.5


def test_the_delay_grows_across_attempts_and_stays_capped(
    monkeypatch, tmp_path: Path
) -> None:
    """Exponential, so two agents that keep colliding spread further apart."""
    delays = _recorder(monkeypatch, tmp_path, retries=[True, True, True])

    assert len(delays) == 2
    assert delays[1] > delays[0], "the backoff must grow, not repeat"
    assert all(d <= auto_integrate_backoff.RETRY_MAX_DELAY_SECONDS for d in delays)


def test_the_wait_happens_before_the_pointer_is_re_read(
    monkeypatch, tmp_path: Path
) -> None:
    """Ordering is the point: the retry must observe a POST-wait pointer.

    Refreshing first and then sleeping would land the retry on a pointer
    read before the collision had time to settle, which is exactly the
    lockstep the backoff exists to break.
    """
    events: list[str] = []
    _recorder(monkeypatch, tmp_path, retries=[True, False], events=events)

    assert events == ["integrate", "sleep", "refresh", "integrate"]


def test_a_sleep_that_raises_never_escapes_the_integration_step(
    monkeypatch, tmp_path: Path
) -> None:
    """Integration never raises into the run -- the backoff is no exception."""

    def _explode(_seconds: float) -> None:
        raise RuntimeError("interrupted")

    _install_retry_loop(monkeypatch, tmp_path, retries=[True, False])

    outcome = auto_integrate.auto_integrate_after_commit(
        _default_config(),
        SimpleNamespace(root=str(tmp_path)),
        RebaseState(),
        sleep=_explode,
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.last_action == "rebased"


def test_full_jitter_shortens_the_wait_rather_than_fixing_it(
    monkeypatch, tmp_path: Path
) -> None:
    """A deterministic backoff would re-synchronise the agents it separated."""
    long_wait = _recorder(monkeypatch, tmp_path, retries=[True, False])
    delays: list[float] = []
    _install_retry_loop(monkeypatch, tmp_path, retries=[True, False])
    auto_integrate.auto_integrate_after_commit(
        _default_config(),
        SimpleNamespace(root=str(tmp_path)),
        RebaseState(),
        sleep=delays.append,
        jitter=lambda: 0.0,
    )

    assert delays[0] < long_wait[0], "jitter must actually vary the delay"
