"""Tests proving workspace changes are routed and observed during conflict resolution.

When an agent makes filesystem modifications in the workspace without resolving
the conflict, FsWorkspace and DefaultWorkspaceChangeProbe observe the changes,
setting workspace_changed=True on the AttemptFact. This allows the agent to run
up to the total unproductive limit (9 attempts) rather than being cut off by the
consecutive unproductive limit (3 attempts).
"""

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
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, AttemptOutcome
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def test_conflict_resolution_observes_workspace_changes_allowing_total_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When an agent changes files on disk, workspace_changed=True allows 9 attempts before demotion."""
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
    facts_recorded: list[AttemptFact] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal markers_remaining
        invocations.append(agent_name)
        if agent_name == "agentA":
            # Agent makes progress on disk on each attempt, but does not resolve conflict markers
            progress_file = tmp_path / f"change_{len(invocations)}.txt"
            progress_file.write_text(f"work done in invocation {len(invocations)}")
            return False

        # Fallback candidate agentB succeeds and clears conflict markers
        markers_remaining = False
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=3,
            total_limit=9,
        )

        original_record = ledger.record

        def _spy_record(fact: AttemptFact) -> AttemptOutcome:
            facts_recorded.append(fact)
            return original_record(fact)

        monkeypatch.setattr(ledger, "record", _spy_record)

        # Do not provide workspace or workspace_probe to ResolutionSession.
        # Driver must instantiate session.workspace from workspace_scope.
        session = ResolutionSession(ledger=ledger, max_rounds_per_stop=15)

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

        # Agent A ran 9 times (total limit, NOT 3 consecutive) because workspace_changed was True
        agent_a_invocations = [name for name in invocations if name == "agentA"]
        assert len(agent_a_invocations) == 9

        # Agent B was invoked on attempt 10 after Agent A reached total limit and became Spent
        assert invocations == ["agentA"] * 9 + ["agentB"]

        # Verify recorded facts observed workspace_changed=True for all Agent A attempts
        agent_a_facts = [f for f in facts_recorded if f.agent == "agentA"]
        assert len(agent_a_facts) == 9
        assert all(f.workspace_changed for f in agent_a_facts)

        # Agent A is spent due to total limit, not in temporary cooldown
        agent_a_status = ledger.status(PHASE_RESOLUTION, "agentA")
        assert agent_a_status.is_spent is True
    finally:
        reset_executors(token)


def test_conflict_resolution_without_workspace_changes_stops_at_consecutive_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When an agent does not change files on disk, consecutive limit (3) triggers demotion."""
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
    facts_recorded: list[AttemptFact] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal markers_remaining
        invocations.append(agent_name)
        if agent_name == "agentA":
            # Agent fails without modifying any workspace files
            return False

        # Fallback candidate agentB succeeds
        markers_remaining = False
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=3,
            total_limit=9,
        )

        original_record = ledger.record

        def _spy_record(fact: AttemptFact) -> AttemptOutcome:
            facts_recorded.append(fact)
            return original_record(fact)

        monkeypatch.setattr(ledger, "record", _spy_record)

        session = ResolutionSession(ledger=ledger, max_rounds_per_stop=15)

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

        # Agent A ran only 3 times (consecutive limit) because workspace_changed was False
        assert invocations == ["agentA"] * 3 + ["agentB"]

        agent_a_facts = [f for f in facts_recorded if f.agent == "agentA"]
        assert len(agent_a_facts) == 3
        assert all(not f.workspace_changed for f in agent_a_facts)

        # Agent A is in cooldown due to consecutive limit, not spent
        agent_a_status = ledger.status(PHASE_RESOLUTION, "agentA")
        assert agent_a_status.is_cooldown is True
    finally:
        reset_executors(token)
