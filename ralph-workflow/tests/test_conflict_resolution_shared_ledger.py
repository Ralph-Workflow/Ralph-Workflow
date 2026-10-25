"""Conflict resolution sessions share one agent ledger with the rest of the run."""

from __future__ import annotations

import dataclasses
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.display.context import make_display_context
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_conflict_resolution_pipeline import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    from ralph.pipeline.factory import PipelineDeps

pytestmark = pytest.mark.timeout_seconds(5)


def _deps(store: AgentUnavailabilityTracker) -> PipelineDeps:
    context = make_display_context(console=Console(file=StringIO()))
    return dataclasses.replace(make_test_pipeline_deps(context), unavailability_store=store)


def _resolve(tmp_path: Path, deps: PipelineDeps, calls: list[str]) -> bool:
    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        calls.append(agent_name)
        if agent_name == "primary":
            raise RuntimeError("agent exploded")
        return True

    return run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=deps,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=ResolutionSession(),
    )


def test_resolver_that_failed_in_an_earlier_session_is_not_offered_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    deps = _deps(AgentUnavailabilityTracker(clock=FakeClock()))
    first: list[str] = []
    second: list[str] = []

    assert _resolve(tmp_path, deps, first) is True
    assert _resolve(tmp_path, deps, second) is True

    # Primary fails 3 times (the ledger consecutive limit), then falls over to fallback.
    assert first == ["primary", "primary", "primary", "fallback"]
    assert second == ["fallback"]


def test_agent_benched_elsewhere_in_the_run_is_skipped_by_the_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.recovery.unavailability_reason import UnavailabilityReason

    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    store = AgentUnavailabilityTracker(clock=FakeClock())
    store.mark_unavailable("analysis", "primary", UnavailabilityReason.OUT_OF_CREDITS)
    calls: list[str] = []

    assert _resolve(tmp_path, _deps(store), calls) is True

    assert calls == ["fallback"]


def test_when_every_candidate_is_benched_the_one_returning_soonest_goes_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.recovery.unavailability_reason import UnavailabilityReason

    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    store = AgentUnavailabilityTracker(clock=FakeClock())
    store.mark_unavailable("x", "primary", UnavailabilityReason.OUT_OF_CREDITS)
    store.mark_unavailable("x", "fallback", UnavailabilityReason.PROCESS_EXITED)
    calls: list[str] = []

    assert _resolve(tmp_path, _deps(store), calls) is True

    assert calls == ["fallback"]
