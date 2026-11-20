from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from loguru import logger

import ralph.agents.invoke as agents_invoke
from ralph._typed_attrs import read_attr
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    IdleStreamTimeoutError,
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
)
from ralph.agents.timeout_clock import SystemClock
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.git.errors import GitOperationError
from ralph.git.operations import find_repo_root
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    register_entry_point,
    registered_entry_points,
    registered_runners,
)
from ralph.recovery.canary_probe_judge import (
    CANONICAL_DETAIL_VALUES,
    CanaryMarkerState,
    ProbeJudgeOutcome,
    ProbeJudgeVerdict,
    judge_probe,
)
from ralph.recovery.canary_probe_launch import (
    ProbeTransportNotSafeError,
    probe_launch_override,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.config.agent_config import AgentConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps, RawAgentInvoke
    from ralph.recovery.controller import RecoveryController

CANARY_MARKER_FILENAME: Final[str] = "ralph_canary_marker.txt"
CANARY_MARKER_CONTENT: Final[str] = "RALPH_CANARY_OK"
CANARY_PROBE_PHASE: Final[str] = "canary_probe"
CANARY_PROMPT_FILENAME: Final[str] = "canary_probe_prompt.md"
CANARY_PROBE_REPLY: Final[str] = "CANARY_WRITTEN"
DEFAULT_PROBE_TIMEOUT_S: Final[float] = 30
PROBE_INCONCLUSIVE_VERDICT: Final[str] = "unknown"
PROBE_AREA_RELATIVE_DIR: Final[str] = ".agent/tmp"
PROBE_DIR_PREFIX: Final[str] = "canary-probe-"
PROBE_OWNER_PID_FILENAME: Final[str] = "owner_pid"
CANARY_PROBE_TASK: Final[str] = (
    f"Canary probe task.\nCreate one file named {CANARY_MARKER_FILENAME} in the current working directory.\nThe entire content of that file must be exactly this text:\n{CANARY_MARKER_CONTENT}\nCreate no other file, change no other file and run no other command.\nWhen the file exists, answer with the single line {CANARY_PROBE_REPLY}."
)

PROBE_COULD_NOT_RUN_DETAIL: Final[str] = "probe_could_not_run"
PROBE_REASON_INVOCATION_ERROR: Final[str] = "invocation_error"
PROBE_REASON_PROBE_TIMEOUT: Final[str] = "probe_timeout"
PROBE_REASON_INTERNAL_ERROR: Final[str] = "probe_internal_error"
PROBE_REASON_BUDGET_EXHAUSTED: Final[str] = "budget_exhausted"
PROBE_REASON_NO_PRETRUSTED_WORKSPACE: Final[str] = "no_pretrusted_workspace"
PROBE_REASON_TRANSPORT_NOT_PROBE_SAFE: Final[str] = "transport_not_probe_safe"
PROBE_INNER_BUDGET_RATIO: Final[float] = 0.9
_SCRATCH_REMOVAL_ATTEMPTS: Final[int] = 3
_SCRATCH_REMOVAL_BACKOFF_S: Final[float] = 0.05
_SESSION_JOIN_TIMEOUT_S: Final[float] = 0.5

if PROBE_COULD_NOT_RUN_DETAIL not in CANONICAL_DETAIL_VALUES:
    raise RuntimeError(
        "the canary probe detail vocabulary must stay inside the judge's canonical detail values"
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
    reason: str = ""


@dataclass(frozen=True)
class _PreparedProbe:
    options: InvokeOptions
    prompt_file: str


@dataclass
class _SpawnedProcess:
    pid: int | None = None

    def report(self, pid: int) -> None:
        self.pid = pid


@dataclass(frozen=True)
class _ProbeInvoke:
    result: AttemptResult | None
    error: BaseException | None
    timed_out: bool
    session_thread: threading.Thread | None = None
    finished: list[AttemptResult | None] | None = None

    @property
    def current_result(self) -> AttemptResult | None:
        if self.result is not None:
            return self.result
        if self.finished and self.finished[0] is not None:
            return self.finished[0]
        return None


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
            phase_workspace=phase_workspace,
            trusted_parent=trusted_parent,
            started=started,
        )
    except (AgentInvocationError, OSError, RuntimeError, ValueError, KeyError) as exc:
        return _inconclusive_result(PROBE_REASON_INTERNAL_ERROR, started, error=_error_text(exc))


def _run_canary_probe(
    config: AgentConfig,
    *,
    invoke_fn: RawAgentInvoke | None,
    workspace_factory: Callable[[], Path] | None,
    timeout_s: float,
    general_config: GeneralConfig | None,
    extra_env: dict[str, str] | None,
    phase_workspace: Path | None,
    trusted_parent: Path | None,
    started: float,
) -> _ProbeResult:
    owns_workspace = workspace_factory is None
    workspace_path: Path | None = None
    timed_out = False
    session_thread: threading.Thread | None = None
    attempt: _ProbeInvoke | None = None
    spawned = _SpawnedProcess()
    try:
        preflight = _preflight_failure(timeout_s, started)
        if preflight is not None:
            return preflight
        workspace = _scratch_workspace(
            workspace_factory,
            phase_workspace,
            trusted_parent,
            started,
            owns_workspace=owns_workspace,
        )
        if isinstance(workspace, _ProbeResult):
            return workspace
        workspace_path = workspace
        general = general_config if general_config is not None else GeneralConfig()
        prepared = _prepare_probe(
            config, workspace_path, general, extra_env, timeout_s, started, spawned
        )
        if isinstance(prepared, _ProbeResult):
            return prepared
        invoke = invoke_fn if invoke_fn is not None else _production_invoke()
        attempt = _invoke_with_timeout(invoke, config, prepared, general, timeout_s)
        timed_out = attempt.timed_out
        session_thread = attempt.session_thread
        marker_state = _read_marker_state(workspace_path)
        return _attempt_result(attempt, marker_state, started)
    finally:
        if owns_workspace and workspace_path is not None:
            if timed_out:
                _reap_scratch_when_the_session_ends(
                    session_thread,
                    workspace_path,
                    attempt=attempt,
                    is_process_terminal=_terminal_process_predicate(spawned),
                )
            else:
                _remove_scratch_workspace(workspace_path)


def _terminal_process_predicate(spawned: _SpawnedProcess) -> Callable[[], bool]:
    from ralph.process.manager import get_process_manager, is_terminal_status

    def _is_terminal() -> bool:
        pid = spawned.pid
        if pid is None:
            return False
        record = get_process_manager().get_record(pid, include_terminal=True)
        if record is None:
            return False
        return is_terminal_status(record.status)

    return _is_terminal


def _reap_scratch_when_the_session_ends(
    session_thread: threading.Thread | None,
    workspace_path: Path,
    *,
    attempt: _ProbeInvoke | None = None,
    is_process_terminal: Callable[[], bool] | None = None,
) -> None:
    if session_thread is None and attempt is None:
        return
    threading.Thread(
        target=_remove_scratch_after_the_session_ends,
        args=(session_thread, workspace_path, attempt, is_process_terminal),
        name="ralph-canary-probe-reaper",
        daemon=True,
    ).start()


def _remove_scratch_after_the_session_ends(
    session_thread: threading.Thread | None,
    workspace_path: Path,
    attempt: _ProbeInvoke | None = None,
    is_process_terminal: Callable[[], bool] | None = None,
) -> None:
    waiter = threading.Event()
    for _ in range(20):
        res = attempt.current_result if attempt is not None else None
        if res is not None and res.facts is not None and res.facts.termination is not None:
            _remove_scratch_workspace(workspace_path)
            return
        if session_thread is not None and not session_thread.is_alive():
            _remove_scratch_workspace(workspace_path)
            return
        if is_process_terminal is not None and is_process_terminal():
            _remove_scratch_workspace(workspace_path)
            return
        waiter.wait(timeout=0.05)
    if session_thread is not None:
        session_thread.join(timeout=_SESSION_JOIN_TIMEOUT_S)
    _remove_scratch_workspace(workspace_path)


def _attempt_result(
    attempt: _ProbeInvoke, marker_state: CanaryMarkerState, started: float
) -> _ProbeResult:
    facts = attempt.result.facts if attempt.result is not None else None
    if marker_state is CanaryMarkerState.MATCHED:
        return _verdict_result(judge_probe(marker_state, facts), started)
    if attempt.timed_out:
        return _inconclusive_result(PROBE_REASON_PROBE_TIMEOUT, started)
    if attempt.error is not None:
        return _inconclusive_result(
            _failure_reason(attempt.error),
            started,
            error=_error_text(attempt.error),
        )
    return _verdict_result(judge_probe(marker_state, facts), started)


def _failure_reason(error: BaseException) -> str:
    if _error_chain_contains(error, ProbeTransportNotSafeError):
        return PROBE_REASON_TRANSPORT_NOT_PROBE_SAFE
    if _error_chain_contains(error, (AgentInactivityTimeoutError, IdleStreamTimeoutError)):
        return PROBE_REASON_PROBE_TIMEOUT
    return PROBE_REASON_INVOCATION_ERROR


def _error_chain_contains(
    error: BaseException, kinds: type[BaseException] | tuple[type[BaseException], ...]
) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, kinds):
            return True
        current = current.__cause__ or current.__context__
    return False


def _preflight_failure(timeout_s: float, started: float) -> _ProbeResult | None:
    if timeout_s <= 0:
        return _inconclusive_result(PROBE_REASON_INTERNAL_ERROR, started, error="invalid_timeout")
    return None


def _scratch_workspace(
    workspace_factory: Callable[[], Path] | None,
    phase_workspace: Path | None,
    trusted_parent: Path | None,
    started: float,
    *,
    owns_workspace: bool,
) -> Path | _ProbeResult:
    try:
        workspace_path = _create_scratch_workspace(workspace_factory, trusted_parent)
    except (AttributeError, OSError, RuntimeError, ValueError, KeyError) as exc:
        return _inconclusive_result(PROBE_REASON_INTERNAL_ERROR, started, error=_error_text(exc))
    if workspace_path is None:
        return _inconclusive_result(PROBE_REASON_NO_PRETRUSTED_WORKSPACE, started)
    if (
        phase_workspace is not None
        and _is_within(workspace_path, phase_workspace)
        and (not _is_designated_probe_area(workspace_path, phase_workspace))
    ):
        if owns_workspace:
            _remove_scratch_workspace(workspace_path)
        return _inconclusive_result(
            PROBE_REASON_INTERNAL_ERROR, started, error="scratch_inside_phase_workspace"
        )
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
    spawned: _SpawnedProcess,
) -> _PreparedProbe | _ProbeResult:
    try:
        options = _build_probe_options(
            config, workspace_path, general, extra_env, timeout_s, spawned
        )
    except (AttributeError, OSError, RuntimeError, ValueError, KeyError) as exc:
        return _inconclusive_result(PROBE_REASON_INTERNAL_ERROR, started, error=_error_text(exc))
    try:
        prompt_file = _write_prompt(workspace_path)
    except OSError as exc:
        return _inconclusive_result(PROBE_REASON_INTERNAL_ERROR, started, error=_error_text(exc))
    return _PreparedProbe(options=options, prompt_file=prompt_file)


def _create_scratch_workspace(
    factory: Callable[[], Path] | None, trusted_parent: Path | None
) -> Path | None:
    if factory is not None:
        return Path(factory())
    parent = _resolve_trusted_parent(trusted_parent)
    if parent is None:
        return None
    probe_area = parent / PROBE_AREA_RELATIVE_DIR
    scratch = probe_area / f"{PROBE_DIR_PREFIX}{uuid4().hex}"
    scratch.mkdir(parents=True, exist_ok=False)
    _write_owner_pid_marker(scratch)
    return scratch


def _write_owner_pid_marker(scratch: Path) -> None:
    try:
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            scratch / PROBE_OWNER_PID_FILENAME,
            str(os.getpid()),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("canary probe owner-pid marker write failed: {}", exc)


def _resolve_trusted_parent(trusted_parent: Path | None) -> Path | None:
    if trusted_parent is not None:
        return trusted_parent
    try:
        return find_repo_root(Path.cwd())
    except (
        AttributeError,
        GitOperationError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
    ) as exc:
        logger.debug("canary probe trusted-parent resolution failed: {}", exc)
        return None


def _remove_scratch_workspace(workspace_path: Path) -> bool:
    workspace = FsWorkspace(workspace_path)
    root = workspace.root
    for attempt in range(_SCRATCH_REMOVAL_ATTEMPTS):
        if not root.is_dir():
            return True
        try:
            workspace.delete(str(root), recursive=True)
        except OSError:
            if attempt == _SCRATCH_REMOVAL_ATTEMPTS - 1:
                break
            backoff = threading.Event()
            backoff.wait(timeout=_SCRATCH_REMOVAL_BACKOFF_S)
            continue
        return True
    return not root.is_dir()


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _agent_name(config: AgentConfig) -> str | None:
    cmd: object = read_attr(config, "cmd", None)
    if not isinstance(cmd, str):
        return None
    parts = cmd.split()
    return parts[0] if parts else None


def _build_probe_options(
    config: AgentConfig,
    workspace_path: Path,
    general: GeneralConfig,
    extra_env: dict[str, str] | None,
    timeout_s: float,
    spawned: _SpawnedProcess,
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
    inner_budget = timeout_s * PROBE_INNER_BUDGET_RATIO
    return replace(
        options,
        idle_timeout_seconds=inner_budget,
        max_session_seconds=inner_budget,
        on_spawn=spawned.report,
    )


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

    def _record_result(res: AttemptResult) -> None:
        if not finished:
            finished.append(res)

    def _run() -> None:
        try:
            with InvokeOptions.spawn_report_override(prepared.options.on_spawn):
                res = _dispatch_probe_attempt(
                    invoke, config, prepared, general, on_attempt_result=_record_result
                )
            if not finished and res is not None:
                finished.append(res)
        except (
            AgentInvocationError,
            TypeError,
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
        ) as exc:
            captured.append(exc)

    thread = threading.Thread(target=_run, name="ralph-canary-probe", daemon=True)
    thread.start()
    thread.join(timeout_s)
    result = finished[0] if finished else None
    error = captured[0] if captured else None
    if thread.is_alive():
        return _ProbeInvoke(
            result=result,
            error=error,
            timed_out=True,
            session_thread=thread,
            finished=finished,
        )
    return _ProbeInvoke(
        result=result,
        error=error,
        timed_out=False,
        session_thread=thread,
        finished=finished,
    )


def _production_invoke() -> RawAgentInvoke:
    return agents_invoke.invoke_agent


def _dispatch_probe_attempt(
    invoke: RawAgentInvoke,
    config: AgentConfig,
    prepared: _PreparedProbe,
    general: GeneralConfig,
    *,
    on_attempt_result: Callable[[AttemptResult], None] | None = None,
) -> AttemptResult | None:
    agent_name = _agent_name(config)
    unified = UnifiedConfig(
        general=general, agents={agent_name: config} if agent_name is not None else {}
    )
    from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps, raw_attempt_invoke

    deps = AttemptRunnerDeps(
        clock=SystemClock(),
        invoke=raw_attempt_invoke(invoke),
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.CANARY_PROBE, config=unified, policy_bundle=None, deps=deps
    )
    result = registered_runners()[AgentEntryPoint.CANARY_PROBE](
        invocation=config,
        config=unified,
        controller=controller,
        deps=deps,
        phase=CANARY_PROBE_PHASE,
        agent=agent_name,
        prompt_file=prepared.prompt_file,
        options=prepared.options,
    )
    if on_attempt_result is not None and result is not None:
        on_attempt_result(result)
    logger.info("canary probe attempt outcome: {}", result.outcome.kind)
    return result


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

    from ralph.pipeline.attempt_runner import run_agent_attempt

    with probe_launch_override():
        return run_agent_attempt(
            AgentEntryPoint.CANARY_PROBE,
            effect=effect,
            invocation=invocation,
            config=config,
            controller=controller,
            deps=deps,
            state=state,
            **execute_kwargs,
        )


if AgentEntryPoint.CANARY_PROBE not in registered_entry_points():
    register_entry_point(AgentEntryPoint.CANARY_PROBE, _run_canary_probe_attempt)


def _read_marker_state(workspace_path: Path) -> CanaryMarkerState:
    marker_path = workspace_path / CANARY_MARKER_FILENAME
    try:
        content = marker_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CanaryMarkerState.MISSING
    except OSError:
        return CanaryMarkerState.UNREADABLE
    if content.strip() == CANARY_MARKER_CONTENT:
        return CanaryMarkerState.MATCHED
    return CanaryMarkerState.MISMATCHED


def _verdict_result(verdict: ProbeJudgeVerdict, started: float) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome(verdict.outcome.value),
        detail=verdict.detail,
        elapsed_ms=_elapsed_ms(started),
        marker_written=verdict.marker_written,
        reason=_verdict_reason(verdict),
    )


def _verdict_reason(verdict: ProbeJudgeVerdict) -> str:
    if verdict.reason:
        return verdict.reason
    if verdict.outcome is ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK:
        return verdict.detail
    return ""


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def sweep_canary_probe_scratch_workspaces(probe_area: Path, *, cutoff: float) -> int:
    removed = 0
    for scratch in _iter_probe_scratch_dirs(probe_area):
        owner_pid = _read_owner_pid(scratch)
        if owner_pid is None:
            if not _is_aged_probe_scratch(scratch, cutoff):
                continue
        elif _probe_owner_pid_alive(owner_pid):
            continue
        if _remove_scratch_workspace(scratch):
            removed += 1
    return removed


def _iter_probe_scratch_dirs(probe_area: Path) -> Iterator[Path]:
    try:
        candidates = DEFAULT_FILE_BACKEND.glob(probe_area, f"{PROBE_DIR_PREFIX}*")
    except OSError:
        return
    for candidate in candidates:
        if candidate.name.startswith(PROBE_DIR_PREFIX) and candidate.is_dir():
            yield candidate


def _read_owner_pid(scratch: Path) -> int | None:
    try:
        raw = DEFAULT_FILE_BACKEND.read_text(scratch / PROBE_OWNER_PID_FILENAME, encoding="utf-8")
    except (KeyError, OSError, UnicodeDecodeError):
        return None
    try:
        owner_pid = int(raw.strip())
    except ValueError:
        return None
    return owner_pid if owner_pid > 0 else None


def _probe_owner_pid_alive(owner_pid: int) -> bool:
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return True
    return True


def _is_aged_probe_scratch(scratch: Path, cutoff: float) -> bool:
    try:
        return scratch.lstat().st_mtime < cutoff
    except OSError:
        return False


def _inconclusive_result(reason: str, started: float, *, error: str | None = None) -> _ProbeResult:
    return _ProbeResult(
        outcome=_ProbeOutcome.INCONCLUSIVE,
        detail=PROBE_COULD_NOT_RUN_DETAIL,
        elapsed_ms=_elapsed_ms(started),
        marker_written=None,
        error=error,
        reason=reason,
    )


__all__ = [
    "CANARY_MARKER_CONTENT",
    "CANARY_MARKER_FILENAME",
    "CANARY_PROBE_PHASE",
    "CANARY_PROBE_REPLY",
    "CANARY_PROBE_TASK",
    "CANARY_PROMPT_FILENAME",
    "DEFAULT_PROBE_TIMEOUT_S",
    "PROBE_COULD_NOT_RUN_DETAIL",
    "PROBE_INCONCLUSIVE_VERDICT",
    "PROBE_OWNER_PID_FILENAME",
    "PROBE_REASON_BUDGET_EXHAUSTED",
    "PROBE_REASON_INTERNAL_ERROR",
    "PROBE_REASON_INVOCATION_ERROR",
    "PROBE_REASON_NO_PRETRUSTED_WORKSPACE",
    "PROBE_REASON_PROBE_TIMEOUT",
    "PROBE_REASON_TRANSPORT_NOT_PROBE_SAFE",
    "ProbeOutcome",
    "ProbeResult",
    "run_canary_probe",
    "sweep_canary_probe_scratch_workspaces",
]
