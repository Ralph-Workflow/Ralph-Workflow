from __future__ import annotations

import concurrent.futures
import contextlib
import errno
import os
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from ralph.mcp.protocol._startup_timeouts import (
    mcp_overrun_diagnostic_timeout_seconds,
    mcp_pre_serve_timeout_seconds,
    mcp_real_startup_deadline_from_env,
    mcp_startup_now,
)
from ralph.mcp.server._exit_handler import ExitHandler
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._overrun_handler import OverrunHandler
from ralph.mcp.server._pre_serve_overrun_events import StageEventRecorder
from ralph.mcp.server._stage_recorder import StageRecorder
from ralph.mcp.server._start_stage_tracker import record as _record_stage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

PRE_SERVE_OVERRUN_EXIT_CODE: int = 32
SESSION_OVERRUN_EXIT_CODE: int = 33
CATALOG_PUBLISH_OVERRUN_EXIT_CODE: int = 34


_stage_recorder: ContextVar[StageRecorder | None] = ContextVar(
    "ralph_mcp_stage_recorder", default=None
)

_exit_handler: ContextVar[ExitHandler | None] = ContextVar("ralph_mcp_exit_handler", default=None)

_overrun_handler: ContextVar[OverrunHandler | None] = ContextVar(
    "ralph_mcp_overrun_handler", default=None
)


def set_exit_handler(handler: ExitHandler | None) -> Token[ExitHandler | None]:
    return _exit_handler.set(handler)


def get_exit_handler() -> ExitHandler | None:
    return _exit_handler.get()


def reset_exit_handler(token: Token[ExitHandler | None]) -> None:
    _exit_handler.reset(token)


@contextmanager
def exit_handler_override(handler: ExitHandler | None) -> Iterator[None]:
    token = set_exit_handler(handler)
    try:
        yield
    finally:
        reset_exit_handler(token)


def set_overrun_handler(handler: OverrunHandler | None) -> Token[OverrunHandler | None]:
    return _overrun_handler.set(handler)


def get_overrun_handler() -> OverrunHandler | None:
    return _overrun_handler.get()


def reset_overrun_handler(token: Token[OverrunHandler | None]) -> None:
    _overrun_handler.reset(token)


@contextmanager
def overrun_handler_override(handler: OverrunHandler | None) -> Iterator[None]:
    token = set_overrun_handler(handler)
    try:
        yield
    finally:
        reset_overrun_handler(token)


def set_stage_recorder(recorder: StageRecorder | None) -> Token[StageRecorder | None]:
    return _stage_recorder.set(recorder)


def get_stage_recorder() -> StageRecorder | None:
    return _stage_recorder.get()


def reset_stage_recorder(token: Token[StageRecorder | None]) -> None:
    _stage_recorder.reset(token)


@contextmanager
def stage_recorder_override(recorder: StageRecorder | None) -> Iterator[None]:
    token = set_stage_recorder(recorder)
    try:
        yield
    finally:
        reset_stage_recorder(token)


def _resolve_stage_recorder(override: StageRecorder | None = None) -> StageRecorder:
    if override is not None:
        return override
    registered = get_stage_recorder()
    if registered is not None:
        return registered
    return _record_stage


def fail_closed_pre_serve_overrun(
    workspace_root: Path,
    *,
    stage: str,
    elapsed_s: float,
    budget_s: float,
    exit_code: int,
    diagnostic_timeout_s: float | None = None,
    stage_recorder: StageRecorder | None = None,
    exit_fn: ExitHandler | None = None,
) -> NoReturn:
    registered_overrun = get_overrun_handler()
    if registered_overrun is not None:
        registered_overrun(
            workspace_root,
            stage=stage,
            elapsed_s=elapsed_s,
            budget_s=budget_s,
            exit_code=exit_code,
            diagnostic_timeout_s=diagnostic_timeout_s,
            stage_recorder=stage_recorder,
            exit_fn=exit_fn,
        )
        sys.exit(exit_code)

    err = McpServerError(
        f"pre-serve overrun at stage {stage!r} after {elapsed_s:.3f}s "
        f"(budget={budget_s:.3f}s); worker thread is wedged on real I/O "
        f"and cannot be interrupted, exiting process to release the "
        f"inherited listener",
        last_stage=stage,
        elapsed_s=elapsed_s,
        restart_count=0,
    )

    timeout_s = (
        diagnostic_timeout_s
        if diagnostic_timeout_s is not None
        else mcp_overrun_diagnostic_timeout_seconds()
    )
    recorder = _resolve_stage_recorder(stage_recorder)

    def _flush_diagnostics() -> None:
        with contextlib.suppress(BaseException):
            logger.error("ralph-mcp startup: {}", err)
        with contextlib.suppress(BaseException):
            recorder(workspace_root, stage)
        with contextlib.suppress(BaseException):
            sys.stdout.flush()
        with contextlib.suppress(BaseException):
            sys.stderr.flush()

    diag_thread = threading.Thread(
        target=_flush_diagnostics,
        name="ralph-mcp-overrun-diagnostics",
        daemon=True,
    )
    diag_thread.start()
    diag_thread.join(timeout=max(0.0, timeout_s))
    target_exit = exit_fn or get_exit_handler() or os._exit
    target_exit(exit_code)
    sys.exit(exit_code)


def stage_overrun_exit_code(stage: str) -> int:
    if stage == "session_ready":
        return SESSION_OVERRUN_EXIT_CODE
    if stage == "upstream_catalog_or_discovery_done":
        return CATALOG_PUBLISH_OVERRUN_EXIT_CODE
    return PRE_SERVE_OVERRUN_EXIT_CODE


def log_and_record_stage(
    workspace_root: Path,
    name: str,
    *,
    t0: float | None = None,
    env_map: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    clock_fn: Callable[[], float] | None = None,
    stage_recorder: StageRecorder | None = None,
) -> None:
    clk = clock_fn if clock_fn is not None else time.monotonic
    eff_env = os.environ if env_map is None else env_map
    eff_t0 = t0 if t0 is not None else clk()
    real_deadline_s = mcp_real_startup_deadline_from_env(eff_env)
    remaining_s = max(0.0, real_deadline_s - (clk() - eff_t0))
    eff_timeout_s = timeout_s if timeout_s is not None else remaining_s
    write_timeout_s = (
        eff_timeout_s if eff_timeout_s > 0.0 else mcp_overrun_diagnostic_timeout_seconds(eff_env)
    )
    recorder = _resolve_stage_recorder(stage_recorder)

    error: list[BaseException] = []

    def _worker() -> None:
        try:
            logger.info("ralph-mcp startup: stage={stage}", stage=name)
            try:
                recorder(workspace_root, name, timeout_s=write_timeout_s)
            except TypeError:
                recorder(workspace_root, name)
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EROFS, errno.EDQUOT):
                logger.warning(
                    "ralph-mcp startup: failed to record stage={stage} due to disk exhaustion: {err}",
                    stage=name,
                    err=exc,
                )
            else:
                error.append(exc)
        except BaseException as exc:
            error.append(exc)

    stage_thread = threading.Thread(
        target=_worker,
        name=f"ralph-mcp-stage-{name}",
        daemon=True,
    )
    stage_thread.start()
    stage_thread.join(timeout=max(0.0, write_timeout_s))
    if stage_thread.is_alive():
        elapsed = clk() - eff_t0
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage=name,
            elapsed_s=elapsed,
            budget_s=eff_timeout_s,
            exit_code=stage_overrun_exit_code(name),
        )
    if error:
        exc = error[0]
        if isinstance(exc, TimeoutError):
            elapsed = clk() - eff_t0
            fail_closed_pre_serve_overrun(
                workspace_root,
                stage=name,
                elapsed_s=elapsed,
                budget_s=eff_timeout_s,
                exit_code=stage_overrun_exit_code(name),
            )
        raise exc


def run_pre_serve_bounded[T](
    workspace_root: Path,
    action: Callable[[], T],
    *,
    stage: str = "config_loading",
    env_map: Mapping[str, str] | None = None,
    t0: float | None = None,
    clock_fn: Callable[[], float] | None = None,
    timeout_s: float | None = None,
    exit_code: int | None = None,
    stage_recorder: StageRecorder | None = None,
    exit_fn: ExitHandler | None = None,
) -> T:
    clk = clock_fn if clock_fn is not None else mcp_startup_now
    eff_env = os.environ if env_map is None else env_map
    eff_t0 = t0 if t0 is not None else clk()
    remaining_s = mcp_pre_serve_timeout_seconds(eff_env, t0=eff_t0, clock_fn=clk)
    effective_timeout_s = timeout_s if timeout_s is not None else remaining_s
    eff_exit_code = exit_code if exit_code is not None else stage_overrun_exit_code(stage)

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(action)
        return future.result(timeout=effective_timeout_s)
    except concurrent.futures.TimeoutError:
        elapsed = clk() - eff_t0
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage=stage,
            elapsed_s=elapsed,
            budget_s=effective_timeout_s,
            exit_code=eff_exit_code,
            stage_recorder=stage_recorder,
            exit_fn=exit_fn,
        )
    finally:
        if ex is not None:
            ex.shutdown(wait=False)


__all__ = [
    "CATALOG_PUBLISH_OVERRUN_EXIT_CODE",
    "PRE_SERVE_OVERRUN_EXIT_CODE",
    "SESSION_OVERRUN_EXIT_CODE",
    "ExitHandler",
    "OverrunHandler",
    "StageEventRecorder",
    "StageRecorder",
    "exit_handler_override",
    "fail_closed_pre_serve_overrun",
    "get_exit_handler",
    "get_overrun_handler",
    "get_stage_recorder",
    "log_and_record_stage",
    "overrun_handler_override",
    "reset_exit_handler",
    "reset_overrun_handler",
    "reset_stage_recorder",
    "run_pre_serve_bounded",
    "set_exit_handler",
    "set_overrun_handler",
    "set_stage_recorder",
    "stage_overrun_exit_code",
    "stage_recorder_override",
]
