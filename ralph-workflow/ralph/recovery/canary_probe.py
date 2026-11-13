from __future__ import annotations

import tempfile
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast

from loguru import logger

from ralph.agents.invoke import (
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
)
from ralph.agents.timeout_clock import SystemClock
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    EntryPointRunner,
    register_entry_point,
    registered_entry_points,
)
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner import AttemptInvoke
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
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
        PRODUCTION_INVOKE: AttemptInvoke
        AttemptRunnerDeps: type[AttemptRunnerDeps]


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


class _InvokeAgentFn(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None: ...


ProbeOutcome = _ProbeOutcome
ProbeResult = _ProbeResult


def run_canary_probe(
    config: AgentConfig,
    *,
    invoke_fn: _InvokeAgentFn | None = None,
    workspace_factory: Callable[[], Path] | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    general_config: GeneralConfig | None = None,
    extra_env: dict[str, str] | None = None,
    budget_exhausted: Callable[[], bool] | None = None,
    phase_workspace: Path | None = None,
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
            started=started,
        )
    except Exception as exc:
        return _inconclusive_result("probe_internal_error", started, error=_error_text(exc))


def _run_canary_probe(
    config: AgentConfig,
    *,
    invoke_fn: _InvokeAgentFn | None,
    workspace_factory: Callable[[], Path] | None,
    timeout_s: float,
    general_config: GeneralConfig | None,
    extra_env: dict[str, str] | None,
    budget_exhausted: Callable[[], bool] | None,
    phase_workspace: Path | None,
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
        error, timed_out = _invoke_with_timeout(
            invoke,
            config,
            prepared,
            general,
            timeout_s,
        )
        if timed_out:
            return _inconclusive_result("probe_timeout", started)
        if error is not None:
            return _inconclusive_result("invocation_error", started, error=_error_text(error))
        return _judge_marker(workspace_path, started)
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
    started: float,
) -> Path | _ProbeResult:
    try:
        workspace_path = _create_scratch_workspace(workspace_factory)
    except Exception as exc:
        return _inconclusive_result("workspace_creation_failed", started, error=_error_text(exc))
    if phase_workspace is not None and _is_within(workspace_path, phase_workspace):
        if owns_workspace:
            _remove_scratch_workspace(workspace_path)
        return _inconclusive_result("scratch_inside_phase_workspace", started)
    return workspace_path


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


def _create_scratch_workspace(factory: Callable[[], Path] | None) -> Path:
    if factory is None:
        return Path(tempfile.mkdtemp(prefix="ralph-canary-probe-"))
    return Path(factory())


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
    invoke: _InvokeAgentFn,
    config: AgentConfig,
    prepared: _PreparedProbe,
    general: GeneralConfig,
    timeout_s: float,
) -> tuple[BaseException | None, bool]:
    captured: list[BaseException] = []

    def _run() -> None:
        try:
            _run_probe_attempt(invoke, config, prepared, general)
        except BaseException as exc:
            captured.append(exc)

    thread = threading.Thread(target=_run, name="ralph-canary-probe", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return (None, True)
    return (captured[0] if captured else None, False)


def _production_invoke() -> _InvokeAgentFn:
    module = cast(
        "_AttemptRunnerDepsModule",
        import_module("ralph.pipeline.attempt_runner_deps"),
    )
    return cast("_InvokeAgentFn", module.PRODUCTION_INVOKE)


def _probe_invoke(invoke: _InvokeAgentFn) -> AttemptInvoke:
    def _invoke(target: object, *args: object, **kwargs: object) -> object:
        del args
        result = invoke(
            cast("AgentConfig", target),
            prompt_file=str(kwargs["prompt_file"]),
            options=cast("InvokeOptions | None", kwargs.get("options")),
        )
        if result is not None:
            deque(result, maxlen=0)
        return PipelineEvent.AGENT_SUCCESS

    return _invoke


def _run_probe_attempt(
    invoke: _InvokeAgentFn,
    config: AgentConfig,
    prepared: _PreparedProbe,
    general: GeneralConfig,
) -> None:
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
        invoke=_probe_invoke(invoke),
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


if AgentEntryPoint.CANARY_PROBE not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.CANARY_PROBE,
        cast("EntryPointRunner", _run_canary_probe_attempt),
    )


def _judge_marker(workspace_path: Path, started: float) -> _ProbeResult:
    marker_path = workspace_path / CANARY_MARKER_FILENAME
    try:
        content = marker_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _failed_result("marker_missing", started)
    except OSError as exc:
        return _inconclusive_result("marker_read_failed", started, error=_error_text(exc))
    if content.strip() == CANARY_MARKER_CONTENT:
        return _passed_result("marker_written", started)
    return _failed_result("marker_content_mismatch", started)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _passed_result(detail: str, started: float) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome.PASSED,
        detail=detail,
        elapsed_ms=_elapsed_ms(started),
        marker_written=True,
    )


def _failed_result(detail: str, started: float) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
        detail=detail,
        elapsed_ms=_elapsed_ms(started),
        marker_written=False,
    )


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
