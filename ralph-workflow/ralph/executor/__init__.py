"""Low-level process execution helpers for Ralph Workflow.

This package wraps subprocess execution with structured result and error types,
providing a consistent interface for running external commands from phase handlers
and MCP tool implementations.

Main entry points:

- ``run_process(cmd, ...)`` — synchronous subprocess execution; returns a
  ``ProcessResult`` with stdout, stderr, and return code.
- ``run_process_async(cmd, ...)`` — async variant for use in asyncio contexts.
- ``ProcessResult`` — holds stdout, stderr, returncode, and a convenience
  ``check()`` method that raises ``ProcessExecutionError`` on non-zero exit.
- ``ProcessExecutionError`` — raised when a process exits with a non-zero code;
  carries the full ``ProcessResult`` for diagnostics.

For agent subprocess management (streaming, watchdogs, parser integration) see
``ralph.agents.invoke`` and ``ralph.agents.subprocess_executor``.
"""

from .process import (
    ProcessExecutionError,
    ProcessResult,
    ProcessRunOptions,
    run_process,
    run_process_async,
)

__all__ = [
    "ProcessExecutionError",
    "ProcessResult",
    "ProcessRunOptions",
    "run_process",
    "run_process_async",
]
