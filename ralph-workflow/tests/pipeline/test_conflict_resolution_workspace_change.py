from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {}, "conflict_resolution": {"max_rounds_per_stop": 15}}
    )


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_conflict_resolution_observes_workspace_changes_allowing_total_limit": (
        "under per-round candidate fallover, fallback candidate interleaves each round "
        "until primary candidate reaches total limit and fallback candidate resolves"
    ),
    "test_conflict_resolution_without_workspace_changes_stops_at_consecutive_limit": (
        "under per-round candidate fallover, fallback candidate interleaves each round "
        "until primary candidate reaches consecutive limit and fallback candidate resolves"
    ),
}


def test_conflict_resolution_observes_workspace_changes_allowing_total_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conflicted_file = "src/example.py"
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [conflicted_file])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _root, _kinds: ())
    markers_remaining = True

    def _fake_markers(_root: Path, paths: list[str]) -> list[str]:
        return list(paths) if markers_remaining else []

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    workspace_scope = WorkspaceScope(tmp_path)
    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal markers_remaining
        invocations.append(agent_name)
        progress_file = tmp_path / f"change_{len(invocations)}.txt"
        progress_file.write_text(f"work done in invocation {len(invocations)}")
        if agent_name == "agentA":
            return False

        if ledger.status(PHASE_RESOLUTION, "agentA").is_spent:
            markers_remaining = False
            return True
        return False

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=3,
            total_limit=9,
        )

        session = ResolutionSession(
            ledger=ledger,
            max_rounds_per_stop=15,
            recovery_controller=RecoveryController(
                ledger=ledger, options=RecoveryControllerOptions(clock=clock)
            ),
        )

        succeeded = driver_module.run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=workspace_scope,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            clock=clock.monotonic,
            session=session,
        )

        assert succeeded is True

        agent_a_invocations = [name for name in invocations if name == "agentA"]
        assert len(agent_a_invocations) == 9

        assert invocations == ["agentA", "agentB"] * 9

        agent_a_status = ledger.status(PHASE_RESOLUTION, "agentA")
        assert agent_a_status.is_spent is True
    finally:
        reset_executors(token)


def test_conflict_resolution_without_workspace_changes_stops_at_consecutive_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conflicted_file = "src/example.py"
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: [conflicted_file])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _root, _kinds: ())
    markers_remaining = True

    def _fake_markers(_root: Path, paths: list[str]) -> list[str]:
        return list(paths) if markers_remaining else []

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    workspace_scope = WorkspaceScope(tmp_path)
    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal markers_remaining
        invocations.append(agent_name)
        if agent_name == "agentA":
            return False

        if ledger.status(PHASE_RESOLUTION, "agentA").is_cooldown:
            markers_remaining = False
            return True
        return False

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=3,
            total_limit=9,
        )

        session = ResolutionSession(
            ledger=ledger,
            max_rounds_per_stop=15,
            recovery_controller=RecoveryController(
                ledger=ledger, options=RecoveryControllerOptions(clock=clock)
            ),
        )

        succeeded = driver_module.run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=workspace_scope,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            clock=clock.monotonic,
            session=session,
        )

        assert succeeded is True

        assert invocations == ["agentA", "agentB"] * 3

        agent_a_status = ledger.status(PHASE_RESOLUTION, "agentA")
        assert agent_a_status.is_cooldown is True
    finally:
        reset_executors(token)
