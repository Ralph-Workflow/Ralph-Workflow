from __future__ import annotations

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.git.merge import unmerged_paths
from ralph.pipeline.conflict_resolution import run_rebase_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import AgentChainConfig
from tests._scenario_agent_mode import AgentMode

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

CONFLICT_MARKER_CONTENT = "<<<<<<< HEAD\ntarget side\n=======\nfeature side\n>>>>>>> feature\n"

pytestmark = pytest.mark.subprocess_e2e


FakeAgentMode = AgentMode


_MODE_ALIASES: dict[str, AgentMode] = {
    "edits-and-stages": AgentMode.EDITS_AND_STAGES,
    "edits_and_stages": AgentMode.EDITS_AND_STAGES,
    "edits-but-never-stages": AgentMode.EDITS_NEVER_STAGES,
    "edits_never_stages": AgentMode.EDITS_NEVER_STAGES,
    "edits-never-stages": AgentMode.EDITS_NEVER_STAGES,
    "leaves-a-marker": AgentMode.LEAVES_MARKER,
    "leaves_marker": AgentMode.LEAVES_MARKER,
    "leaves-marker": AgentMode.LEAVES_MARKER,
    "non-zero exit": AgentMode.NON_ZERO_EXIT,
    "non-zero-exit": AgentMode.NON_ZERO_EXIT,
    "non_zero_exit": AgentMode.NON_ZERO_EXIT,
    "nonzero_exit": AgentMode.NON_ZERO_EXIT,
    "killed-by-signal": AgentMode.SIGNAL,
    "killed_by_signal": AgentMode.SIGNAL,
    "signal": AgentMode.SIGNAL,
    "hangs-until-watchdog": AgentMode.WATCHDOG_HANG,
    "hangs_until_watchdog": AgentMode.WATCHDOG_HANG,
    "watchdog_hang": AgentMode.WATCHDOG_HANG,
    "provider-failure": AgentMode.PROVIDER_FAILURE,
    "provider_failure": AgentMode.PROVIDER_FAILURE,
}


def coerce_fake_mode(mode: AgentMode | str) -> AgentMode:
    if isinstance(mode, AgentMode):
        return mode
    norm = str(mode).strip().lower()
    if norm in _MODE_ALIASES:
        return _MODE_ALIASES[norm]
    hyphenated = norm.replace("_", "-").replace(" ", "-")
    if hyphenated in _MODE_ALIASES:
        return _MODE_ALIASES[hyphenated]
    raise ValueError(f"Unknown fake agent mode: {mode}")


def failure_exception_for_mode(
    mode: AgentMode | str,
    agent_name: str = "agent",
) -> BaseException:
    m = coerce_fake_mode(mode)
    if m is AgentMode.NON_ZERO_EXIT:
        return AgentInvocationError(agent_name, 7, f"agent '{agent_name}' exited with status 7")
    if m is AgentMode.SIGNAL:
        return AgentInvocationError(
            agent_name, -15, f"agent '{agent_name}' terminated by signal 15"
        )
    if m is AgentMode.WATCHDOG_HANG:
        return AgentInactivityTimeoutError(agent_name, 30.0)
    if m is AgentMode.PROVIDER_FAILURE:
        return SupervisionInfrastructureError(
            agent_name, f"provider unavailable for agent '{agent_name}'"
        )
    raise ValueError(f"Mode {mode} is not an error exit mode")


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
    resolving_agents: Sequence[str] = (),
    tmp_path: Path,
    resolve_after_attempts: int = 1,
    mode: FakeAgentMode | str | None = None,
    agent_modes: Mapping[str, FakeAgentMode | str] | None = None,
) -> ResolutionInvoker:
    resolving = frozenset(resolving_agents)

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)

        active_mode = None
        if agent_modes and agent_name in agent_modes:
            active_mode = coerce_fake_mode(agent_modes[agent_name])
        elif mode is not None:
            active_mode = coerce_fake_mode(mode)

        unmerged = [p for p in unmerged_paths(tmp_path) if p != "<unmerged-path-query-failed>"]
        target_paths = [p for p in unmerged if (tmp_path / p).is_file()]
        if not target_paths:
            target_paths = [CONFLICTED_PATH]

        if active_mode is not None:
            if active_mode in (
                FakeAgentMode.NON_ZERO_EXIT,
                FakeAgentMode.SIGNAL,
                FakeAgentMode.WATCHDOG_HANG,
                FakeAgentMode.PROVIDER_FAILURE,
            ):
                raise failure_exception_for_mode(active_mode, agent_name)

            if active_mode is FakeAgentMode.LEAVES_MARKER:
                for p in target_paths:
                    (tmp_path / p).write_text(CONFLICT_MARKER_CONTENT, encoding="utf-8")
                return True

            if active_mode is FakeAgentMode.EDITS_NEVER_STAGES:
                for p in target_paths:
                    (tmp_path / p).write_text(RESOLVED_CONTENT, encoding="utf-8")
                return True

            if active_mode is FakeAgentMode.EDITS_AND_STAGES:
                for p in target_paths:
                    (tmp_path / p).write_text(RESOLVED_CONTENT, encoding="utf-8")
                    subprocess.run(
                        ("git", "add", "--", p),
                        cwd=tmp_path,
                        check=True,
                        capture_output=True,
                    )
                return True

        if agent_name not in resolving or invocations.count(agent_name) < resolve_after_attempts:
            raise RuntimeError(f"agent '{agent_name}' produced no artifact")
        for p in target_paths:
            (tmp_path / p).write_text(RESOLVED_CONTENT, encoding="utf-8")
        return True

    return _invoke


def make_mode_scripted_resolver_process(
    mode: FakeAgentMode | str,
    *,
    invocations: list[str],
    tmp_path: Path,
    agent_modes: Mapping[str, FakeAgentMode | str] | None = None,
    resolving_agents: Sequence[str] = (),
    resolve_after_attempts: int = 1,
) -> ResolutionInvoker:
    return make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=resolving_agents,
        tmp_path=tmp_path,
        resolve_after_attempts=resolve_after_attempts,
        mode=mode,
        agent_modes=agent_modes,
    )


def make_fake_mode_agent_runner(
    mode: AgentMode | str = AgentMode.EDITS_AND_STAGES,
    *,
    tmp_path: Path | None = None,
    clock: object | None = None,
    agent_modes: Mapping[str, AgentMode | str] | None = None,
) -> object:
    class _FakeModeAgentRunner:
        def __init__(self) -> None:
            self.mode = coerce_fake_mode(mode)
            self.tmp_path = tmp_path
            self.clock = clock
            self.agent_modes = dict(agent_modes) if agent_modes else {}
            self.launches = 0
            self.launch_times: list[float] = []
            self.invocations: list[str] = []

        def __call__(self, agent_name: str, prompt_path: Path, round_index: int) -> bool:
            del prompt_path, round_index
            self.launches += 1
            self.invocations.append(agent_name)
            if self.clock is not None and hasattr(self.clock, "monotonic"):
                self.launch_times.append(float(self.clock.monotonic()))
            m = self.agent_modes.get(agent_name, self.mode)
            m = coerce_fake_mode(m)
            if m in (
                AgentMode.NON_ZERO_EXIT,
                AgentMode.SIGNAL,
                AgentMode.WATCHDOG_HANG,
                AgentMode.PROVIDER_FAILURE,
            ):
                raise failure_exception_for_mode(m, agent_name)
            if self.tmp_path is not None:
                if m is AgentMode.LEAVES_MARKER:
                    (self.tmp_path / CONFLICTED_PATH).write_text(
                        CONFLICT_MARKER_CONTENT, encoding="utf-8"
                    )
                    return True
                if m is AgentMode.EDITS_NEVER_STAGES:
                    (self.tmp_path / CONFLICTED_PATH).write_text(RESOLVED_CONTENT, encoding="utf-8")
                    return True
                if m is AgentMode.EDITS_AND_STAGES:
                    (self.tmp_path / CONFLICTED_PATH).write_text(RESOLVED_CONTENT, encoding="utf-8")
                    repo = Repo(self.tmp_path)
                    repo.index.add([CONFLICTED_PATH])
                    return True
            return True

        def resolve_stop(self, root: Path, target: str, stop: RebaseStop) -> bool:
            del target, stop
            self.tmp_path = root
            return self("resolver", root / "prompt.txt", 1)

        def handle_agent_effect(self, effect: object, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.launches += 1
            agent_name = getattr(effect, "agent_name", "agent")
            self.invocations.append(agent_name)
            if self.clock is not None and hasattr(self.clock, "monotonic"):
                self.launch_times.append(float(self.clock.monotonic()))
            m = self.agent_modes.get(agent_name, self.mode)
            m = coerce_fake_mode(m)
            if m in (
                AgentMode.NON_ZERO_EXIT,
                AgentMode.SIGNAL,
                AgentMode.WATCHDOG_HANG,
                AgentMode.PROVIDER_FAILURE,
            ):
                raise failure_exception_for_mode(m, agent_name)
            from ralph.pipeline.events import PipelineEvent

            return PipelineEvent.AGENT_SUCCESS

    return _FakeModeAgentRunner()


def make_rebase_stop_resolver(
    *,
    ledger: AgentFailureLedger,
    clock: FrozenClock,
    policy_bundle: PolicyBundle,
    invoke: ResolutionInvoker,
    config: UnifiedConfig,
) -> RebaseStopResolver:
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

    controller = getattr(ledger, "recovery_controller", None)
    if not isinstance(controller, RecoveryController):
        controller = RecoveryController(
            options=RecoveryControllerOptions(
                clock=clock,
                ledger=ledger,
                policy_bundle=policy_bundle,
            )
        )
    session = ResolutionSession(ledger=ledger, recovery_controller=controller)

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
