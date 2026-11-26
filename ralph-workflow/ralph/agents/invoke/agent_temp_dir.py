from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND

if TYPE_CHECKING:
    from collections.abc import Iterator

_DARWIN_SHORT_TEMP_ROOT: Final[Path] = Path("/private/tmp")
_POSIX_SHORT_TEMP_ROOT: Final[Path] = Path("/") / "tmp"
_MAX_ROOT_LENGTH: Final[int] = 32
_PREFIX: Final[str] = "ra"


def _default_root() -> Path:
    if sys.platform == "darwin" and DEFAULT_FILE_BACKEND.exists(_DARWIN_SHORT_TEMP_ROOT):
        return _DARWIN_SHORT_TEMP_ROOT
    system_root = Path(tempfile.gettempdir())
    if len(str(system_root)) > _MAX_ROOT_LENGTH and DEFAULT_FILE_BACKEND.exists(
        _POSIX_SHORT_TEMP_ROOT
    ):
        return _POSIX_SHORT_TEMP_ROOT
    return system_root


@contextmanager
def private_agent_tmpdir(root: Path | None = None) -> Iterator[Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=root or _default_root()))
    try:
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if DEFAULT_FILE_BACKEND.exists(tmpdir):
            logger.warning("agent temp dir {} could not be fully removed", tmpdir)


__all__ = ["private_agent_tmpdir"]
