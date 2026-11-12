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
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for relative_path, payload in config_files:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def cleanup() -> None:
        shutil.rmtree(root, ignore_errors=True)

    return root, cleanup
