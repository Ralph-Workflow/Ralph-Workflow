
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _HasSrcPath(Protocol):

    src_path: str
