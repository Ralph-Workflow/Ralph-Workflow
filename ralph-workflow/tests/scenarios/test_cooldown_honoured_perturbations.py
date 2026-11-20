from __future__ import annotations

import functools
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.merge import unmerged_paths
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import CleanupLadderRung, cleanup_action_for
from ralph.pipeline.integration_ladder import resume_rung
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import git
from tests._scenario_harness import paused_text_rebase
from tests.pipeline._auto_integrate_agent_failure_fixtures import resolution_policy_bundle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.policy.models import PolicyBundle

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

_RESOLUTION_AGENT = "claude"
_PHASE_RESOLUTION = "rebase_conflict_resolution"
_TARGET = "main"
_RESTATED_SUBJECT = "restated scope: resolve the same conflict on a different ask"
_SWITCHED_STRATEGY = "rebase_resolver: unresolved shared.txt"


class _IntegrationClock:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def now(self) -> float:
        return self._clock.monotonic()

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def epoch(self) -> float:
        return self._clock.epoch()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)


@dataclass
class _RecordingResolverAgent:
    clock: FakeClock
    failures: int = 1
    launches: list[tuple[str, float]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def __call__(self, effect: object, *args: object, **kwargs: object) -> object:
        agent_name = str(getattr(effect, "agent_name", ""))
        self.launches.append((agent_name, self.clock.monotonic()))
        prompt_file = getattr(effect, "prompt_file", "")
        if isinstance(prompt_file, str) and prompt_file:
            self.prompts.append(Path(prompt_file).read_text(encoding="utf-8"))
        if len(self.launches) <= self.failures:
            raise AgentInvocationError(agent_name, 7, f"agent '{agent_name}' exited with status 7")
        workspace_scope = kwargs.get("workspace_scope")
        if workspace_scope is None and len(args) > 2:
            workspace_scope = args[2]
        root = Path(getattr(workspace_scope, "root", Path.cwd()))
        unmerged = unmerged_paths(root)
        for relative in unmerged:
            candidate = root / relative
            if candidate.is_file():
                candidate.write_text("resolved by scenario agent\n", encoding="utf-8")
        if unmerged:
            git(root, "add", "--", *unmerged)
        return PipelineEvent.AGENT_SUCCESS

    @property
    def launch_count(self) -> int:
        return len(self.launches)

    @property
    def last_prompt(self) -> str:
        return self.prompts[-1] if self.prompts else ""


@dataclass
class _CooldownScenario:
    root: Path
    clock: FakeClock
    controller: RecoveryController
    agent: _RecordingResolverAgent
    config: UnifiedConfig
    bundle: PolicyBundle
    display: ScenarioDisplay
    session: ResolutionSession

    def operator_lines(self) -> tuple[str, ...]:
        return tuple(line.content for line in self.display.lines)

    def new_operator_lines(self, since: int) -> tuple[str, ...]:
        return tuple(line.content for line in self.display.lines[since:])


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": False,
            },
            "conflict_resolution": {
                "max_rounds_per_stop": 2,
                "max_rebase_conflict_stops": 4,
            },
        }
    )


def _controller(clock: FakeClock, bundle: PolicyBundle) -> RecoveryController:
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=bundle,
        )
    )


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="cooldown-perturbation-paused-"))
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()
    paused_text_rebase(base)
    return base


def _materialize_paused_rebase(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _paused_rebase_template(),
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _scenario(tmp_path: Path) -> _CooldownScenario:
    root = _materialize_paused_rebase(tmp_path / "repo")
    clock = FakeClock(start=1000.0)
    bundle = resolution_policy_bundle((_RESOLUTION_AGENT,))
    controller = _controller(clock, bundle)
    return _CooldownScenario(
        root=root,
        clock=clock,
        controller=controller,
        agent=_RecordingResolverAgent(clock=clock),
        config=_config(),
        bundle=bundle,
        display=ScenarioDisplay(),
        session=ResolutionSession(ledger=controller.ledger, recovery_controller=controller),
    )


@contextmanager
def _seams(scenario: _CooldownScenario) -> Iterator[None]:
    executors_token = install_executors(clock=_IntegrationClock(scenario.clock))
    deps_token = install_agent_effect_dependencies(
        agent_runner=scenario.agent, recovery_controller=scenario.controller
    )
    try:
        yield
    finally:
        reset_agent_effect_dependencies(deps_token)
        reset_executors(executors_token)


def _drive(
    scenario: _CooldownScenario,
    *,
    restate: bool = False,
    strategy_history: tuple[str, ...] = (),
) -> object:
    def _resolver(resolver_root: Path, target: str, stop: RebaseStop) -> bool:
        effective_stop = stop
        if restate:
            effective_stop = replace(stop, subject=_RESTATED_SUBJECT)
        return isinstance(
            run_rebase_conflict_resolution_outcome(
                root=resolver_root,
                target=target,
                stop=effective_stop,
                config=scenario.config,
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=scenario.bundle,
                display=scenario.display,
                display_context=None,
                clock=scenario.clock.monotonic,
                session=scenario.session,
                strategy_history=strategy_history,
            ),
            ResolvedOutcome,
        )

    with _seams(scenario):
        return attempt_stop_resolution(
            "cooldown-perturbation",
            StopResolutionTrigger(
                name="rebase_stop",
                root=scenario.root,
                target=_TARGET,
                resolver=_resolver,
                conflict_config=scenario.config.conflict_resolution,
                force_retry=True,
            ),
        )


def _shared_cooldown_expiry(scenario: _CooldownScenario) -> float:
    status = scenario.controller.agent_eligibility(_PHASE_RESOLUTION, _RESOLUTION_AGENT)
    assert isinstance(status, Cooldown), (
        f"the shared cooldown system must hold the failed resolver, got {status!r}"
    )
    expiry_seconds = status.until_ms / 1000.0
    assert expiry_seconds > scenario.clock.monotonic(), (
        "the shared cooldown must expire in the future on the fake clock"
    )
    snapshot = scenario.controller.snapshot()
    assert snapshot["unavailable_timeouts"][_RESOLUTION_AGENT] == status.until_ms, (
        "the shared cooldown record must carry the expiry the resolver was gated on"
    )
    assert snapshot["unavailability_reasons"][_RESOLUTION_AGENT] is not None
    return expiry_seconds


def _assert_durable_resume_point(scenario: _CooldownScenario) -> None:
    snapshot = scenario.controller.snapshot()
    epoch_ms = snapshot["unavailable_until_epoch_ms"][_RESOLUTION_AGENT]
    expected_until = (
        datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).replace(microsecond=0).isoformat()
    )
    cooldown_lines = [content for content in scenario.operator_lines() if "on cooldown" in content]
    assert cooldown_lines, (
        "the operator-visible status must report the resolver's cooldown, got "
        f"{scenario.operator_lines()}"
    )
    assert any(
        f"next-eligible: {_RESOLUTION_AGENT}" in content and expected_until in content
        for content in cooldown_lines
    ), (
        "the operator-visible status must name the cooling resolver and its expiry "
        f"{expected_until}, got {cooldown_lines}"
    )


def _assert_not_relaunched_while_cooling(
    scenario: _CooldownScenario,
    launches_before: int,
    lines_before: int,
) -> None:
    assert scenario.agent.launch_count == launches_before, (
        "a cooling resolver must not be relaunched before its cooldown elapses, "
        f"launches were {scenario.agent.launches}"
    )
    reported = scenario.new_operator_lines(lines_before)
    assert any("on cooldown" in content for content in reported), (
        "the operator-visible status must report that the resolver is still cooling, "
        f"got {reported}"
    )


def _assert_relaunched_once_after_expiry(
    scenario: _CooldownScenario,
    launches_before: int,
    expiry_seconds: float,
) -> None:
    assert scenario.agent.launch_count == launches_before + 1, (
        "the resolver must be relaunched exactly once after its cooldown elapses, "
        f"launches were {scenario.agent.launches}"
    )
    last_launch = scenario.agent.launches[-1][1]
    assert last_launch >= expiry_seconds, (
        f"the relaunch happened at {last_launch} before the cooldown expiry {expiry_seconds}"
    )


def _move_target(root: Path) -> str:
    repo = Repo(root)
    try:
        old = repo.commit(_TARGET).hexsha
        parent = repo.commit(f"{old}^").hexsha
        tree = repo.commit(parent).tree.hexsha
        moved = repo.git.commit_tree(tree, "-p", parent, "-m", "target moved")
        repo.git.update_ref(f"refs/heads/{_TARGET}", moved, old)
        return moved
    finally:
        repo.close()


def _target_sha(root: Path) -> str:
    repo = Repo(root)
    try:
        return repo.commit(_TARGET).hexsha
    finally:
        repo.close()


def _advance_cleanup_ladder(root: Path) -> tuple[CleanupLadderRung | None, ...]:
    ledger = AttemptLedger(root)
    before = resume_rung(ledger.read())
    state = ledger.read()
    evidence = dict(state.ladder_evidence)
    key = f"rung:{cleanup_action_for(CleanupLadderRung.NORMAL_CLEANUP).value}"
    evidence[key] = "failed:the resolver is cooling down"
    ledger.persist(replace(state, ladder_evidence=evidence))
    return before, resume_rung(ledger.read())


def test_cooldown_honoured_when_the_problem_is_restated(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _drive(scenario, restate=True)

    assert scenario.agent.launch_count == 1, (
        f"the failing resolver must have been launched once, got {scenario.agent.launches}"
    )
    assert _RESTATED_SUBJECT in scenario.agent.last_prompt, (
        "the restated problem must have been the one the resolver was asked to solve"
    )
    expiry_seconds = _shared_cooldown_expiry(scenario)
    _assert_durable_resume_point(scenario)

    launches_before = scenario.agent.launch_count
    lines_before = len(scenario.display.lines)
    _drive(scenario, restate=True)

    assert _RESTATED_SUBJECT in scenario.agent.last_prompt, (
        "the restated problem must still be the outstanding ask while the resolver cools"
    )
    _assert_not_relaunched_while_cooling(scenario, launches_before, lines_before)

    scenario.clock.advance(expiry_seconds - scenario.clock.monotonic() + 1.0)
    _drive(scenario, restate=True)

    _assert_relaunched_once_after_expiry(scenario, launches_before, expiry_seconds)


def test_cooldown_honoured_when_the_cleanup_ladder_changes_rung(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _drive(scenario)

    assert scenario.agent.launch_count == 1
    expiry_seconds = _shared_cooldown_expiry(scenario)
    _assert_durable_resume_point(scenario)

    launches_before = scenario.agent.launch_count
    lines_before = len(scenario.display.lines)
    rung_before, rung_after = _advance_cleanup_ladder(scenario.root)

    assert rung_before is not None and rung_after is not None
    assert rung_before != rung_after, (
        f"the cleanup ladder must move to another rung, stayed at {rung_before}"
    )

    _drive(scenario)

    _assert_not_relaunched_while_cooling(scenario, launches_before, lines_before)
    assert (
        scenario.controller.agent_eligibility(_PHASE_RESOLUTION, _RESOLUTION_AGENT).is_eligible
        is False
    ), "the shared cooldown must still gate the resolver after the ladder moved rung"

    scenario.clock.advance(expiry_seconds - scenario.clock.monotonic() + 1.0)
    _drive(scenario)

    _assert_relaunched_once_after_expiry(scenario, launches_before, expiry_seconds)


def test_cooldown_honoured_when_the_strategy_switches(tmp_path: Path) -> None:
    history = (_SWITCHED_STRATEGY,)
    scenario = _scenario(tmp_path)
    _drive(scenario, strategy_history=history)

    assert scenario.agent.launch_count == 1
    assert _SWITCHED_STRATEGY in scenario.agent.last_prompt, (
        "the switched strategy must have been handed to the resolver prompt"
    )
    expiry_seconds = _shared_cooldown_expiry(scenario)
    _assert_durable_resume_point(scenario)

    launches_before = scenario.agent.launch_count
    lines_before = len(scenario.display.lines)
    _drive(scenario, strategy_history=history)

    assert _SWITCHED_STRATEGY in scenario.agent.last_prompt, (
        "the switched strategy must still be the outstanding ask while the resolver cools"
    )
    _assert_not_relaunched_while_cooling(scenario, launches_before, lines_before)

    scenario.clock.advance(expiry_seconds - scenario.clock.monotonic() + 1.0)
    _drive(scenario, strategy_history=history)

    _assert_relaunched_once_after_expiry(scenario, launches_before, expiry_seconds)


def test_cooldown_honoured_when_the_target_moves(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _drive(scenario)

    assert scenario.agent.launch_count == 1
    expiry_seconds = _shared_cooldown_expiry(scenario)
    _assert_durable_resume_point(scenario)

    launches_before = scenario.agent.launch_count
    lines_before = len(scenario.display.lines)
    target_before = _target_sha(scenario.root)
    moved = _move_target(scenario.root)
    target_after = _target_sha(scenario.root)

    assert target_after != target_before, "the target must have moved"
    assert target_after == moved

    _drive(scenario)

    assert _target_sha(scenario.root) == moved, "the moved target must stay moved"
    _assert_not_relaunched_while_cooling(scenario, launches_before, lines_before)

    scenario.clock.advance(expiry_seconds - scenario.clock.monotonic() + 1.0)
    _drive(scenario)

    _assert_relaunched_once_after_expiry(scenario, launches_before, expiry_seconds)


def test_cooldown_honoured_across_a_supervisor_restart(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _drive(scenario)

    assert scenario.agent.launch_count == 1
    expiry_seconds = _shared_cooldown_expiry(scenario)
    _assert_durable_resume_point(scenario)

    launches_before = scenario.agent.launch_count
    lines_before = len(scenario.display.lines)
    durable_snapshot = scenario.controller.snapshot()
    restarted = RecoveryController.from_snapshot(
        durable_snapshot,
        policy_bundle=scenario.bundle,
        clock=scenario.clock,
    )
    scenario.controller = restarted
    scenario.session = ResolutionSession(ledger=restarted.ledger, recovery_controller=restarted)

    status = restarted.agent_eligibility(_PHASE_RESOLUTION, _RESOLUTION_AGENT)
    assert isinstance(status, Cooldown), (
        f"the durable cooldown must survive a supervisor restart, got {status!r}"
    )

    _drive(scenario)

    _assert_not_relaunched_while_cooling(scenario, launches_before, lines_before)

    scenario.clock.advance(expiry_seconds - scenario.clock.monotonic() + 1.0)
    _drive(scenario)

    _assert_relaunched_once_after_expiry(scenario, launches_before, expiry_seconds)
