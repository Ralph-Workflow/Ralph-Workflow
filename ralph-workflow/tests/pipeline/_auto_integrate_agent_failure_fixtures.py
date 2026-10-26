"""Observable boundaries for the PRODUCT_CRITERIA §7.5 outer-integration suite.

The §7.5 scenarios are proved through the REAL outer integration entry
point (:func:`ralph.pipeline.auto_integrate.auto_integrate_after_commit`)
and its rebase flow. Everything that DECIDES anything stays in
production: the candidate chain comes from the shipped policy, the
selection and the demotion come from
:class:`~ralph.recovery.agent_failure_ledger.AgentFailureLedger` through
the recovery controller, and the integration outcome comes from the
repository.

This module supplies only the boundaries that are observable from
outside production:

* the scripted resolver PROCESS outcome -- what one agent invocation
  returns, and whether the conflicted artifact appears;
* the clock every cooldown and every wait is measured against; and
* Git observations -- the repository under test is a real one, so the
  rebase, its stops and its landing are git's own.

The workspace-change observation is the one boundary this suite does
NOT have to fake: an agent that produces nothing changes nothing, so
the resolution session sees no workspace change either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.pipeline.conflict_resolution import run_rebase_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import AgentChainConfig

if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, RebaseStopResolver
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

#: The path the feature branch and the target branch conflict on.
CONFLICTED_PATH = "shared.txt"

#: The out-of-graph drain the conflict-resolution chain is bound to.
RESOLUTION_CHAIN = "rebase_conflict_resolution"

#: The branch the feature is integrated onto.
TARGET = "main"

#: What a scripted agent leaves behind when it resolves the conflict.
RESOLVED_CONTENT = "resolved by the resolver\n"

#: This module crosses the real-Git boundary itself (:func:`make_conflicted_repo`
#: drives ``Repo.init`` / ``repo.index.commit``), so it declares the
#: ``subprocess_e2e`` profile the same way its consumer suite does. The
#: test-policy audit reads the module-level ``pytestmark`` and exempts the
#: file's GitPython crossings on that declaration.
pytestmark = pytest.mark.subprocess_e2e


class FrozenClock:
    """A clock that never advances.

    A cooldown that never expires is what makes the demotion observable:
    once the ledger has benched an agent, nothing brings it back inside
    the scenario, so the recorded invocation order is the order
    production chose and not the order a timer happened to allow.
    """

    def __init__(self) -> None:
        self._now = 1_000.0

    def now(self) -> float:
        """Return the frozen wall-clock reading."""
        return self._now

    def now_utc(self) -> datetime:
        """Return the frozen UTC wall-clock reading."""
        return datetime.fromtimestamp(self._now, tz=UTC)

    def epoch(self) -> float:
        """Return the frozen epoch reading."""
        return self._now

    def monotonic(self) -> float:
        """Return the frozen monotonic reading."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Wait without advancing: a frozen clock expires no cooldown."""
        del seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        """Never wait and never advance."""
        del event, seconds
        return False


def make_conflicted_repo(tmp_path: Path) -> Path:
    """Create a real repository whose feature branch conflicts with the target.

    ``main`` and ``feature`` both rewrite :data:`CONFLICTED_PATH` from the
    same seed, so replaying the feature onto the target produces a real
    paused rebase with a real conflicted file -- the state the production
    resolver loop is written against. The repository is created IN
    ``tmp_path``, which is the temporary root the scenario owns.
    """
    repo = Repo.init(tmp_path, initial_branch=TARGET)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (tmp_path / CONFLICTED_PATH).write_text("initial seed\n", encoding="utf-8")
    repo.index.add([CONFLICTED_PATH])
    repo.index.commit("initial commit")
    feature = repo.create_head("feature")
    (tmp_path / CONFLICTED_PATH).write_text("target side\n", encoding="utf-8")
    repo.index.add([CONFLICTED_PATH])
    repo.index.commit("target commit")
    repo.head.reference = feature
    repo.head.reset(index=True, working_tree=True)
    (tmp_path / CONFLICTED_PATH).write_text("feature side\n", encoding="utf-8")
    repo.index.add([CONFLICTED_PATH])
    repo.index.commit("feature commit")
    return tmp_path


@lru_cache(maxsize=1)
def _shipped_policy_bundle() -> PolicyBundle:
    """Return the shipped policy bundle, loaded once."""
    return load_policy(default_dir())


def resolution_policy_bundle(candidates: Sequence[str]) -> PolicyBundle:
    """Return the shipped policy with the resolution chain bound to ``candidates``.

    The chain is read from the shipped defaults, so the drain binding, the
    chain name and the surrounding policy stay exactly as production ships
    them; only the ordered list of resolver agents changes.
    """
    bundle = _shipped_policy_bundle()
    chains = dict(bundle.agents.agent_chains)
    chains[RESOLUTION_CHAIN] = AgentChainConfig(
        agents=list(candidates),
        max_retries=2,
        retry_delay_ms=0,
    )
    return bundle.model_copy(
        update={"agents": bundle.agents.model_copy(update={"agent_chains": chains})}
    )


def make_scripted_resolver_process(
    *,
    invocations: list[str],
    resolving_agents: Sequence[str],
    tmp_path: Path,
) -> ResolutionInvoker:
    """Return the scripted agent PROCESS behind the resolution chain.

    An agent named in ``resolving_agents`` repairs the conflicted file in
    ``tmp_path`` -- the temporary repository root the scenario owns -- and
    reports success; every other agent produces nothing and fails. The
    ``invocations`` list records the order in which production chose to
    run them.
    """
    resolving = frozenset(resolving_agents)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        if agent_name not in resolving:
            raise RuntimeError(f"agent '{agent_name}' produced no artifact")
        (tmp_path / CONFLICTED_PATH).write_text(RESOLVED_CONTENT, encoding="utf-8")
        return True

    return _invoke


def make_rebase_stop_resolver(
    *,
    ledger: AgentFailureLedger,
    clock: FrozenClock,
    policy_bundle: PolicyBundle,
    invoke: ResolutionInvoker,
    config: UnifiedConfig,
) -> RebaseStopResolver:
    """Return the resolver production uses for ONE paused rebase stop.

    It runs the shipped conflict-resolution pipeline, so the candidate
    chain, the selection, the benching and the demotion all stay in
    production. Only the agent PROCESS behind it is replaced.
    """

    # The resolution pipeline accepts the session as an explicit public
    # input. Constructing it with the shared ledger keeps the run's agent
    # health decisions on the same owner without mutating whichever
    # session the outer rebase loop happens to have active.
    session = ResolutionSession(ledger=ledger)

    def _resolver(root: Path, target: str, stop: RebaseStop) -> bool:
        return run_rebase_conflict_resolution_pipeline(
            root=root,
            target=target,
            stop=stop,
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=policy_bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            clock=clock.monotonic,
            session=session,
        )

    return _resolver
