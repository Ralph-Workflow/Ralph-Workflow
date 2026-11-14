from __future__ import annotations

import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    TARGET,
    FrozenClock,
    make_conflicted_repo,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from ralph.pipeline.auto_integrate_resolve import ConflictResolver

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_RESOLUTION_PHASE = "rebase_conflict_resolution"

_OTHER_PHASES = ("development", "review")

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_scenario_b_agents_demoted_while_resolving_report_pending_and_keep_the_rebase": (
        "align outcome assertion with typed AGENT_FAILURE failure cause preserved across recovery"
    ),
}


def _declines(_root: Path, _target: str) -> bool:
    return False


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": TARGET,
            }
        }
    )


@pytest.fixture(scope="module")
def _template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_conflicted_repo(tmp_path_factory.mktemp("seed_repo") / "repo")


def _clone_repo(template: Path, target: Path) -> Path:
    shutil.copytree(template, target, dirs_exist_ok=True)
    return target


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    captured: list[str] = []
    sink_id = loguru_logger.add(lambda message: captured.append(str(message)), level="INFO")
    try:
        yield captured
    finally:
        loguru_logger.remove(sink_id)


def _run_integration(
    repo_root: Path,
    *,
    clock: FrozenClock,
    ledger: AgentFailureLedger,
    candidates: Sequence[str],
    resolving_agents: Sequence[str],
    conflict_resolver: ConflictResolver | None = None,
) -> tuple[RebaseState | None, list[str], list[str]]:
    invocations: list[str] = []
    bundle = resolution_policy_bundle(candidates)
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=resolving_agents,
        tmp_path=repo_root,
    )
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_config(),
    )
    token = install_executors(clock=clock)
    try:
        with _captured_logs() as logs:
            outcome = auto_integrate_after_commit(
                _config(),
                WorkspaceScope(repo_root),
                RebaseState(),
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=resolver,
                ledger=ledger,
                candidates=tuple(candidates),
                sleep=lambda _seconds: None,
                jitter=lambda: 0.0,
            )
    finally:
        reset_executors(token)
    return outcome, invocations, logs


def test_scenario_a_unproductive_resolver_is_demoted_and_the_next_one_resolves(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=("agentB",),
    )

    assert invocations == ["agentA", "agentA", "agentA", "agentB"]

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert outcome.fast_forwarded is True
    assert not outcome.is_intervention_required

    assert any(f"Agent 'agentA' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs)
    assert any("Selected agent agentB (skipped agentA:" in line for line in logs), (
        "the demoted agent must be skipped for a stated reason"
    )


def test_scenario_b_demoted_resolvers_report_pending_and_keep_the_rebase_state(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    for agent in ("agentA", "agentB"):
        ledger.record(AttemptFact(phase=_RESOLUTION_PHASE, agent=agent, artifact_valid=False))

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=(),
        conflict_resolver=_declines,
    )

    assert invocations == []

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
    assert outcome.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert not outcome.is_intervention_required
    assert not outcome.fast_forwarded

    assert outcome.last_target == TARGET

    assert any(
        "auto_integrate:" in line and f"'{TARGET}'" in line and "resolv" in line for line in logs
    ), "the skipped resolver must be reported for the target it was skipped for"


def test_scenario_b_agents_demoted_while_resolving_report_pending_and_keep_the_rebase(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=(),
    )

    assert invocations == ["agentA", "agentB"]

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
    assert outcome.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE
    assert not outcome.is_intervention_required
    assert not outcome.fast_forwarded

    assert outcome.last_target == TARGET
    assert rebase_in_progress(repo_root)

    for agent in ("agentA", "agentB"):
        assert any(
            f"Agent '{agent}' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs
        )


def test_scenario_c_integration_demotion_leaves_other_phases_untouched(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock), ledger=ledger)

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=("agentB",),
    )

    assert invocations == ["agentA", "agentB"]
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert outcome.fast_forwarded is True
    assert not outcome.is_intervention_required

    assert any(f"Agent 'agentA' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs)

    with _captured_logs() as other_logs:
        for phase in _OTHER_PHASES:
            selection = controller.preferred_agent_index(phase, ("agentA", "agentB"))
            assert selection.index == 0
            assert selection.agent == "agentA"
            assert not any(
                skipped_agent == "agentA" for skipped_agent, _ in selection.skipped_reasons
            )
            assert any(f"Phase {phase}: Selected agent agentA" in line for line in other_logs)


def test_scenario_c_demotion_elsewhere_leaves_integration_standing_intact(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock), ledger=ledger)

    for phase in _OTHER_PHASES:
        for _ in range(3):
            ledger.record(AttemptFact(phase=phase, agent="agentA", artifact_valid=False))

    with _captured_logs() as other_logs:
        for phase in _OTHER_PHASES:
            selection = controller.preferred_agent_index(phase, ("agentA", "agentB"))
            assert selection.index == 1
            assert selection.agent == "agentB"
            assert any(skipped_agent == "agentA" for skipped_agent, _ in selection.skipped_reasons)
            assert any(
                f"Phase {phase}: Selected agent agentB (skipped agentA:" in line
                for line in other_logs
            )

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=("agentA",),
    )

    assert invocations == ["agentA"]
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert outcome.fast_forwarded is True
    assert not outcome.is_intervention_required

    assert not any(
        f"Agent 'agentA' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs
    )
    assert not any("skipped agentA" in line for line in logs)
