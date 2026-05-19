from __future__ import annotations

from dataclasses import dataclass
from typing import IO


@dataclass
class ProcessStreams:
    """Process I/O streams."""

    stdin: IO[bytes] | None = None
    stdout: IO[bytes] | None = None
    stderr: IO[bytes] | None = None
