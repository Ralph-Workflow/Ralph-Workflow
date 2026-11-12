from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from ralph.agents.invoke import (
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    invoke_agent,
)
from ralph.config.general_config import GeneralConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig

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
        prepared = _prepare_probe(
            config,
            workspace_path,
            general_config,
            extra_env,
            timeout_s,
            started,
        )
        if isinstance(prepared, _ProbeResult):
            return prepared
        invoke = invoke_agent if invoke_fn is None else invoke_fn
        error, timed_out = _invoke_with_timeout(
            invoke,
            config,
            prepared.prompt_file,
            prepared.options,
            timeout_s,
        )
        if timed_out:
            return _inconclusive_result("probe_timeout", started)
        if error is not None:
            return _inconclusive_result("invocation_error", started, error=_error_text(error))
        return _judge_marker(workspace_path, started)
    finally:
        if owns_workspace and workspace_path is not None and not timed_out:
            shutil.rmtree(workspace_path, ignore_errors=True)


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
            shutil.rmtree(workspace_path, ignore_errors=True)
        return _inconclusive_result("scratch_inside_phase_workspace", started)
    return workspace_path


def _prepare_probe(
    config: AgentConfig,
    workspace_path: Path,
    general_config: GeneralConfig | None,
    extra_env: dict[str, str] | None,
    timeout_s: float,
    started: float,
) -> _PreparedProbe | _ProbeResult:
    try:
        options = _build_probe_options(
            config,
            workspace_path,
            general_config,
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
    general_config: GeneralConfig | None,
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
    general = general_config if general_config is not None else GeneralConfig()
    options = build_invoke_options_from_config(general, runtime)
    return replace(options, idle_timeout_seconds=timeout_s, max_session_seconds=timeout_s)


def _write_prompt(workspace_path: Path) -> str:
    prompt_path = workspace_path / CANARY_PROMPT_FILENAME
    prompt_path.write_text(CANARY_PROBE_TASK, encoding="utf-8")
    return str(prompt_path)


def _invoke_with_timeout(
    invoke: _InvokeAgentFn,
    config: AgentConfig,
    prompt_file: str,
    options: InvokeOptions,
    timeout_s: float,
) -> tuple[BaseException | None, bool]:
    captured: list[BaseException] = []

    def _run() -> None:
        try:
            result = invoke(config, prompt_file, options=options)
            if result is not None:
                deque(result, maxlen=0)
        except BaseException as exc:
            captured.append(exc)

    thread = threading.Thread(target=_run, name="ralph-canary-probe", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return (None, True)
    return (captured[0] if captured else None, False)


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
