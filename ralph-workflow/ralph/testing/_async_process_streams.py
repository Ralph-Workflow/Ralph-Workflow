from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio


@dataclass
class AsyncProcessStreams:
    """Async process I/O streams."""

    stdin: asyncio.StreamWriter | None = None
    stdout: asyncio.StreamReader | None = None
    stderr: asyncio.StreamReader | None = None
