"""Tests for the conflict-resolution wall-clock budget.

``general.auto_integrate_resolve_timeout_seconds`` is the ceiling for the
WHOLE resolution pipeline. Two separate defects used to let a conflicted
integration outlive it, and both are pinned here:

* the driver handed out ``ceiling / MAX_RESOLUTION_ROUNDS`` unconditionally
  while one round may run two chain candidates SEQUENTIALLY, so six
  attempts of a third each permitted twice the configured ceiling;
* :func:`ralph.pipeline.conflict_resolution.session.with_session_ceiling`
  DECLINED the override whenever the requested share sat below the
  run-wide idle watchdog or soft-wrapup threshold -- which, with the
  shipped defaults, is every share the integration ever asks for. The
  attempt then silently kept the unrelated 3,300 s run-wide maximum.

Every test here drives the REAL default config through the REAL default
invoker and the REAL session wrapper with an injected clock, stubbing
only :func:`ralph.pipeline.effect_executor.execute_agent_effect` -- the
last seam before a process is launched -- so the ceilings under assertion
are the ones an agent would actually have been started with.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.pipeline import effect_executor
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import MAX_RESOLUTION_ROUNDS
from ralph.pipeline.conflict_resolution.session import with_session_ceiling
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py"]

#: The shipped default, asserted so a config change cannot silently
#: invalidate the arithmetic below.
_CEILING_SECONDS = 900.0


@lru_cache(maxsize=1)
def _policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    """The real default config, not a hand-built stub."""
    return UnifiedConfig.model_validate({"general": {}})


class _FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _SpendingSession:
    """Stands in for ``execute_agent_effect`` at the launch boundary.

    Records the session ceiling the invocation would have been started
    with, then burns it on the clock -- the worst case an agent can
    impose, every attempt running right up to its maximum.
    """

    def __init__(self, clock: _FakeClock, *, spend: float | None = None) -> None:
        self.clock = clock
        self.spend = spend
        self.ceilings: list[float | None] = []

    def __call__(
        self, effect: object, config: object, *args: object, **kwargs: object
    ) -> PipelineEvent:
        del effect, args, kwargs
        assert isinstance(config, UnifiedConfig)
        ceiling = config.general.agent_max_session_seconds
        self.ceilings.append(ceiling)
        assert ceiling is not None
        self.clock.now += ceiling if self.spend is None else self.spend
        return PipelineEvent.AGENT_FAILURE


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    surviving: Sequence[str] = _CONFLICTED,
) -> None:
    """Stub the git queries and the prompt render; leave the budget real."""
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(surviving),
    )
    prompt_path = tmp_path / "conflict-prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(
        driver_module, "render_conflict_prompt", lambda **kwargs: prompt_path
    )


def _run(
    tmp_path: Path, *, clock: _FakeClock, config: UnifiedConfig | None = None
) -> bool:
    """Drive the pipeline through its REAL default invoker."""
    return run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=config if config is not None else _config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        clock=clock,
    )


def _install_session(
    monkeypatch: pytest.MonkeyPatch, session: _SpendingSession
) -> None:
    """Cut the production path exactly where a process would be launched.

    ``session.py`` calls ``execute_agent_effect`` through the module
    object, so replacing the attribute here is what the real
    :func:`~ralph.pipeline.conflict_resolution.session.invoke_resolution_agent`
    will reach -- everything before it stays production code.
    """
    monkeypatch.setattr(
        effect_executor,
        "execute_agent_effect",
        session,
    )


def _drive_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[_SpendingSession, _FakeClock]:
    """Run every round with agents that spend their whole share."""
    _install_seams(monkeypatch, tmp_path)
    clock = _FakeClock()
    session = _SpendingSession(clock)
    _install_session(monkeypatch, session)
    assert _run(tmp_path, clock=clock) is False
    return session, clock


def test_default_resolve_ceiling_is_the_documented_value() -> None:
    """The arithmetic in this module rests on the shipped default."""
    assert _config().general.auto_integrate_resolve_timeout_seconds == _CEILING_SECONDS


def test_every_attempt_receives_a_bounded_positive_maximum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No attempt may run unbounded, and none may claim the whole ceiling."""
    session, _ = _drive_exhausted(monkeypatch, tmp_path)

    assert session.ceilings, "the default invoker never ran"
    for ceiling in session.ceilings:
        assert ceiling is not None
        assert 0.0 < ceiling <= _CEILING_SECONDS


def test_cumulative_attempts_cannot_exceed_the_configured_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression: rounds times candidates used to multiply the ceiling."""
    session, clock = _drive_exhausted(monkeypatch, tmp_path)

    assert sum(ceiling or 0.0 for ceiling in session.ceilings) <= _CEILING_SECONDS
    assert clock.now - 1_000.0 <= _CEILING_SECONDS
    # At least one attempt per round, and possibly two per round once the
    # chain falls back: exactly the case the old per-round division could
    # not bound, so the deadline must survive it.
    assert len(session.ceilings) >= MAX_RESOLUTION_ROUNDS


def test_an_overrunning_attempt_declines_every_later_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One attempt that eats the whole deadline ends the pipeline.

    Proves the decline path: the remaining rounds are refused outright
    rather than each being granted a fresh share of an already-spent
    budget.
    """
    _install_seams(monkeypatch, tmp_path)
    clock = _FakeClock()
    session = _SpendingSession(clock, spend=_CEILING_SECONDS)
    _install_session(monkeypatch, session)

    assert _run(tmp_path, clock=clock) is False
    assert len(session.ceilings) == 1
    assert clock.now - 1_000.0 == _CEILING_SECONDS


def test_the_session_ceiling_is_applied_under_the_shipped_defaults() -> None:
    """The share must WIN over the run-wide maximum, not be ignored."""
    config = _config()
    share = _CEILING_SECONDS / MAX_RESOLUTION_ROUNDS
    bounded = with_session_ceiling(config, share)

    assert config.general.agent_max_session_seconds != share, (
        "the default is expected to differ, or this test proves nothing"
    )
    assert bounded.general.agent_max_session_seconds == share


def test_the_bounded_general_config_stays_valid() -> None:
    """The copy must satisfy the orderings GeneralConfig validates."""
    bounded = with_session_ceiling(_config(), 30.0).general

    assert bounded.agent_max_session_seconds == 30.0
    assert bounded.agent_idle_timeout_seconds <= 30.0
    assert bounded.agent_session_soft_wrapup_seconds is not None
    assert bounded.agent_session_soft_wrapup_seconds < 30.0
    # model_copy skips validation, so re-validate to prove the values are
    # ones the config model would have accepted in the first place.
    GeneralConfig.model_validate(bounded.model_dump())


def test_a_generous_share_leaves_the_configured_watchdogs_alone() -> None:
    """Only values that would break the ordering are pulled down."""
    config = _config()
    bounded = with_session_ceiling(config, 6_000.0).general

    assert bounded.agent_max_session_seconds == 6_000.0
    assert (
        bounded.agent_idle_timeout_seconds == config.general.agent_idle_timeout_seconds
    )
    assert (
        bounded.agent_session_soft_wrapup_seconds
        == config.general.agent_session_soft_wrapup_seconds
    )


def test_a_non_positive_share_cannot_produce_a_ceiling() -> None:
    """``gt=0`` is a config invariant; the original is returned instead."""
    config = _config()
    assert with_session_ceiling(config, 0.0) is config
    assert with_session_ceiling(config, -1.0) is config
