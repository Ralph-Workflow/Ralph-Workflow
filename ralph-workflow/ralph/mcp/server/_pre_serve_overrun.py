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

Exit codes are stable per-stage so the parent's restart budget and
the durable health-event sink can attribute the exit without
re-reading the on-disk stage file.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import record as _record_stage

if TYPE_CHECKING:
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
) -> NoReturn:
    """Force-exit the child when a bounded pre-serve operation cannot complete in time.

    See the module docstring for why ``os._exit`` is the only correct
    fail-closed when the worker thread holds the GIL on real I/O.

    The function does not return: ``os._exit`` terminates the
    process before any Python cleanup runs. The ``type: ignore`` on
    the no-return annotation documents the contract for type
    checkers (mypy otherwise flags the unreachable subsequent
    statements as type errors).
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
    logger.error("ralph-mcp startup: {}", err)
    # pragma: no cover - stage-file write may itself fail
    with contextlib.suppress(Exception):
        _record_stage(workspace_root, stage)
    # Flush stdio: the parent reaps us via process termination, so any
    # unflushed buffer is lost. loguru writes synchronously to stderr;
    # the explicit ``flush`` covers stdout (the banner site) and any
    # third-party sink that might be attached.
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    os._exit(exit_code)


__all__ = [
    "CATALOG_PUBLISH_OVERRUN_EXIT_CODE",
    "PRE_SERVE_OVERRUN_EXIT_CODE",
    "SESSION_OVERRUN_EXIT_CODE",
    "fail_closed_pre_serve_overrun",
]
