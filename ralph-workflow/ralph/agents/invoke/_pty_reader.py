"""PTY-based process line reader compatibility shim."""

from __future__ import annotations

from ralph.agents.invoke._pty_runner import run_pty_and_read_lines as _run_pty_and_read_lines

__all__ = ["_run_pty_and_read_lines"]
