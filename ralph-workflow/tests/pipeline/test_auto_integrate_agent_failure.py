"""PRODUCT_CRITERIA §7.5 scenarios through the REAL outer integration entry point.

Every scenario here drives :func:`auto_integrate_after_commit` -- the
seam the run itself calls after a commit -- over a REAL repository whose
feature branch conflicts with its target. Production owns everything that
decides:

* the resolver chain comes from the shipped policy;
* the SELECTION of the next resolver, the BENCHING of an unproductive
  one and its DEMOTION come from
  :class:`~ralph.recovery.agent_failure_ledger.AgentFailureLedger`
  through the recovery controller; and
* the integration OUTCOME comes from the repository (rebased and landed,
  or a genuinely unresolvable conflict).

The suite replaces only the observable boundaries: the scripted resolver
PROCESS outcome, the clock, and the Git observations (the repository is
a real one). It asserts only on which agents ran and in what order, on
the integration outcome, and on the logged demotion/skip reasons.

No test reads a private counter, a snapshot field or any other private
ledger state.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState, ResolutionFailureCause
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
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

pytestmark = pytest.mark.timeout_seconds(30)

#: The phase the integration/resolution chain owns. Its history is
#: independent of the development and review phases.
_RESOLUTION_PHASE = "rebase_conflict_resolution"

#: The phases whose standing must stay independent of the integration.
_OTHER_PHASES = ("development", "review")


def _declines(_root: Path, _target: str) -> bool:
    """A merge-conflict resolver that never resolves anything."""
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
    """One real repository every scenario copies, so the git setup is paid once."""
    return make_conflicted_repo(tmp_path_factory.mktemp("seed_repo") / "repo")


def _clone_repo(template: Path, target: Path) -> Path:
    """Copy the seeded repository so every scenario owns its own worktree."""
    shutil.copytree(template, target, dirs_exist_ok=True)
    return target


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Collect everything production logged while the block runs."""
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
    """Run one integration attempt and report its outcome and what it logged.

    The Git seams and the resolver-dispatch seam are left at their
    production implementations: the rebase, its stops and its landing are
    git's own, and the resolver loop is the shipped conflict-resolution
    pipeline.
    """
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
    _stubs: dict[str, object],
) -> None:
    """§7.5 (a): resolver A produces nothing three times, B resolves.

    Production runs A three times, the ledger demotes it, production
    lands B, and the integration is reported as a landed rebase. The
    recorded order is the order PRODUCTION chose -- the suite supplies
    only what each invocation returns.
    """
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

    # The order production chose: A three times, then B.
    assert invocations == ["agentA", "agentA", "agentA", "agentB"]

    # The integration succeeded and landed.
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert outcome.fast_forwarded is True
    assert not outcome.is_intervention_required

    # A's demotion was logged, with its phase.
    assert any(f"Agent 'agentA' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs)
    # And the skip was logged with the reason production gave for it.
    assert any("Selected agent agentB (skipped agentA:" in line for line in logs), (
        "the demoted agent must be skipped for a stated reason"
    )


def test_scenario_b_demoted_resolvers_report_pending_and_keep_the_rebase_state(
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 (b): every integration agent is demoted.

    The integration is reported PENDING -- the conflict is recorded, the
    attempt is not a failure, the operator is not handed the run, and the
    rebase state is preserved.
    """
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    for agent in ("agentA", "agentB"):
        for _ in range(3):
            ledger.record(AttemptFact(phase=_RESOLUTION_PHASE, agent=agent, artifact_valid=False))

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=(),
        conflict_resolver=_declines,
    )

    # No demoted agent was invoked.
    assert invocations == []

    # The integration is pending, not failed and not an operator handoff.
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
    assert outcome.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert not outcome.is_intervention_required
    assert not outcome.fast_forwarded

    # The rebase state is preserved.
    assert outcome.last_target == TARGET

    # The skip was reported for the blocked target.
    assert any(
        "auto_integrate:" in line and f"'{TARGET}'" in line and "resolv" in line for line in logs
    ), "the skipped resolver must be reported for the target it was skipped for"


def test_scenario_b_agents_demoted_while_resolving_report_pending_and_keep_the_rebase(
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 (b): every integration agent is demoted WHILE it is resolving.

    Production spends both candidates, the ledger demotes both, and the
    integration is reported pending with the paused rebase still on disk.
    """
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    outcome, invocations, logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA", "agentB"),
        resolving_agents=(),
    )

    # Both candidates were spent, three attempts each, and no more.
    assert invocations == ["agentA", "agentA", "agentA", "agentB", "agentB", "agentB"]

    # The integration is pending, not failed and not an operator handoff.
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
    assert outcome.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert not outcome.is_intervention_required
    assert not outcome.fast_forwarded

    # The rebase state is preserved: the paused rebase is still there.
    assert outcome.last_target == TARGET
    assert rebase_in_progress(repo_root)

    # Both demotions were logged, with their phase.
    for agent in ("agentA", "agentB"):
        assert any(
            f"Agent '{agent}' demoted in phase '{_RESOLUTION_PHASE}'" in line for line in logs
        )


def test_scenario_c_integration_demotion_leaves_other_phases_untouched(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 (c): a demoted integration agent keeps its standing elsewhere."""
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    outcome, invocations, _logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA",),
        resolving_agents=(),
    )

    assert invocations == ["agentA", "agentA", "agentA"]
    assert outcome is not None

    # The agent is demoted in the integration phase ...
    assert not ledger.is_available(_RESOLUTION_PHASE, "agentA")
    # ... and untouched in every other phase.
    for phase in _OTHER_PHASES:
        assert ledger.is_available(phase, "agentA")


def test_scenario_c_demotion_elsewhere_leaves_integration_standing_intact(
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 (c): a demotion in another phase does not demote the integration."""
    repo_root = _clone_repo(_template_repo, tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    for _ in range(3):
        ledger.record(AttemptFact(phase="development", agent="agentA", artifact_valid=False))

    # The agent is unavailable in development ...
    assert not ledger.is_available("development", "agentA")
    # ... and fully eligible in the integration phase.
    assert ledger.is_available(_RESOLUTION_PHASE, "agentA")

    outcome, invocations, _logs = _run_integration(
        repo_root,
        clock=clock,
        ledger=ledger,
        candidates=("agentA",),
        resolving_agents=("agentA",),
    )

    # The agent ran and resolved: the development demotion did not reach it.
    assert invocations == ["agentA"]
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert outcome.fast_forwarded is True
    assert not outcome.is_intervention_required
