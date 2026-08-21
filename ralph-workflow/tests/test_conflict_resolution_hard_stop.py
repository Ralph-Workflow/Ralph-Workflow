"""Tests for the resolution driver's OWN wall-clock stop.

The driver used to bound a round by handing the agent layer a session
ceiling and then blocking on the call. That is a bound only for as long
as the agent layer honours it: every watchdog in
``ralph.agents.invoke`` runs INSIDE the very call the driver is waiting
on, so a wedge anywhere below the driver -- a reader that never pumps, a
watchdog that never evaluates, a process that cannot be signalled --
leaves the driver blocked for the rest of the run with a rebase paused
mid-replay and no way out but SIGKILL.

A ceiling the driver cannot enforce is not a ceiling. These tests pin
the enforcement the driver owns itself: an attempt that does not come
back within its share is ABANDONED, its agent processes are reaped, and
the round is failed so the caller can abort the rebase.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import (
    RESOLVE_TIMEOUT_SECONDS,
    run_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.hard_stop import call_with_hard_stop
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py"]


@lru_cache(maxsize=1)
def _policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


class _RecordingHardStop:
    """Stands in for the driver's hard stop; every attempt is abandoned."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def __call__(self, call: object, timeout_seconds: float) -> bool | None:
        del call
        self.timeouts.append(timeout_seconds)
        return None


def _install_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stub the git queries and the prompt render; leave the budget real."""
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(_CONFLICTED),
    )
    prompt_path = tmp_path / "conflict-prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(driver_module, "render_conflict_prompt", lambda **kwargs: prompt_path)


def test_an_attempt_that_never_returns_fails_its_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The driver reports failure instead of blocking on a wedged agent."""
    _install_seams(monkeypatch, tmp_path)
    hard_stop = _RecordingHardStop()

    resolved = run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        hard_stop=hard_stop,
    )

    assert resolved is False
    assert hard_stop.timeouts, "the driver never applied a hard stop"


def test_every_hard_stop_is_a_bounded_share_of_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No attempt may be given an unbounded, zero, or whole-ceiling stop."""
    _install_seams(monkeypatch, tmp_path)
    hard_stop = _RecordingHardStop()

    run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        hard_stop=hard_stop,
    )

    for timeout in hard_stop.timeouts:
        assert 0.0 < timeout <= RESOLVE_TIMEOUT_SECONDS


def test_a_call_that_returns_in_time_yields_its_value() -> None:
    """The hard stop is transparent to an attempt that finishes."""
    assert call_with_hard_stop(lambda: True, 5.0) is True
    assert call_with_hard_stop(lambda: False, 5.0) is False


def test_a_call_that_outlives_its_stop_is_abandoned() -> None:
    """A blocked attempt returns control to the driver instead of hanging."""
    release = threading.Event()

    def _never_returns_in_time() -> bool:
        return release.wait(timeout=30.0)

    try:
        assert call_with_hard_stop(_never_returns_in_time, 0.05) is None
    finally:
        release.set()


def test_a_raising_call_is_reported_as_a_failed_attempt() -> None:
    """An attempt that dies is a failed round, never a hang or a crash."""

    def _raises() -> bool:
        msg = "agent layer exploded"
        raise RuntimeError(msg)

    assert call_with_hard_stop(_raises, 5.0) is False
