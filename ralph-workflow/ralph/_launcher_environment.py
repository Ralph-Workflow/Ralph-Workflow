from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli import main as cli_main
from ralph.config import _env as config_env

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _same_directory(entry: str, directory: Path) -> bool:
    try:
        return Path(entry).resolve() == directory.resolve()
    except OSError:
        return False


def detach_runtime_virtualenv(environ: MutableMapping[str, str], runtime_prefix: Path) -> None:
    active = environ.get("VIRTUAL_ENV")
    if active and _same_directory(active, runtime_prefix):
        del environ["VIRTUAL_ENV"]
    search_path = environ.get("PATH")
    if search_path is None:
        return
    runtime_bin = runtime_prefix / "bin"
    environ["PATH"] = os.pathsep.join(
        entry
        for entry in search_path.split(os.pathsep)
        if not entry or not _same_directory(entry, runtime_bin)
    )


def launch() -> None:
    sys.argv[0] = "ralph"
    detach_runtime_virtualenv(config_env.environ(), Path(sys.prefix))
    cli_main.app()
