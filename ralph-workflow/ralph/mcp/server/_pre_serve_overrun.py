"""Force-exit helper for the bounded pre-serve critical path.

The ``ThreadPoolExecutor.shutdown(wait=False)`` timeout does NOT kill
a worker thread that is blocked on real I/O holding the GIL (a FIFO
read with no writer, a hung ``stat()`` on a stalled NFS mount, a
wedged SQLite open, a ``tomllib.load()`` on a slow filesystem).
Python cannot interrupt that thread: the interpreter waits for ALL
non-daemon threads to finish before exiting, and
``ThreadPoolExecutor`` workers are non-daemon.

The only correct fail-closed is to bypass interpreter cleanup: log
the typed error, record the last stage so the parent's preflight
surfaces the attribution, flush stdio, and call ``os._exit()`` so
the OS reaps the worker via process termination. The parent owns
the listening socket for the bridge's lifetime, so a force-exited
child releases it without an ``ECONNREFUSED`` window.

Diagnostic and stage-recording I/O before ``os._exit()`` is itself
bounded: if the underlying filesystem or stdio stream is wedged on
the same stalled I/O that caused the overrun, diagnostic flushes run in
a bounded daemon thread with a short fail-safe timeout so stalled
diagnostic I/O cannot hold the unserving child past its deadline.

Exit codes are stable per-stage so the parent's restart budget and
the durable health-event sink can attribute the exit without
re-reading the on-disk stage file.

STAGE-RECORDER SEAM: the stage marker is written through an INJECTED
recorder (:class:`StageRecorder`). Production uses the on-disk stage
tracker; a caller that needs a different sink passes one explicitly
(``stage_recorder=``) or registers one for the current context
(:func:`stage_recorder_override`). Tests therefore observe, stall, or
fail the stage-recording step through the public seam instead of
patching a private module attribute.

:class:`StageEventRecorder`, :class:`StageRecorder`, :class:`ExitHandler`,
and :class:`OverrunHandler` are defined in their respective modules and
re-exported here, so imports from :mod:`ralph.mcp.server._pre_serve_overrun`
keep working unchanged.
"""

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

#: Distinct non-zero exit codes for the pre-serve overrun paths. Three
#: codes (one per stage) so the parent's restart budget and the
#: incident-triage sink can attribute the exit without re-reading
#: the on-disk stage file.
PRE_SERVE_OVERRUN_EXIT_CODE: int = 32
SESSION_OVERRUN_EXIT_CODE: int = 33
#: Catalog publication overrun: ``os.open`` on the catalog temp path
#: blocked (hostile or wedged filesystem, FIFO replacement). Distinct
#: from the session and config_loading overruns so the parent
#: attributes the exit to the catalog publication stage without
#: re-reading the on-disk stage file.
CATALOG_PUBLISH_OVERRUN_EXIT_CODE: int = 34


#: Stage recorder registered for the current context. ``None`` means the
#: on-disk stage tracker is used. A ``ContextVar`` (not a module-level
#: global) keeps concurrent agent runs and parallel test tasks from
#: stomping on each other's recorder.
_stage_recorder: ContextVar[StageRecorder | None] = ContextVar(
    "ralph_mcp_stage_recorder", default=None
)

_exit_handler: ContextVar[ExitHandler | None] = ContextVar("ralph_mcp_exit_handler", default=None)

_overrun_handler: ContextVar[OverrunHandler | None] = ContextVar(
    "ralph_mcp_overrun_handler", default=None
)


def set_exit_handler(handler: ExitHandler | None) -> Token[ExitHandler | None]:
    """Register the overrun exit handler for the current context."""
    return _exit_handler.set(handler)


def get_exit_handler() -> ExitHandler | None:
    """Return the registered overrun exit handler, or ``None``."""
    return _exit_handler.get()


def reset_exit_handler(token: Token[ExitHandler | None]) -> None:
    """Restore the exit handler to a previous value."""
    _exit_handler.reset(token)


@contextmanager
def exit_handler_override(handler: ExitHandler | None) -> Iterator[None]:
    """Temporarily register ``handler`` as the overrun exit handler.

    The registration is scoped to the ``with`` block: on exit the previous
    handler is restored, so tests and callers never leak exit interceptors.
    """
    token = set_exit_handler(handler)
    try:
        yield
    finally:
        reset_exit_handler(token)


def set_overrun_handler(handler: OverrunHandler | None) -> Token[OverrunHandler | None]:
    """Register the pre-serve overrun handler for the current context."""
    return _overrun_handler.set(handler)


def get_overrun_handler() -> OverrunHandler | None:
    """Return the registered pre-serve overrun handler, or ``None``."""
    return _overrun_handler.get()


def reset_overrun_handler(token: Token[OverrunHandler | None]) -> None:
    """Restore the overrun handler to a previous value."""
    _overrun_handler.reset(token)


@contextmanager
def overrun_handler_override(handler: OverrunHandler | None) -> Iterator[None]:
    """Temporarily register ``handler`` as the pre-serve overrun handler.

    The registration is scoped to the ``with`` block: on exit the previous
    handler is restored, so tests and callers never leak overrun handlers.
    """
    token = set_overrun_handler(handler)
    try:
        yield
    finally:
        reset_overrun_handler(token)


def set_stage_recorder(recorder: StageRecorder | None) -> Token[StageRecorder | None]:
    """Register the startup stage recorder for the current context.

    Args:
        recorder: Recorder invoked with ``(root, stage)`` for every startup
            stage marker. ``None`` clears the registration.

    Returns:
        A Token that can be passed to :func:`reset_stage_recorder` to restore
        the previous recorder.
    """
    return _stage_recorder.set(recorder)


def get_stage_recorder() -> StageRecorder | None:
    """Return the registered startup stage recorder, or ``None``."""
    return _stage_recorder.get()


def reset_stage_recorder(token: Token[StageRecorder | None]) -> None:
    """Restore the stage recorder to a previous value."""
    _stage_recorder.reset(token)


@contextmanager
def stage_recorder_override(recorder: StageRecorder | None) -> Iterator[None]:
    """Temporarily register ``recorder`` as the startup stage recorder.

    The registration is scoped to the ``with`` block: on exit the previous
    recorder is restored, so a caller never leaks its recorder into the
    next startup stage.
    """
    token = set_stage_recorder(recorder)
    try:
        yield
    finally:
        reset_stage_recorder(token)


def _resolve_stage_recorder(override: StageRecorder | None = None) -> StageRecorder:
    """Return the stage recorder to use for the next stage marker.

    Resolution order: the explicit ``override`` argument, then the recorder
    registered for the current context, then the on-disk stage tracker.
    """
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
    """Force-exit the child when a bounded pre-serve operation cannot complete in time.

    See the module docstring for why ``os._exit`` is the only correct
    fail-closed when the worker thread holds the GIL on real I/O.

    Stage recording, error logging, and stdio flushing are attempted in a
    bounded daemon thread with a fail-safe timeout (defaulting to
    ``mcp_overrun_diagnostic_timeout_seconds()``). If the filesystem or stdio
    stream is stalled, the timeout expires and ``os._exit`` terminates the
    process immediately, preventing a stalled filesystem from delaying
    deadline enforcement.

    Args:
        workspace_root: Workspace root the stage marker is written under.
        stage: Stage label attributed to the overrun.
        elapsed_s: Elapsed seconds the stage consumed.
        budget_s: Budget the stage was allowed to consume.
        exit_code: Process exit code for the overrun.
        diagnostic_timeout_s: Fail-safe timeout for the diagnostic flush.
            ``None`` uses the configured overrun diagnostic budget.
        stage_recorder: Recorder used to persist the stage marker. ``None``
            (the production default) resolves the context-registered recorder
            and falls back to the on-disk stage tracker.
        exit_fn: Override callable to invoke on exit. ``None`` (the production
            default) resolves the context-registered exit handler or ``os._exit``.

    The function does not return: ``os._exit`` (or ``exit_fn``) terminates the
    process before any Python cleanup runs.
    """
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
        # pragma: no cover - stage-file write may itself fail or stall
        with contextlib.suppress(BaseException):
            recorder(workspace_root, stage)
        # Flush stdio: the parent reaps us via process termination, so any
        # unflushed buffer is lost. loguru writes synchronously to stderr;
        # the explicit ``flush`` covers stdout (the banner site) and any
        # third-party sink that might be attached.
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
    """Return the distinct overrun exit code associated with ``stage``."""
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
    """Emit one flushed ``logger.info`` line and write it to the stage file.

    R4-LOGS: every startup stage is logged as a flushed ``logger.info``
    line AND recorded to the stage-tracker file the supervisor reads on
    preflight failure. The two actions are always coupled so the operator
    has both the real-time stream and the on-disk record.

    Both logging and label persistence are bounded by the remaining real
    startup deadline (``mcp_real_startup_deadline_from_env``). If the
    stage write or logging stalls (e.g. hung filesystem, blocked FIFO),
    the child fails closed via :func:`fail_closed_pre_serve_overrun`
    strictly before preflight expires, leaving available safety headroom
    for the supervisor to attribute the exit cleanly.

    Args:
        workspace_root: Workspace root the stage marker is written under.
        name: Stage label to log and record.
        t0: Startup anchor the remaining budget is measured from. ``None``
            reads the clock once.
        env_map: Environment map the envelope is derived from. ``None`` means
            the process environment.
        timeout_s: Explicit budget for the stage write. ``None`` uses the
            remaining real startup deadline.
        clock_fn: Injectable monotonic clock. ``None`` uses ``time.monotonic``.
        stage_recorder: Recorder used to persist the stage marker. ``None``
            (the production default) resolves the context-registered recorder
            and falls back to the on-disk stage tracker.
    """
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
    """Execute a synchronous pre-serve operation within the remaining startup deadline.

    Runs ``action`` in a single-worker ``ThreadPoolExecutor``. If ``action``
    does not complete before the effective timeout (by default the remaining real
    startup deadline from :func:`mcp_pre_serve_timeout_seconds`), fails closed
    via :func:`fail_closed_pre_serve_overrun` with the distinct overrun exit code
    for ``stage``.

    Args:
        workspace_root: Workspace root where overrun diagnostics and stage markers are written.
        action: Synchronous callable to execute.
        stage: Startup stage attribution for diagnostics (default: "config_loading").
        env_map: Environment mapping determining deadline envelopes.
        t0: Monotonic timestamp marking the start of the startup window.
        clock_fn: Clock callable returning monotonic seconds.
        timeout_s: Explicit timeout in seconds; defaults to remaining real startup deadline.
        exit_code: Specific exit code on timeout; defaults to ``stage_overrun_exit_code(stage)``.
        stage_recorder: Recorder for stage markers; falls back to context registration.
        exit_fn: Exit callable on overrun; falls back to context registration or ``os._exit``.

    Returns:
        The result of ``action()``.
    """
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
