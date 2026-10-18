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
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from ralph.mcp.protocol._startup_timeouts import (
    mcp_overrun_diagnostic_timeout_seconds,
    mcp_real_startup_deadline_from_env,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import record as _record_stage

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
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


def fail_closed_pre_serve_overrun(
    workspace_root: Path,
    *,
    stage: str,
    elapsed_s: float,
    budget_s: float,
    exit_code: int,
    diagnostic_timeout_s: float | None = None,
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

    The function does not return: ``os._exit`` terminates the
    process before any Python cleanup runs.
    """
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

    def _flush_diagnostics() -> None:
        with contextlib.suppress(BaseException):
            logger.error("ralph-mcp startup: {}", err)
        # pragma: no cover - stage-file write may itself fail or stall
        with contextlib.suppress(BaseException):
            _record_stage(workspace_root, stage)
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
    os._exit(exit_code)


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

    error: list[BaseException] = []

    def _worker() -> None:
        try:
            logger.info("ralph-mcp startup: stage={stage}", stage=name)
            try:
                _record_stage(workspace_root, name, timeout_s=write_timeout_s)
            except TypeError:
                _record_stage(workspace_root, name)
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


__all__ = [
    "CATALOG_PUBLISH_OVERRUN_EXIT_CODE",
    "PRE_SERVE_OVERRUN_EXIT_CODE",
    "SESSION_OVERRUN_EXIT_CODE",
    "fail_closed_pre_serve_overrun",
    "log_and_record_stage",
    "stage_overrun_exit_code",
]
