from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class AsyncProcessStreams:
    """Async process I/O streams."""

    stdin: asyncio.StreamWriter | None = None
    stdout: asyncio.StreamReader | None = None
    stderr: asyncio.StreamReader | None = None
