"""Timeout exception that records which process deadline expired."""

from __future__ import annotations

import subprocess
from typing import Literal


class DeadlineTimeoutExpired(subprocess.TimeoutExpired):
    """Timeout carrying the deadline that expired."""

    def __init__(
        self,
        cmd: list[str],
        timeout: float,
        *,
        timeout_cause: Literal["inactivity", "hard_cap"],
        output: bytes | None = None,
        stderr: bytes | None = None,
    ) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.timeout_cause = timeout_cause


__all__ = ["DeadlineTimeoutExpired"]
