from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from loguru import logger

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult

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
    "ScenarioDisplay",
    "ScenarioResult",
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
    "spent_ledger_state",
    "supervisor_restart",
]

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_auto_integrate_symptom_matrix.py",)


class AgentMode(StrEnum):
    EDITS_AND_STAGES = "edits_and_stages"
    EDITS_NEVER_STAGES = "edits_never_stages"
    LEAVES_MARKER = "leaves_marker"
    NON_ZERO_EXIT = "non_zero_exit"
    SIGNAL = "signal"
    WATCHDOG_HANG = "watchdog_hang"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_RESULT = "invalid_result"


@dataclass(frozen=True)
class OperatorLine:
    level: str
    content: str


@dataclass
class FakeAgentRunner:
    mode: AgentMode
    launches: int = 0
    launch_times: list[float] = field(default_factory=list)
    clock: object | None = None

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: object,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        from ralph.pipeline._stop_resolution_entry import production_rebase_runner

        return production_rebase_runner().resolve_rebase(
            root, target, self, session=session
        )

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        now = getattr(self.clock, "monotonic", None)
        self.launch_times.append(cast("float", now()) if callable(now) else 0.0)
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


def git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def commit_file(root: Path, relative: str, content: str, message: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", "--", relative)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


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
        succeeded = bool(getattr(outcome, "succeeded", False))
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
        succeeded = bool(getattr(outcome, "succeeded", False))
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
