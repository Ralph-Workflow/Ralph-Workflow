"""Owned private configuration roots for native MCP transports."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def prepare_private_config_root(
    config_files: tuple[tuple[Path, bytes], ...], *, prefix: str
) -> tuple[Path, Callable[[], None]]:
    """Create a temporary root containing only the supplied native config files."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for relative_path, payload in config_files:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)  # filesystem-write-ok: invocation-owned temporary config root

    def cleanup() -> None:
        """Remove the invocation-owned configuration root."""
        shutil.rmtree(root, ignore_errors=True)  # filesystem-write-ok: invocation-owned temporary config root

    return root, cleanup
