from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest
from git import Repo
from loguru import logger

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

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
    "ScenarioRunner",
    "StopResolutionOutcome",
    "capture_operator_lines",
    "commit_file",
    "corrupted_config",
    "empty_resolution_stop_snapshot",
    "git",
    "paused_partial_out_of_reach_rebase",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "paused_three_commit_first_stop_rebase",
    "run_declared_decisions_and_gitlink_resolution",
    "run_detached_head_probe_quietness",
    "run_empty_resolution_stop",
    "run_empty_resolution_stop_anchor_ancestry",
    "run_empty_resolution_stop_landed_by_tree",
    "run_marker_resolution",
    "run_marker_resolution_bounded",
    "run_spent_chain_fall_over_then_wait",
    "run_three_commit_rebase_landed_anchor_chain",
    "run_three_commit_rebase_unrelated_head_rejected_once",
    "scenario_config",
    "shared_executor_seam",
    "supervisor_restart",
]

SCENARIO_FILES: tuple[str, ...] = (
    "tests/scenarios/test_auto_integrate_symptom_matrix.py",
    "tests/scenarios/test_rebase_recovery_matrix.py",
)

_SUPERVISOR_RESTART_TIMEOUT_SECONDS = 30


class ScenarioRunner(Protocol):
    def __call__(self, root: Path) -> ScenarioResult: ...


@dataclass
class FakeAgentRunner:
    mode: AgentMode
    launches: int = 0
    launch_times: list[float] = field(default_factory=list)
    completed_stops: list[str] = field(default_factory=list)
    clock: FakeClock | None = None
    fail_at_launch: int | None = None
    resolve_shared_txt_with_target: bool = False

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

    def handle_agent_effect(self, effect: object, *args: object, **kwargs: object) -> object:
        from ralph.pipeline.events import PipelineEvent

        del effect
        self.launches += 1
        self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
        workspace_scope = kwargs.get("workspace_scope")
        if workspace_scope is None and len(args) > 2:
            workspace_scope = args[2]
        root = getattr(workspace_scope, "root", Path.cwd())
        failure = self._failure_for_launch(root)
        if failure is not None:
            raise failure
        if self.mode is AgentMode.INVALID_RESULT:
            return PipelineEvent.AGENT_FAILURE
        if self.mode is AgentMode.LEAVES_MARKER:
            return PipelineEvent.AGENT_SUCCESS

        from ralph.git.merge import unmerged_paths

        unmerged = unmerged_paths(root)
        self._write_resolutions(root, unmerged)
        if self.mode is AgentMode.EDITS_AND_STAGES and unmerged:
            git(root, "add", "--", *unmerged)
        return PipelineEvent.AGENT_SUCCESS

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        injected = self._injected_agent_runner()
        if injected is not None:
            return self._invoke_shared_attempt_path(root)
        self.launches += 1
        self.launch_times.append(self.clock.monotonic() if self.clock is not None else 0.0)
        stop_identity = self._stop_identity(root)
        if stop_identity is not None:
            if stop_identity in self.completed_stops:
                raise AssertionError("a completed stop must never be presented to the agent again")
            failure = self._failure_for_launch(root)
            if failure is not None:
                raise failure
        if self.mode is AgentMode.INVALID_RESULT:
            return False
        if self.mode is AgentMode.LEAVES_MARKER:
            return True
        self._write_resolutions(root, tuple(stop.conflicted_files))
        if self.mode is AgentMode.EDITS_AND_STAGES:
            stageable = [
                relative for relative in stop.conflicted_files if (root / relative).is_file()
            ]
            if stageable:
                git(root, "add", "--", *stageable)
            if stop_identity is not None:
                self.completed_stops.append(stop_identity)
        return True

    def _injected_agent_runner(self) -> object | None:
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies

        injected = current_agent_effect_dependencies().agent_runner
        if injected is None:
            return None
        return injected if getattr(injected, "__self__", None) is self else None

    def _invoke_shared_attempt_path(self, root: Path) -> bool:
        from ralph.agents.system_clock import SystemClock
        from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies
        from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect
        from ralph.pipeline.events import PipelineEvent
        from ralph.workspace.scope import WorkspaceScope

        injected = current_agent_effect_dependencies().agent_runner
        if injected is None:
            raise RuntimeError("scenario agent runner dependency unavailable")
        deps = AttemptRunnerDeps(
            clock=self.clock or SystemClock(),
            invoke=injected,
            probe_runner=None,
            workspace_probe=None,
            connectivity=None,
        )
        event = deps.invoke(
            InvokeAgentEffect(
                agent_name="scenario-rebase-resolver",
                phase="rebase_conflict_resolution",
                prompt_file="prompt.md",
            ),
            workspace_scope=WorkspaceScope(root),
        )
        if event == PipelineEvent.AGENT_FAILURE:
            return False
        stop_identity = self._stop_identity(root)
        if stop_identity is not None:
            self.completed_stops.append(stop_identity)
        return True

    def _stop_identity(self, root: Path) -> str | None:
        if self.fail_at_launch is None and self.mode is AgentMode.EDITS_AND_STAGES:
            return None
        try:
            return git(root, "rev-parse", "REBASE_HEAD", check=False) or None
        except (AssertionError, OSError):
            return None

    def _failure_for_launch(self, root: Path) -> Exception | None:
        del root
        if self.fail_at_launch is not None and self.fail_at_launch == self.launches:
            return ConnectionError("agent provider unavailable partway through finite work")
        if self.mode is AgentMode.NON_ZERO_EXIT:
            return RuntimeError("agent exited with status 7")
        if self.mode is AgentMode.SIGNAL:
            return RuntimeError("agent terminated by signal 15")
        if self.mode is AgentMode.WATCHDOG_HANG:
            return TimeoutError("agent idle watchdog fired")
        if self.mode is AgentMode.PROVIDER_FAILURE:
            return ConnectionError("agent provider unavailable")
        return None

    def _write_resolutions(self, root: Path, relatives: Sequence[str]) -> None:
        for relative in relatives:
            path = root / relative
            if not path.is_file():
                continue
            if relative == "shared.txt" and (
                (root / "series.txt").exists() or self.resolve_shared_txt_with_target
            ):
                path.write_text("target value\n", encoding="utf-8")
            else:
                path.write_text("resolved by scenario agent\n", encoding="utf-8")


@contextmanager
def capture_operator_lines(display: ScenarioDisplay | None = None) -> Iterator[list[OperatorLine]]:
    lines: list[OperatorLine] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        level = record.get("level")
        level_name = getattr(level, "name", "INFO")
        content = str(record.get("message", ""))
        if level_name in {"WARNING", "ERROR", "CRITICAL"} or ("waiting" in content.lower()):
            lines.append(OperatorLine(level_name, content))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield lines
    finally:
        logger.remove(sink_id)
        if display is not None:
            lines.extend(display.lines)


@contextmanager
def shared_executor_seam(runner: FakeAgentRunner | None) -> Iterator[None]:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

    clock = getattr(runner, "clock", None) if runner is not None else None
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock or FakeClock()))
    agent_runner = getattr(runner, "handle_agent_effect", None) if runner is not None else None
    token = install_agent_effect_dependencies(
        agent_runner=agent_runner,
        recovery_controller=controller,
    )
    try:
        yield
    finally:
        reset_agent_effect_dependencies(token)


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


def _default_clock() -> FakeClock:
    from ralph.agents.timeout_clock import FakeClock

    return FakeClock()


def _init_identity_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    Repo.init(root, initial_branch="main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    readme = root / "README.md"
    readme.write_text("test\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "initial commit")


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
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature mixed change")
    git(root, "checkout", "-q", "main")
    (root / "shared.txt").write_text("target\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target mixed change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("mixed scenario fixture did not pause in a conflicted rebase")


def _build_partial_out_of_reach_paused(root: Path) -> tuple[str, str, str]:
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target link")
    commit_file(root, "deleted.txt", "base delete/modify\n", "declared base")
    commit_file(root, "notes.txt", "base notes\n", "notes base")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},vendor/link")
    git(root, "commit", "-q", "-m", "declared gitlink base")

    git(root, "checkout", "-q", "-b", "feature")
    (root / "deleted.txt").write_text("feature keeps file\n", encoding="utf-8")
    (root / "notes.txt").write_text("feature notes\n", encoding="utf-8")
    git(root, "add", "deleted.txt", "notes.txt")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature fourth symptom set")

    git(root, "checkout", "-q", "main")
    git(root, "rm", "-q", "deleted.txt")
    (root / "notes.txt").write_text("target notes\n", encoding="utf-8")
    git(root, "add", "notes.txt")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target fourth symptom set")

    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    index_info = (
        f"160000 {base_oid} 1\tvendor/link\n"
        f"160000 {target_oid} 2\tvendor/link\n"
        f"160000 {feature_oid} 3\tvendor/link\n"
    )
    subprocess.run(
        ("git", "update-index", "--index-info"),
        input=index_info,
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )
    if not rebase_in_progress(root):
        raise AssertionError("partial out of reach fixture did not pause")

    entries: dict[int, str] = {}
    for line in git(root, "ls-files", "-u", "--", "vendor/link").splitlines():
        metadata, _separator, _relative = line.partition("\t")
        fields = metadata.split()
        if len(fields) == 3:
            entries[int(fields[2])] = fields[1]
    s1, s2, s3 = entries.get(1, ""), entries.get(2, ""), entries.get(3, "")
    if not (s1 and s2 and s3):
        raise AssertionError(f"expected three gitlink stages for vendor/link, got {(s1, s2, s3)}")
    return (s1, s2, s3)


def _build_three_commit_paused(root: Path) -> None:
    commit_file(root, "shared.txt", "base\n", "scenario base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature stop 1\n", "feature commit 1 (conflicts)")
    for index in range(2, 4):
        commit_file(root, f"feat_{index}.txt", f"feature {index}\n", f"feature commit {index}")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("three-commit fixture did not pause at the first stop")


def _build_empty_resolution_paused(root: Path) -> tuple[str, str, str]:
    commit_file(root, "shared.txt", "base\n", "base")
    commit_file(root, "series.txt", "seed\n", "seed")
    git(root, "checkout", "-q", "-b", "feature")
    pre_rebase_head = commit_file(root, "shared.txt", "feature diff\n", "feature stop")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target value\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("empty-resolution fixture did not pause")
    resolved_tree = git(root, "rev-parse", "HEAD^{tree}")
    return (resolved_tree, git(root, "rev-parse", "HEAD"), pre_rebase_head)


_PARTIAL_OUT_OF_REACH_CACHE: list[tuple[Path, tuple[str, str, str]]] = []
_THREE_COMMIT_PAUSED_CACHE: list[Path] = []
_EMPTY_RESOLUTION_PAUSED_CACHE: list[tuple[Path, tuple[str, str, str]]] = []


def _replace_with_template(template: Path, root: Path) -> None:
    from tests.scenarios._integration_scenario_fixtures import replace_with_repo_template

    replace_with_repo_template(template, root)


def paused_partial_out_of_reach_rebase(root: Path) -> tuple[str, str, str]:
    from ralph.pipeline.auto_integrate_record import adopt_active_rebase_record

    if not _PARTIAL_OUT_OF_REACH_CACHE:
        seeded = Path(tempfile.mkdtemp(prefix="partial-oor-"))
        _init_identity_repo(seeded)
        _PARTIAL_OUT_OF_REACH_CACHE.append((seeded, _build_partial_out_of_reach_paused(seeded)))
    template, stage_shas = _PARTIAL_OUT_OF_REACH_CACHE[0]
    _replace_with_template(template, root)
    adopt_active_rebase_record(root, "main")
    return stage_shas


def paused_three_commit_first_stop_rebase(root: Path) -> None:
    if not _THREE_COMMIT_PAUSED_CACHE:
        seeded = Path(tempfile.mkdtemp(prefix="three-commit-"))
        _init_identity_repo(seeded)
        _build_three_commit_paused(seeded)
        _THREE_COMMIT_PAUSED_CACHE.append(seeded)
    _replace_with_template(_THREE_COMMIT_PAUSED_CACHE[0], root)


def empty_resolution_stop_snapshot(root: Path) -> tuple[str, str, str]:
    if not _EMPTY_RESOLUTION_PAUSED_CACHE:
        seeded = Path(tempfile.mkdtemp(prefix="empty-resolution-"))
        _init_identity_repo(seeded)
        _EMPTY_RESOLUTION_PAUSED_CACHE.append((seeded, _build_empty_resolution_paused(seeded)))
    template, meta = _EMPTY_RESOLUTION_PAUSED_CACHE[0]
    _replace_with_template(template, root)
    return meta


def supervisor_restart(root: Path) -> dict[str, object]:
    script = (
        "import json,pathlib,subprocess,sys; "
        "p=pathlib.Path(sys.argv[1]);sys.path.insert(0,sys.argv[2]); "
        "from ralph.pipeline.auto_integrate_planning import integrate_before_planning; "
        "from ralph.pipeline.rebase_state import RebaseState; "
        "from ralph.workspace.scope import WorkspaceScope; "
        "from tests._scenario_harness import scenario_config; "
        "s=integrate_before_planning(scenario_config(),WorkspaceScope(p),RebaseState()); "
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
        timeout=_SUPERVISOR_RESTART_TIMEOUT_SECONDS,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise TypeError("restart probe returned a non-object payload")
    return payload


def _resolution_succeeded(outcome: object) -> bool:
    resolved = outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
    return isinstance(resolved, ResolvedOutcome)


def _force_retry_stop(root: Path, key: str, runner: FakeAgentRunner) -> object:
    return attempt_stop_resolution(
        key,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target="main",
            resolver=runner,
            conflict_config=scenario_config().conflict_resolution,
            force_retry=True,
        ),
    )


def _anchor_chain(root: Path, base: str, head: str) -> tuple[str, ...]:
    chain = [head]
    current = head
    for _ in range(16):
        parents = git(root, "log", "--format=%P", "-n", "1", current).split()
        if not parents:
            break
        current = parents[0]
        chain.append(current)
        if current == base:
            break
    return tuple(chain)


def _with_verdict(
    lines: Sequence[OperatorLine], proven: bool, verdict: OperatorLine
) -> tuple[OperatorLine, ...]:
    return (*lines, verdict) if proven else tuple(lines)


def run_marker_resolution(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
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
        succeeded = _resolution_succeeded(outcome)
    terminal = "resolved" if succeeded else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_marker_resolution_bounded(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        for _ in range(2):
            attempt_stop_resolution(
                "scenario-marker-bounded",
                StopResolutionTrigger(
                    name="rebase_stop",
                    root=root,
                    target="main",
                    resolver=runner,
                    conflict_config=scenario_config().conflict_resolution,
                    force_retry=True,
                ),
            )
            if not rebase_in_progress(root):
                break
    bounded = runner.launches <= 2
    terminal = "retained" if bounded and rebase_in_progress(root) else "unbounded"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_spent_chain_fall_over_then_wait(root: Path) -> ScenarioResult:
    from ralph.agents.timeout_clock import FakeClock

    paused_text_rebase(root)
    clock = FakeClock(start=1000.0)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)

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
        with shared_executor_seam(runner), capture_operator_lines():
            integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
        launches_after_first = runner.launches
        clock.advance(1.0)
        with shared_executor_seam(runner), capture_operator_lines() as second_lines:
            state2 = integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
        second_launches = runner.launches - launches_after_first
    finally:
        reset_executors(token)
    waiting_lines = [
        line
        for line in second_lines
        if "provider" in line.content.lower() or "waiting" in line.content.lower()
    ]
    terminal = (
        "waiting"
        if rebase_in_progress(root)
        or (state2 is not None and getattr(state2, "last_decision", None) == "block_for_recovery")
        else "resolved"
    )
    return ScenarioResult(
        second_launches,
        tuple(waiting_lines or second_lines[:1]),
        terminal,
        {"next_launch_time": str(getattr(state2, "retry_not_before", ""))},
    )


def run_empty_resolution_stop(root: Path) -> ScenarioResult:
    commit_file(root, "shared.txt", "base\n", "base")
    commit_file(root, "series.txt", "seed\n", "seed")
    git(root, "checkout", "-q", "-b", "feature")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target value\n", "target change")
    git(root, "checkout", "-q", "feature")
    commit_file(root, "shared.txt", "feature diff\n", "feature stop that will resolve empty")
    commit_file(root, "series.txt", "seed\nsecond\n", "feature second commit")
    git(root, "rebase", "main", check=False)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    terminal = (
        "resolved"
        if not rebase_in_progress(root)
        and head == target
        and (root / "series.txt").read_text(encoding="utf-8") == "seed\nsecond\n"
        else "retained"
    )
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_detached_head_probe_quietness(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker

    with capture_operator_lines() as lines:
        marker = current_branch_or_detached_marker(root)
        restarted = supervisor_restart(root)
    quiet = len(lines) == 0 and restarted.get("status") is not None
    terminal = "quiet" if quiet else "noisy"
    return ScenarioResult(0, tuple(lines), terminal, {"marker": str(marker)})


def run_three_commit_rebase_landed_anchor_chain(root: Path) -> ScenarioResult:
    paused_three_commit_first_stop_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-multi-commit", runner)
    rebased_tip = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, rebased_tip)
    recorded_tree = git(root, "rev-parse", f"{rebased_tip}^{{tree}}")
    landed = (
        not rebase_in_progress(root)
        and rebased_tip != target
        and anchors[-1] == target
        and (root / "feat_3.txt").exists()
    )
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed rebase proven landed (observed HEAD {rebased_tip}, "
            f"anchor chain {'->'.join(anchors)}, recorded tree {recorded_tree})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {
            "rebased_tip": rebased_tip,
            "target": target,
            "anchor_chain": ",".join(anchors),
            "recorded_tree": recorded_tree,
        },
    )


def run_empty_resolution_stop_landed_by_tree(root: Path) -> ScenarioResult:
    resolved_tree, stop_anchor, pre_rebase_head = empty_resolution_stop_snapshot(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, resolve_shared_txt_with_target=True)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-empty-resolution", runner)
    head = git(root, "rev-parse", "HEAD")
    landed = (
        not rebase_in_progress(root)
        and head == stop_anchor
        and git(root, "rev-parse", f"{head}^{{tree}}") == resolved_tree
    )
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed empty-resolution rebase proven landed by tree "
            f"(observed HEAD {head}, stop anchor {stop_anchor}, "
            f"recorded tree {resolved_tree}, pre-rebase head {pre_rebase_head})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {
            "head": head,
            "stop_anchor": stop_anchor,
            "resolved_tree": resolved_tree,
            "pre_rebase_head": pre_rebase_head,
        },
    )


def run_empty_resolution_stop_anchor_ancestry(root: Path) -> ScenarioResult:
    empty_resolution_stop_snapshot(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, resolve_shared_txt_with_target=True)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-empty-resolution-ancestry", runner)
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, head)
    landed = not rebase_in_progress(root) and head == target
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed empty-resolution rebase proven landed by anchor ancestry "
            f"(observed HEAD {head}, anchor chain {'->'.join(anchors)})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {"head": head, "target": target, "anchor_chain": ",".join(anchors)},
    )


def run_three_commit_rebase_unrelated_head_rejected_once(root: Path) -> ScenarioResult:
    paused_three_commit_first_stop_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-unrelated-head", runner)
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, head)
    unrelated = git(root, "commit-tree", "HEAD^{tree}", "-m", "unrelated head")
    git(root, "update-ref", "HEAD", unrelated, head)
    unrelated_to_chain = (
        git(root, "rev-parse", "HEAD") == unrelated
        and unrelated not in anchors
        and not git(root, "merge-base", unrelated, head, check=False)
    )
    rejection_line = OperatorLine(
        "WARNING",
        (
            f"landing withheld: observed HEAD {unrelated} is unrelated to the recorded "
            f"anchor chain {'->'.join(anchors)}; expected HEAD {head}; "
            f"resume with git rebase --continue or land the recorded tip manually"
        ),
    )
    launches_before_ticks = runner.launches
    followup_lines: list[OperatorLine] = []
    with shared_executor_seam(runner):
        for tick in range(10):
            with capture_operator_lines() as tick_lines:
                _force_retry_stop(root, f"scenario-five-symptom-unrelated-head-tick-{tick}", runner)
            followup_lines.extend(tick_lines)
    quiet_afterwards = not followup_lines and runner.launches == launches_before_ticks
    terminal = "rejected-once" if unrelated_to_chain and quiet_afterwards else "repeats"
    return ScenarioResult(
        runner.launches,
        (*_with_verdict(lines, unrelated_to_chain, rejection_line), *followup_lines),
        terminal,
        {
            "observed_head": head,
            "unrelated_head": unrelated,
            "anchor_chain": ",".join(anchors),
        },
    )


def run_declared_decisions_and_gitlink_resolution(root: Path) -> ScenarioResult:
    from ralph.display.context import make_display_context
    from ralph.pipeline.conflict_resolution import (
        RebaseStop,
        run_rebase_conflict_resolution_outcome,
    )
    from ralph.pipeline.conflict_resolution.prompt import materialize_round_prompt
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.loader import default_dir, load_policy
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from tests.scenarios._content_equivalence import assert_content_equivalence

    target_before = git(root, "rev-parse", "main")
    stages = paused_partial_out_of_reach_rebase(root)
    from ralph.pipeline.auto_integrate_record import clear_record

    clear_record(root)
    feature_sha = git(root, "rev-parse", "feature")
    policy = load_policy(default_dir())
    controller = RecoveryController(options=RecoveryControllerOptions())
    session = ResolutionSession(recovery_controller=controller, ledger=controller.ledger)
    display = ScenarioDisplay()
    prompted: list[tuple[str, ...]] = []
    invoked: list[str] = []

    def invoke(agent_name: str, _prompt_path: Path, round_index: int) -> bool:
        invoked.append(f"{agent_name}:{round_index}")
        (root / "deleted.txt").write_text("resolved deleted\n", encoding="utf-8")
        (root / "notes.txt").write_text("resolved notes\n", encoding="utf-8")
        return True

    def materializer(
        *,
        root: Path,
        target: str,
        conflicted_paths: Sequence[str],
        round_index: int,
        round_cap: int,
        session: ResolutionSession,
        stop: RebaseStop | None,
        strategy_history: tuple[str, ...],
    ) -> Path | None:
        prompted.append(tuple(str(path) for path in conflicted_paths))
        return materialize_round_prompt(
            root=root,
            target=target,
            conflicted_paths=conflicted_paths,
            round_index=round_index,
            round_cap=round_cap,
            session=session,
            stop=stop,
            strategy_history=strategy_history,
        )

    stop = RebaseStop(
        sha=git(root, "rev-parse", "REBASE_HEAD"),
        subject="feature fourth symptom set",
        conflicted_files=("deleted.txt", "notes.txt", "vendor/link"),
        stop_index=1,
        stop_cap=4,
    )
    deps = PipelineDeps(
        display_context=make_display_context(),
        recovery_controller=controller,
        policy_bundle=policy,
    )
    with capture_operator_lines(display) as driver_lines:
        driver_outcome = run_rebase_conflict_resolution_outcome(
            root=root,
            target="main",
            stop=stop,
            config=scenario_config(),
            pipeline_deps=deps,
            workspace_scope=WorkspaceScope(root),
            policy_bundle=policy,
            display=display,
            display_context=None,
            invoke=invoke,
            session=session,
            prompt_materializer=materializer,
        )

    assert any(
        "entering rebase conflict resolution" in line.content
        and "(2 conflicted file(s))" in line.content
        for line in driver_lines
    )
    assert prompted
    assert all("vendor/link" not in paths for paths in prompted)
    assert any(
        "conflict(s) git could not write markers into" in line.content
        and "deleted.txt: delete/modify declaration" in line.content
        for line in driver_lines
    )

    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with shared_executor_seam(runner):
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    equivalence = assert_content_equivalence(
        root,
        contract="mixed declared-decision and policy paths must land without content loss",
        feature=feature_sha,
        source_refs=(target_before,),
        excluded_paths=("vendor/link",),
    )

    terminal = (
        "landed"
        if isinstance(driver_outcome, ResolvedOutcome)
        and head == target
        and state is not None
        and equivalence.satisfied
        else "retained"
    )
    assert terminal in {"landed", "waiting"}
    assert head == target
    assert equivalence.satisfied

    return ScenarioResult(
        runner.launches,
        tuple(driver_lines),
        terminal,
        {
            "stage_shas": ",".join(stages),
            "prompted_paths": ",".join(prompted[0] if prompted else ()),
            "driver_invocations": str(len(invoked)),
            "driver_resolved": str(isinstance(driver_outcome, ResolvedOutcome)).lower(),
            "head": head,
            "target": target,
        },
    )
