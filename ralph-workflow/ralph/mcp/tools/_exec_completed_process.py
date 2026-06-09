"""Completed process adapter dataclass for exec tool output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _CompletedProcessAdapter:
    """Adapter exposing stdout/stderr/returncode like subprocess.CompletedProcess."""

    stdout: bytes
    stderr: bytes
    returncode: int
    #: True when output hit the capture cap and the process was killed, so the
    #: captured bytes are the (tail) prefix of a larger, incomplete output.
    truncated: bool = False
