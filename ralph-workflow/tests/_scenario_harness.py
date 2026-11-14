from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

import ralph.pipeline._auto_integrate_rebase_resolve as _airr
import ralph.pipeline.conflict_resolution.round_verdict as _rv
import ralph.pipeline.resolution_outcome as _ro
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline._retry_later_outcome import RetryLaterOutcome as _FullRetryLaterOutcome
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_retry_later import RetryLater
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult

_airr.RetryLater = RetryLater
_airr.ConfigFailure = ConfigFailure
_rv.RetryLaterOutcome = _FullRetryLaterOutcome
_ro.RetryLaterOutcome = _FullRetryLaterOutcome

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.timeout_clock import FakeClock
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "SCENARIO_FILES",
    "AgentMode",
    "FakeAgentRunner",
    "OperatorLine",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "ScenarioDisplay",
    "ScenarioResult",
    "StopResolutionOutcome",
    "capture_operator_lines",
    "commit_file",
    "corrupted_config",
    "git",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "run_completed_rebase_promotion",
    "run_marker_resolution",
    "run_spent_gate",
    "run_unstaged_text_resolution",
    "scenario_config",
    "supervisor_restart",
]

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_auto_integrate_symptom_matrix.py",)


@dataclass
class FakeAgentRunner:
    mode: AgentMode
    launches: int = 0
    launch_times: list[float] = field(default_factory=list)
    completed_stops: list[str] = field(default_factory=list)
    clock: FakeClock | None = None
    fail_at_launch: int | None = None

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: object,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        from ralph.pipeline.integration_actions import default_agent_runner

        effective_resolver = resolver if callable(resolver) else self
        return default_agent_runner().resolve_rebase(
            root, target, effective_resolver, session=session
        )

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
        stop_identity: str | None = None
        if self.fail_at_launch is not None:
            stop_identity = git(root, "rev-parse", "REBASE_HEAD")
            if stop_identity in self.completed_stops:
                raise AssertionError("a completed stop must never be presented to the agent again")
            if self.fail_at_launch == self.launches:
                raise ConnectionError("agent provider unavailable partway through finite work")
        if self.mode is AgentMode.NON_ZERO_EXIT:
            raise RuntimeError("agent exited with status 7")
        if self.mode is AgentMode.SIGNAL:
            raise RuntimeError("agent terminated by signal 15")
        if self.mode is AgentMode.WATCHDOG_HANG:
            raise TimeoutError("agent idle watchdog fired")
        if self.mode is AgentMode.PROVIDER_FAILURE:
            raise ConnectionError("agent provider unavailable")
        if self.mode is AgentMode.INVALID_RESULT:
            return False
        if self.mode is AgentMode.LEAVES_MARKER:
            return True
        for relative in stop.conflicted_files:
            path = root / relative
            if path.is_file():
                path.write_text("resolved by scenario agent\n", encoding="utf-8")
        if self.mode is AgentMode.EDITS_AND_STAGES:
            git(root, "add", "--", *stop.conflicted_files)
            if stop_identity is not None:
                self.completed_stops.append(stop_identity)
        return True


@contextmanager
def capture_operator_lines(display: ScenarioDisplay | None = None) -> Iterator[list[OperatorLine]]:
    lines: list[OperatorLine] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        level = record.get("level")
        level_name = getattr(level, "name", "INFO")
        content = str(record.get("message", ""))
        if level_name in {"WARNING", "ERROR", "CRITICAL"}:
            lines.append(OperatorLine(level_name, content))

    sink_id = logger.add(sink, level="WARNING", format="{message}")
    try:
        yield lines
    finally:
        logger.remove(sink_id)
        if display is not None:
            lines.extend(display.lines)


def paused_text_rebase(root: Path) -> None:
    commit_file(root, "shared.txt", "base\n", "scenario base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature\n", "feature change")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("scenario fixture did not pause in a conflicted rebase")


def paused_text_and_gitlink_rebase(root: Path) -> None:
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target link")
    commit_file(root, "shared.txt", "base\n", "mixed base text")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},vendor/link")
    git(root, "commit", "-q", "-m", "mixed base link")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "shared.txt").write_text("feature\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature mixed change")
    git(root, "checkout", "-q", "main")
    (root / "shared.txt").write_text("target\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target mixed change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("mixed scenario fixture did not pause in a conflicted rebase")


def scenario_config() -> UnifiedConfig:
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            },
            "conflict_resolution": {
                "max_rounds_per_stop": 2,
                "max_rebase_conflict_stops": 4,
            },
        }
    )


def corrupted_config(root: Path) -> Path:
    config = root / ".agent" / "ralph-workflow.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[general\nauto_integrate_enabled = true\n", encoding="utf-8")
    return config


def spent_ledger_state(target: str = "main") -> tuple[RebaseState, ConflictIdentity]:
    identity = ConflictIdentity(
        feature_sha="feature",
        target_sha="target",
        conflicted_paths=("shared.txt",),
        stage_oids=("one", "two"),
    )
    state = RebaseState(
        last_target=target,
        resolution_exhausted=True,
        last_conflict_feature_sha=identity.feature_sha,
        last_conflict_target_sha=identity.target_sha,
        last_conflict_paths=identity.conflicted_paths,
        last_conflict_stage_oids=identity.stage_oids,
    )
    return state, identity


def supervisor_restart(root: Path) -> dict[str, object]:
    script = (
        "import json,pathlib,subprocess,sys; "
        "p=pathlib.Path(sys.argv[1]);sys.path.insert(0,sys.argv[2]); "
        "from ralph.pipeline.auto_integrate_planning import integrate_before_planning; "
        "from ralph.pipeline.rebase_state import RebaseState; "
        "from ralph.workspace.scope import WorkspaceScope; "
        "from tests._scenario_harness import scenario_config; "
        "s=integrate_before_planning(scenario_config(),WorkspaceScope(p),RebaseState(),"
        "conflict_resolver=lambda *_:False,rebase_stop_resolver=lambda *_:False); "
        "g=lambda *a: subprocess.run(('git',*a),cwd=p,text=True,capture_output=True,check=True).stdout.strip(); "
        "print(json.dumps({'head':g('rev-parse','HEAD'),'status':g('status','--porcelain'),"
        "'resumed':s is not None}))"
    )
    package_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        (sys.executable, "-I", "-c", script, str(root), str(package_root)),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise AssertionError("restart probe returned a non-object payload")
    return payload


def run_completed_rebase_promotion(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with capture_operator_lines() as lines:
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    terminal = (
        "promoted" if head == target and state is not None and state.fast_forwarded else "orphaned"
    )
    return ScenarioResult(runner.launches, tuple(lines), terminal, {"head": head, "target": target})


def run_unstaged_text_resolution(root: Path) -> ScenarioResult:
    paused_text_and_gitlink_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-unstaged-gitlink",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = isinstance(outcome, ResolvedOutcome)
    terminal = "resolved" if succeeded and not rebase_in_progress(root) else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_marker_resolution(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-marker",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = isinstance(outcome, ResolvedOutcome)
    terminal = "resolved" if succeeded else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_spent_gate(root: Path, clock: FakeClock, *, advance: float = 0.0) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)
    if advance:
        clock.advance(advance)

    class _IntegrationClock:
        def now(self) -> float:
            return clock.monotonic()

        def monotonic(self) -> float:
            return clock.monotonic()

        def epoch(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(agent_runner=runner, clock=_IntegrationClock())
    try:
        with capture_operator_lines() as lines:
            state = integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
    finally:
        reset_executors(token)
    terminal = "launched" if runner.launches else "waiting"
    facts = {
        "launch_times": ",".join(str(item) for item in runner.launch_times),
        "next_launch_time": str(getattr(state, "retry_not_before", "")),
    }
    return ScenarioResult(runner.launches, tuple(lines), terminal, facts)
