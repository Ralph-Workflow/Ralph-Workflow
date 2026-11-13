from __future__ import annotations

import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast
from uuid import uuid4

from loguru import logger

import ralph.agents.invoke as agents_invoke
from ralph.agents.invoke import (
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
)
from ralph.agents.timeout_clock import SystemClock
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.git.operations import find_repo_root
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    register_entry_point,
    registered_entry_points,
)
from ralph.recovery.canary_probe_judge import (
    CanaryMarkerState,
    ProbeJudgeVerdict,
    judge_probe,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner import AttemptInvoke
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps, RawAgentInvoke
    from ralph.recovery.controller import RecoveryController

    class _AttemptRunnerModule(Protocol):
        def build_entry_point_recovery_controller(
            self,
            entry_point: AgentEntryPoint,
            **kwargs: object,
        ) -> RecoveryController: ...

        def run_agent_attempt(
            self,
            entry_point: AgentEntryPoint,
            **kwargs: object,
        ) -> AttemptResult: ...

    class _AttemptRunnerDepsModule(Protocol):
        AttemptRunnerDeps: type[AttemptRunnerDeps]

        def raw_attempt_invoke(self, invoke: RawAgentInvoke) -> AttemptInvoke: ...


def _run_canary_probe_attempt(
    *_args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    runner = cast(
        "_AttemptRunnerModule",
        import_module("ralph.pipeline.attempt_runner"),
    )
    return runner.run_agent_attempt(
        AgentEntryPoint.CANARY_PROBE,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


CANARY_MARKER_FILENAME: Final[str] = "ralph_canary_marker.txt"
CANARY_MARKER_CONTENT: Final[str] = "RALPH_CANARY_OK"
CANARY_PROBE_PHASE: Final[str] = "canary_probe"
CANARY_PROMPT_FILENAME: Final[str] = "canary_probe_prompt.md"
CANARY_PROBE_REPLY: Final[str] = "CANARY_WRITTEN"
DEFAULT_PROBE_TIMEOUT_S: Final[float] = 30
PROBE_INCONCLUSIVE_VERDICT: Final[str] = "unknown"
PROBE_AREA_RELATIVE_DIR: Final[str] = ".agent/tmp"
PROBE_DIR_PREFIX: Final[str] = "canary-probe-"

CANARY_PROBE_TASK: Final[str] = (
    "Canary probe task.\n"
    f"Create one file named {CANARY_MARKER_FILENAME} in the current working directory.\n"
    f"The entire content of that file must be exactly this text:\n{CANARY_MARKER_CONTENT}\n"
    "Create no other file, change no other file and run no other command.\n"
    f"When the file exists, answer with the single line {CANARY_PROBE_REPLY}."
)


class _ProbeOutcome(StrEnum):
    PASSED = "passed"
    FAILED_REPRODUCING_NO_WORK = "failed_reproducing_no_work"
    INCONCLUSIVE = "inconclusive"


_REQUIRED_PROBE_OUTCOME_VALUES: Final[tuple[str, ...]] = (
    "passed",
    "failed_reproducing_no_work",
    "inconclusive",
)

if tuple(outcome.value for outcome in _ProbeOutcome) != _REQUIRED_PROBE_OUTCOME_VALUES:
    raise RuntimeError(
        "ProbeOutcome must expose exactly passed, failed_reproducing_no_work, inconclusive"
    )


@dataclass(frozen=True)
class _ProbeResult:
    outcome: _ProbeOutcome
    detail: str
    elapsed_ms: int | None = None
    marker_written: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class _PreparedProbe:
    options: InvokeOptions
    prompt_file: str


@dataclass(frozen=True)
class _ProbeInvoke:
    result: AttemptResult | None
    error: BaseException | None
    timed_out: bool


ProbeOutcome = _ProbeOutcome
ProbeResult = _ProbeResult


def run_canary_probe(
    config: AgentConfig,
    *,
    invoke_fn: RawAgentInvoke | None = None,
    workspace_factory: Callable[[], Path] | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    general_config: GeneralConfig | None = None,
    extra_env: dict[str, str] | None = None,
    budget_exhausted: Callable[[], bool] | None = None,
    phase_workspace: Path | None = None,
    trusted_parent: Path | None = None,
) -> ProbeResult:
    started = time.monotonic()
    try:
        return _run_canary_probe(
            config,
            invoke_fn=invoke_fn,
            workspace_factory=workspace_factory,
            timeout_s=timeout_s,
            general_config=general_config,
            extra_env=extra_env,
            budget_exhausted=budget_exhausted,
            phase_workspace=phase_workspace,
            trusted_parent=trusted_parent,
            started=started,
        )
    except Exception as exc:
        return _inconclusive_result("probe_internal_error", started, error=_error_text(exc))


def _run_canary_probe(
    config: AgentConfig,
    *,
    invoke_fn: RawAgentInvoke | None,
    workspace_factory: Callable[[], Path] | None,
    timeout_s: float,
    general_config: GeneralConfig | None,
    extra_env: dict[str, str] | None,
    budget_exhausted: Callable[[], bool] | None,
    phase_workspace: Path | None,
    trusted_parent: Path | None,
    started: float,
) -> _ProbeResult:
    owns_workspace = workspace_factory is None
    workspace_path: Path | None = None
    timed_out = False
    try:
        preflight = _preflight_failure(timeout_s, budget_exhausted, started)
        if preflight is not None:
            return preflight
        workspace = _scratch_workspace(
            workspace_factory,
            phase_workspace,
            owns_workspace,
            trusted_parent,
            started,
        )
        if isinstance(workspace, _ProbeResult):
            return workspace
        workspace_path = workspace
        general = general_config if general_config is not None else GeneralConfig()
        prepared = _prepare_probe(
            config,
            workspace_path,
            general,
            extra_env,
            timeout_s,
            started,
        )
        if isinstance(prepared, _ProbeResult):
            return prepared
        invoke = invoke_fn if invoke_fn is not None else _production_invoke()
        attempt = _invoke_with_timeout(
            invoke,
            config,
            prepared,
            general,
            timeout_s,
        )
        timed_out = attempt.timed_out
        if attempt.timed_out:
            return _inconclusive_result("probe_timeout", started)
        if attempt.error is not None:
            return _inconclusive_result(
                "invocation_error", started, error=_error_text(attempt.error)
            )
        marker_state = _read_marker_state(workspace_path)
        facts = attempt.result.facts if attempt.result is not None else None
        return _verdict_result(judge_probe(marker_state, facts), started)
    finally:
        if owns_workspace and workspace_path is not None and not timed_out:
            _remove_scratch_workspace(workspace_path)


def _preflight_failure(
    timeout_s: float,
    budget_exhausted: Callable[[], bool] | None,
    started: float,
) -> _ProbeResult | None:
    if timeout_s <= 0:
        return _inconclusive_result("invalid_timeout", started)
    if budget_exhausted is None:
        return None
    try:
        exhausted = bool(budget_exhausted())
    except Exception as exc:
        return _inconclusive_result("budget_check_failed", started, error=_error_text(exc))
    if exhausted:
        return _inconclusive_result("budget_exhausted", started)
    return None


def _scratch_workspace(
    workspace_factory: Callable[[], Path] | None,
    phase_workspace: Path | None,
    owns_workspace: bool,
    trusted_parent: Path | None,
    started: float,
) -> Path | _ProbeResult:
    try:
        workspace_path = _create_scratch_workspace(workspace_factory, trusted_parent)
    except Exception as exc:
        return _inconclusive_result("workspace_creation_failed", started, error=_error_text(exc))
    if (
        phase_workspace is not None
        and _is_within(workspace_path, phase_workspace)
        and not _is_designated_probe_area(workspace_path, phase_workspace)
    ):
        if owns_workspace:
            _remove_scratch_workspace(workspace_path)
        return _inconclusive_result("scratch_inside_phase_workspace", started)
    return workspace_path


def _is_designated_probe_area(workspace_path: Path, phase_workspace: Path) -> bool:
    probe_area = phase_workspace / PROBE_AREA_RELATIVE_DIR
    try:
        workspace_path.relative_to(probe_area)
    except ValueError:
        return False
    return workspace_path.name.startswith(PROBE_DIR_PREFIX)


def _prepare_probe(
    config: AgentConfig,
    workspace_path: Path,
    general: GeneralConfig,
    extra_env: dict[str, str] | None,
    timeout_s: float,
    started: float,
) -> _PreparedProbe | _ProbeResult:
    try:
        options = _build_probe_options(
            config,
            workspace_path,
            general,
            extra_env,
            timeout_s,
        )
    except Exception as exc:
        return _inconclusive_result("options_build_failed", started, error=_error_text(exc))
    try:
        prompt_file = _write_prompt(workspace_path)
    except OSError as exc:
        return _inconclusive_result("prompt_write_failed", started, error=_error_text(exc))
    return _PreparedProbe(options=options, prompt_file=prompt_file)


def _create_scratch_workspace(
    factory: Callable[[], Path] | None,
    trusted_parent: Path | None,
) -> Path:
    if factory is not None:
        return Path(factory())
    parent = _resolve_trusted_parent(trusted_parent)
    if parent is None:
        return Path(tempfile.mkdtemp(prefix="ralph-canary-probe-"))
    probe_area = parent / PROBE_AREA_RELATIVE_DIR
    scratch = probe_area / f"{PROBE_DIR_PREFIX}{uuid4().hex}"
    scratch.mkdir(parents=True, exist_ok=False)
    return scratch


def _resolve_trusted_parent(trusted_parent: Path | None) -> Path | None:
    if trusted_parent is not None:
        return trusted_parent
    try:
        return find_repo_root(Path.cwd())
    except Exception as exc:
        logger.debug("canary probe trusted-parent resolution failed: {}", exc)
        return None


def _remove_scratch_workspace(workspace_path: Path) -> None:
    workspace = FsWorkspace(workspace_path)
    root = workspace.root
    if not root.is_dir():
        return
    with suppress(OSError):
        workspace.delete(str(root), recursive=True)


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _agent_name(config: AgentConfig) -> str | None:
    parts = config.cmd.split()
    return parts[0] if parts else None


def _build_probe_options(
    config: AgentConfig,
    workspace_path: Path,
    general: GeneralConfig,
    extra_env: dict[str, str] | None,
    timeout_s: float,
) -> InvokeOptions:
    runtime = InvokeRuntimeOptions(
        verbose=False,
        show_progress=False,
        workspace_path=workspace_path,
        extra_env=extra_env,
        phase=CANARY_PROBE_PHASE,
        agent_name=_agent_name(config),
        requires_completion_evidence=False,
    )
    options = build_invoke_options_from_config(general, runtime)
    return replace(options, idle_timeout_seconds=timeout_s, max_session_seconds=timeout_s)


def _write_prompt(workspace_path: Path) -> str:
    prompt_path = workspace_path / CANARY_PROMPT_FILENAME
    write_text_if_changed(DEFAULT_FILE_BACKEND, prompt_path, CANARY_PROBE_TASK, encoding="utf-8")
    return str(prompt_path)


def _invoke_with_timeout(
    invoke: RawAgentInvoke,
    config: AgentConfig,
    prepared: _PreparedProbe,
    general: GeneralConfig,
    timeout_s: float,
) -> _ProbeInvoke:
    captured: list[BaseException] = []
    finished: list[AttemptResult | None] = []

    def _run() -> None:
        try:
            finished.append(_run_probe_attempt(invoke, config, prepared, general))
        except BaseException as exc:
            captured.append(exc)

    thread = threading.Thread(target=_run, name="ralph-canary-probe", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return _ProbeInvoke(result=None, error=None, timed_out=True)
    return _ProbeInvoke(
        result=finished[0] if finished else None,
        error=captured[0] if captured else None,
        timed_out=False,
    )


def _production_invoke() -> RawAgentInvoke:
    return agents_invoke.invoke_agent


def _run_probe_attempt(
    invoke: RawAgentInvoke,
    config: AgentConfig,
    prepared: _PreparedProbe,
    general: GeneralConfig,
) -> AttemptResult | None:
    runner = cast(
        "_AttemptRunnerModule",
        import_module("ralph.pipeline.attempt_runner"),
    )
    runner_deps = cast(
        "_AttemptRunnerDepsModule",
        import_module("ralph.pipeline.attempt_runner_deps"),
    )
    agent_name = _agent_name(config)
    unified = UnifiedConfig(
        general=general,
        agents={agent_name: config} if agent_name is not None else {},
    )
    deps = runner_deps.AttemptRunnerDeps(
        clock=SystemClock(),
        invoke=runner_deps.raw_attempt_invoke(invoke),
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )
    controller = runner.build_entry_point_recovery_controller(
        AgentEntryPoint.CANARY_PROBE,
        config=unified,
        policy_bundle=None,
        deps=deps,
    )
    result = runner.run_agent_attempt(
        AgentEntryPoint.CANARY_PROBE,
        invocation=config,
        config=unified,
        controller=controller,
        deps=deps,
        phase=CANARY_PROBE_PHASE,
        agent=agent_name,
        prompt_file=prepared.prompt_file,
        options=prepared.options,
    )
    logger.info("canary probe attempt outcome: {}", result.outcome.kind)
    return result


if AgentEntryPoint.CANARY_PROBE not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.CANARY_PROBE,
        _run_canary_probe_attempt,
    )


def _read_marker_state(workspace_path: Path) -> CanaryMarkerState:
    marker_path = workspace_path / CANARY_MARKER_FILENAME
    try:
        content = marker_path.read_text(encoding="utf-8")
    except OSError:
        return CanaryMarkerState.MISSING
    if content.strip() == CANARY_MARKER_CONTENT:
        return CanaryMarkerState.MATCHED
    return CanaryMarkerState.MISMATCHED


def _verdict_result(verdict: ProbeJudgeVerdict, started: float) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome(verdict.outcome.value),
        detail=verdict.detail,
        elapsed_ms=_elapsed_ms(started),
        marker_written=verdict.marker_written,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _inconclusive_result(
    detail: str,
    started: float,
    *,
    error: str | None = None,
) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome.INCONCLUSIVE,
        detail=detail,
        elapsed_ms=_elapsed_ms(started),
        marker_written=None,
        error=error,
    )


__all__ = [
    "CANARY_MARKER_CONTENT",
    "CANARY_MARKER_FILENAME",
    "CANARY_PROBE_PHASE",
    "CANARY_PROBE_REPLY",
    "CANARY_PROBE_TASK",
    "CANARY_PROMPT_FILENAME",
    "DEFAULT_PROBE_TIMEOUT_S",
    "PROBE_INCONCLUSIVE_VERDICT",
    "ProbeOutcome",
    "ProbeResult",
    "run_canary_probe",
]
