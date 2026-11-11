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

CONFLICTED_PATH = "shared.txt"

RESOLUTION_CHAIN = "rebase_conflict_resolution"

TARGET = "main"

RESOLVED_CONTENT = "resolved by the resolver\n"

pytestmark = pytest.mark.subprocess_e2e


class FrozenClock:
    def __init__(self) -> None:
        self._now = 1_000.0

    def now(self) -> float:
        return self._now

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._now, tz=UTC)

    def epoch(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        del seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        del event, seconds
        return False


def make_conflicted_repo(tmp_path: Path) -> Path:
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
    return load_policy(default_dir())


def resolution_policy_bundle(candidates: Sequence[str]) -> PolicyBundle:
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
