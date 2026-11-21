from __future__ import annotations

import dataclasses
import json
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.cli.commands import run as run_command
from ralph.cli.commands.run import RunPipelineRequest, load_resume_state
from ralph.config.enums import Verbosity
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import checkpoint, run_loop
from ralph.pipeline.auto_integrate_catchup import start_catchup_worker_if_enabled
from ralph.policy.loader import cached_default_policy_bundle
from ralph.project_policy.markers import OPT_OUT_MARKER
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.workspace.scope import resolve_workspace_scope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.plan_fixtures import (
    MINIMAL_PLAN_MARKDOWN,
    commit_review_markdown,
    development_result_markdown,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_catchup import AutoIntegrateCatchupWorker
    from ralph.pipeline.state import PipelineState

pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "TARGET",
    "WORK_FILE",
    "AgentCall",
    "Interrupt",
    "RunObserver",
    "RunOutcome",
    "SoakRun",
    "SoakViolation",
    "assert_soak_invariant",
    "git",
]

TARGET = "main"
FEATURE = "feature"
WORK_FILE = "src/example.py"
_GATE_POLICY = "docs/ralph-workflow-policy/verification-policy.md"
_GATE_FACT = "RALPH-FACT: authoritative_verify_command:"
_GIT_TIMEOUT_SECONDS = 60
_INTERRUPTED_EXIT = 130
_REPEAT_LIMIT = 5
_STALL_LIMIT = 40
_STEP_LIMIT = 300
_GENERIC_CAUSES = frozenset({"", "unknown error", "unknown failure", "error", "failed"})
_ENGINE_PATHS = (".agent/", "checkpoint.json")
_VOLATILE_STATE_KEYS = frozenset(
    {
        "metrics",
        "recovery_epoch",
        "ledger_snapshot",
        "agent_cooldowns",
        "phase_timings",
        "dev_timebox_consumed_seconds",
        "dev_timebox_started_at",
        "cycle_timebox_consumed_seconds",
        "cycle_timebox_started_at",
    }
)
_CRITERIA = (
    "parallel decomposition",
    "coverage",
    "truthfulness",
    "actionability",
    "execution conflicts",
)
_PLANNING_DECISION = (
    "---\ntype: planning_analysis_decision\nstatus: completed\n---\n\n## Summary\n\n"
    "- [SUM-1] Every planning criterion is met.\n\n## Criterion Verdicts\n\n"
    + "".join(
        f"- [PA-00{index}] Plan-level: Criterion: {criterion}. Expected observation: the "
        f"plan addresses {criterion}. Observation: the plan addresses {criterion}. Proposed "
        "revision: none. Verdict: met. Evidence: inspected src/example.py. Location: plan. "
        "Cost: none.\n"
        for index, criterion in enumerate(_CRITERIA, start=1)
    )
)
_DEVELOPMENT_DECISION = (
    "---\ntype: development_analysis_decision\nstatus: completed\n---\n\n"
    "The change is complete and verified.\n"
)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _fast_import_history(primary: Path, commits: int) -> None:
    parent = git(primary, "rev-parse", TARGET)
    stream = [f"reset refs/heads/{TARGET}\nfrom {parent}\n\n"]
    for index in range(commits):
        message = f"target history {index}"
        body = f"entry {index}\n"
        stream.append(
            f"commit refs/heads/{TARGET}\n"
            f"committer History <history@example.com> {1_700_000_000 + index} +0000\n"
            f"data {len(message)}\n{message}\n"
            f"M 100644 inline history/log.txt\ndata {len(body)}\n{body}\n"
        )
    stream.append("done\n")
    completed = subprocess.run(
        ("git", "fast-import", "--quiet", "--done"),
        cwd=primary,
        input="".join(stream),
        text=True,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    git(primary, "reset", "-q", "--hard", TARGET)


def _commit_message(subject: str) -> str:
    return (
        f"---\ntype: commit\nsubject: {subject}\n---\n## Body Summary\n"
        "- [BS-1] Complete the requested repository change.\n"
    )


class SoakViolation(KeyboardInterrupt):
    pass


@dataclass(frozen=True)
class AgentCall:
    phase: str
    nth: int
    prompt: str


@dataclass(frozen=True)
class Interrupt:
    kind: str
    phase: str
    nth: int = 1


@dataclass(frozen=True)
class _Progress:
    phase: str
    cycle: int
    target_sha: str
    head_sha: str


def _state_signature(state: PipelineState) -> str:
    payload = state.model_dump(mode="json")
    return json.dumps(
        {key: value for key, value in payload.items() if key not in _VOLATILE_STATE_KEYS},
        sort_keys=True,
        default=str,
    )


def _cycle(state: PipelineState) -> int:
    return sum(int(value) for value in state.outer_progress.values())


@dataclass
class RunObserver:
    soak: SoakRun
    interrupt_at: Interrupt | None = None
    steps: int = 0
    waits: int = 0
    interrupted_by: str | None = None
    violation: str | None = None
    last_state: PipelineState | None = None
    trail: list[str] = field(default_factory=list)
    signatures: Counter[tuple[str, str]] = field(default_factory=Counter)
    phase_entries: Counter[str] = field(default_factory=Counter)
    progress: _Progress | None = None
    since_progress: int = 0

    @property
    def interrupted(self) -> bool:
        return self.interrupted_by is not None

    @property
    def persisted_phase(self) -> str | None:
        if self.last_state is None or self.interrupted_by == "step":
            return None
        return str(self.last_state.phase)

    def notify(self, state: PipelineState) -> None:
        self._raise_if_violated()
        self.steps += 1
        phase = str(state.phase)
        previous = self.last_state
        self.last_state = state
        self.phase_entries[phase] += 1
        self.trail.append(f"step {self.steps}: {phase} {state.last_error or ''}".rstrip())
        key = (phase, _state_signature(state))
        self.signatures[key] += 1
        if self.signatures[key] > _REPEAT_LIMIT:
            self._violate(
                f"phase {phase} repeated one state {self.signatures[key]} times without an "
                "agent invocation or a state change"
            )
        if previous is None or str(previous.phase) != phase or _cycle(previous) != _cycle(state):
            self._record_progress(None)
        else:
            self._record_progress(
                _Progress(
                    phase,
                    _cycle(state),
                    self.soak.target_sha(),
                    git(self.soak.root, "rev-parse", "HEAD"),
                )
            )
        self._tick(phase)
        self._maybe_interrupt("step", phase, self.phase_entries[phase])

    def sleep(self, seconds: float) -> None:
        self._raise_if_violated()
        self.soak.clock.advance(seconds)
        self.waits += 1
        self.trail.append(f"wait {seconds:.1f}s")
        self._tick(f"wait {seconds:.1f}s")
        self._maybe_interrupt("sleep", "*", self.waits)

    def agent(self, call: AgentCall) -> None:
        self._raise_if_violated()
        self.trail.append(f"agent {call.phase} #{call.nth}")
        self.signatures.clear()
        self._maybe_interrupt("agent", call.phase, call.nth)

    def log(self, message: str) -> None:
        self.trail.append(message.strip())

    def _record_progress(self, progress: _Progress | None) -> None:
        if progress is None or progress != self.progress:
            self.progress = progress
            self.since_progress = 0

    def _maybe_interrupt(self, kind: str, phase: str, nth: int) -> None:
        if self.interrupt_at != Interrupt(kind, phase, nth):
            return
        self.interrupt_at = None
        self.interrupted_by = kind
        self.trail.append(f"interrupt during {kind} {phase} #{nth}")
        raise KeyboardInterrupt

    def _tick(self, label: str) -> None:
        self.since_progress += 1
        if self.since_progress > _STALL_LIMIT:
            self._violate(
                f"no phase, cycle, commit or landing progress within {_STALL_LIMIT} loop "
                f"steps (last: {label})"
            )
        if self.steps + self.waits > _STEP_LIMIT:
            self._violate(f"the run exceeded {_STEP_LIMIT} loop steps")

    def _violate(self, message: str) -> None:
        self.violation = message
        raise SoakViolation(message)

    def _raise_if_violated(self) -> None:
        if self.violation is not None:
            raise SoakViolation(self.violation)


@dataclass
class ScriptedAgent:
    soak: SoakRun
    observer: RunObserver
    calls: Counter[str] = field(default_factory=Counter)
    phases: list[str] = field(default_factory=list)

    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del config, _clock
        prompt_path = Path(prompt_file)
        if prompt_path.name == CANARY_PROMPT_FILENAME:
            return iter(["ok\n"])
        phase = (options.phase if options is not None else None) or prompt_path.stem
        self.calls[phase] += 1
        self.phases.append(phase)
        absolute = prompt_path if prompt_path.is_absolute() else self.soak.root / prompt_path
        prompt = absolute.read_text(encoding="utf-8") if absolute.is_file() else ""
        call = AgentCall(phase, self.calls[phase], prompt)
        self.observer.agent(call)
        hook = self.soak.hooks.get((phase, call.nth))
        if hook is None or not hook(self.soak, call):
            self._act(call)
        return iter([f"{phase} done\n"])

    def _act(self, call: AgentCall) -> None:
        root = self.soak.root
        artifacts = root / ".agent" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        if call.phase == "planning":
            (artifacts / "plan.md").write_text(MINIMAL_PLAN_MARKDOWN, encoding="utf-8")
        elif call.phase == "planning_analysis":
            (artifacts / "planning_analysis_decision.md").write_text(
                _PLANNING_DECISION, encoding="utf-8"
            )
        elif call.phase == "development_analysis":
            (artifacts / "development_analysis_decision.md").write_text(
                _DEVELOPMENT_DECISION, encoding="utf-8"
            )
        elif call.phase == "development":
            (root / WORK_FILE).write_text(f"VALUE = {call.nth}\n", encoding="utf-8")
            if "Publication gate" in call.prompt:
                (root / "gate-ok").write_text("fixed\n", encoding="utf-8")
            (artifacts / "development_result.md").write_text(
                development_result_markdown("example"), encoding="utf-8"
            )
        elif call.phase.endswith("commit_review"):
            (artifacts / "commit_review.md").write_text(
                commit_review_markdown((), tree_sha=self.soak.commit_review_tree()),
                encoding="utf-8",
            )
        elif call.phase.endswith("commit"):
            (artifacts / "commit_message.md").write_text(
                _commit_message(f"feat: soak change {call.nth}"), encoding="utf-8"
            )
        else:
            self.soak.resolve_conflicts()


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int | None
    error: KeyboardInterrupt | None
    observer: RunObserver
    phases: tuple[str, ...]
    final_state: PipelineState | None

    def describe(self) -> str:
        saved = self.final_state.phase if self.final_state is not None else None
        tail = "\n".join(self.observer.trail[-40:])
        return (
            f"exit={self.exit_code} error={self.error!r} violation={self.observer.violation}\n"
            f"checkpoint phase={saved} agent phases={list(self.phases)}\nlast trail:\n{tail}"
        )


@dataclass
class SoakRun:
    base: Path
    monkeypatch: pytest.MonkeyPatch
    gate: str = "`true`"
    hooks: dict[tuple[str, int], Callable[[SoakRun, AgentCall], bool | None]] = field(
        default_factory=dict
    )
    catchup_interval: float | None = None
    clock: FakeClock = field(default_factory=lambda: FakeClock(start=1_000_000.0))

    @property
    def primary(self) -> Path:
        return self.base / "primary"

    @property
    def root(self) -> Path:
        return self.base / "wt"

    def seed(self, *, history: int = 0) -> None:
        primary = self.primary
        primary.mkdir(parents=True)
        git(primary, "init", "-q", "-b", TARGET)
        for key, value in (
            ("user.name", "Soak"),
            ("user.email", "soak@example.com"),
            ("commit.gpgsign", "false"),
            ("gc.auto", "0"),
        ):
            git(primary, "config", key, value)
        files = {
            "AGENTS.md": f"# Agents\n\n{OPT_OUT_MARKER}\n",
            "PROMPT.md": "Implement the soak change.\n",
            WORK_FILE: "VALUE = 0\n",
            _GATE_POLICY: f"# Verification\n\n{_GATE_FACT} {self.gate}\n",
        }
        for relative, content in files.items():
            (primary / relative).parent.mkdir(parents=True, exist_ok=True)
            (primary / relative).write_text(content, encoding="utf-8")
        git(primary, "add", "--", *files)
        git(primary, "commit", "-q", "-m", "seed")
        fork = git(primary, "rev-parse", TARGET)
        if history:
            _fast_import_history(primary, history)
        git(primary, "worktree", "add", "-q", "-b", FEATURE, str(self.root), fork)
        config = self.root / ".agent" / "ralph-workflow.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "[general]\n"
            "auto_integrate_enabled = true\n"
            f'auto_integrate_target = "{TARGET}"\n'
            "auto_integrate_remote_enabled = false\n"
            "developer_iters = 1\n",
            encoding="utf-8",
        )

    def target_sha(self) -> str:
        return git(self.primary, "rev-parse", TARGET)

    def move_target(self, relative: str, content: str, message: str) -> None:
        path = self.primary / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(self.primary, "add", "--", relative)
        git(self.primary, "commit", "-q", "-m", message)

    def commit_review_tree(self) -> str:
        context = self.root / ".agent" / "tmp" / "commit_review_context.json"
        return str(json.loads(context.read_text(encoding="utf-8"))["tree_sha"])

    def resolve_conflicts(self) -> None:
        unmerged = git(self.root, "diff", "--name-only", "--diff-filter=U").splitlines()
        for relative in unmerged:
            (self.root / relative).write_text("resolved by the soak resolver\n", encoding="utf-8")
        if unmerged:
            git(self.root, "add", "--", *unmerged)

    def run(self, *, interrupt_at: Interrupt | None = None) -> RunOutcome:
        return self._drive(resume=False, cli=False, interrupt_at=interrupt_at)

    def resume(self, *, cli: bool = False, interrupt_at: Interrupt | None = None) -> RunOutcome:
        return self._drive(resume=True, cli=cli, interrupt_at=interrupt_at)

    def interrupt_and_resume(
        self,
        *interrupts: Interrupt,
        cli: bool = False,
        hold: Callable[[SoakRun], None] | None = None,
    ) -> RunOutcome:
        previous = self.run(interrupt_at=interrupts[0])
        for following in (*interrupts[1:], None):
            assert_interrupted(previous)
            if hold is not None:
                hold(self)
            resumed = self.resume(cli=cli, interrupt_at=following)
            assert_resumed_without_redoing(previous, resumed)
            previous = resumed
        return previous

    def _drive(self, *, resume: bool, cli: bool, interrupt_at: Interrupt | None) -> RunOutcome:
        observer = RunObserver(self, interrupt_at=interrupt_at)
        agent = ScriptedAgent(self, observer)
        deps = dataclasses.replace(
            make_test_pipeline_deps(
                make_display_context(),
                artifact_requirements_resolver=resolve_phase_required_artifact,
                phase_prompt_materializer=materialize_prompt_for_phase,
                invoke_agent=agent,
                recovery_clock=self.clock,
                validate_custom_mcp_servers_fn=lambda _root: 0,
            ),
            recovery_sleep=observer.sleep,
            catchup_worker_factory=self._catchup_worker,
            startup_rebase_resolver=None,
            auto_integrate_resolver=None,
            commit_effect_executor=None,
            has_uncommitted_changes=None,
        )

        def run_func(
            config: UnifiedConfig, initial_state: PipelineState | None, **_kwargs: object
        ) -> int:
            return run_loop.run(
                config,
                initial_state,
                pipeline_subscriber=observer,
                pipeline_deps=deps,
                verbosity=Verbosity.QUIET,
            )

        self.monkeypatch.chdir(self.root)
        self.monkeypatch.delenv("RALPH_WORKSPACE", raising=False)
        self.monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
        self.monkeypatch.setattr(run_command.state, "run_func", run_func)
        sink = logger.add(observer.log, level="WARNING", format="{level}: {message}")
        exit_code: int | None = None
        error: KeyboardInterrupt | None = None
        try:
            if cli:
                exit_code = run_command.run_pipeline(
                    RunPipelineRequest(resume=resume, verbosity=Verbosity.QUIET),
                    display_context=make_display_context(),
                )
            else:
                scope = resolve_workspace_scope(self.root)
                config = load_config(None, {}, workspace_scope=scope)
                exit_code = run_func(config, load_resume_state(scope) if resume else None)
        except KeyboardInterrupt as exc:
            error = exc
        finally:
            logger.remove(sink)
        return RunOutcome(
            exit_code,
            error,
            observer,
            tuple(agent.phases),
            checkpoint.load(checkpoint.run_state_path(self.root)),
        )

    def _catchup_worker(
        self, config: UnifiedConfig, root: Path
    ) -> AutoIntegrateCatchupWorker | None:
        if self.catchup_interval is None:
            return None
        return start_catchup_worker_if_enabled(config, root, interval_seconds=self.catchup_interval)


def assert_interrupted(outcome: RunOutcome) -> None:
    assert outcome.observer.violation is None, outcome.describe()
    assert outcome.error is None, outcome.describe()
    assert outcome.observer.interrupted, outcome.describe()
    assert outcome.exit_code == _INTERRUPTED_EXIT, outcome.describe()
    persisted = outcome.observer.persisted_phase
    saved = outcome.final_state.phase if outcome.final_state is not None else None
    assert persisted is None or saved == persisted, (
        f"the interrupt replaced the saved {persisted} checkpoint with {saved}\n"
        f"{outcome.describe()}"
    )


def assert_resumed_without_redoing(interrupted: RunOutcome, resumed: RunOutcome) -> None:
    order = tuple(cached_default_policy_bundle().pipeline.phases)
    saved = str(interrupted.final_state.phase) if interrupted.final_state is not None else None
    if saved not in order or not resumed.phases or resumed.phases[0] not in order:
        return
    assert order.index(resumed.phases[0]) >= order.index(saved), (
        f"the resumed run restarted at {resumed.phases[0]} instead of {saved}\n{resumed.describe()}"
    )


def _tracked_changes(root: Path) -> list[str]:
    status = git(root, "status", "--porcelain", "--untracked-files=no")
    return [
        line
        for line in status.splitlines()
        if not any(line[3:].startswith(prefix) for prefix in _ENGINE_PATHS)
    ]


def _reported_cause(state: PipelineState) -> str:
    rebase = state.rebase
    parts = (state.last_error, rebase.last_reason, rebase.publication_block)
    return " ".join(str(part) for part in parts if part)


def _is_landed(root: Path) -> bool:
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", "HEAD", TARGET),
        cwd=root,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


def assert_soak_invariant(
    soak: SoakRun, outcome: RunOutcome, *, unpublished_cause: str | None = None
) -> None:
    assert outcome.observer.violation is None, outcome.describe()
    assert outcome.error is None, f"uncaught exception\n{outcome.describe()}"
    assert outcome.exit_code is not None, outcome.describe()
    assert outcome.exit_code != _INTERRUPTED_EXIT or outcome.observer.interrupted, (
        f"the run reported an interrupt nobody sent\n{outcome.describe()}"
    )
    state = outcome.final_state
    assert state is not None, f"the run left no checkpoint\n{outcome.describe()}"
    if outcome.exit_code != 0:
        cause = (state.last_error or "").strip()
        assert cause.lower().rstrip(".") not in _GENERIC_CAUSES, (
            f"the run failed without an accurate cause: {cause!r}\n{outcome.describe()}"
        )
        return
    assert str(state.phase) == cached_default_policy_bundle().pipeline.terminal_phase, (
        outcome.describe()
    )
    landed = _is_landed(soak.root)
    changes = _tracked_changes(soak.root)
    if landed and not changes:
        assert unpublished_cause is None, (
            f"the run published although it should have reported {unpublished_cause!r}\n"
            f"{outcome.describe()}"
        )
        assert state.last_error is None, (
            f"the run completed and published its work but still reports the failure "
            f"{state.last_error!r}\n{outcome.describe()}"
        )
        return
    cause = _reported_cause(state)
    assert cause, (
        f"the run completed with unpublished commits (landed={landed}) or uncommitted "
        f"tracked changes {changes} and reported no reason\n{outcome.describe()}"
    )
    assert unpublished_cause is None or unpublished_cause in cause, (
        f"expected the reported cause to name {unpublished_cause!r}, got {cause!r}\n"
        f"{outcome.describe()}"
    )
