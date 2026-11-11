"""Atomic and safe writer for ralph-workflow-agents.toml config files.

Ensures TOML validity before write, backs up existing configurations when changed,
and performs atomic file replacement via temporary files.
"""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


def write_agents_toml(path: Path, text: str, *, trigger: str) -> bool:
    """Validate TOML and atomically write text to path.

    Parameters
    ----------
    path:
        Target file path for the agents configuration.
    text:
        TOML formatted string content to write.
    trigger:
        Description or identifier of why the write was initiated.

    Returns
    -------
    bool:
        True if the file was written, False if existing content was identical.

    Raises
    ------
    ValueError:
        If text is not valid TOML.
    """
    path = Path(path)

    try:
        tomllib.loads(text)
    except ValueError as err:
        raise ValueError(f"Invalid TOML content for {path}: {err}") from err

    bak_path = path.with_name(f"{path.name}.bak")

    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        if existing_text == text:
            return False
        bak_path.write_bytes(  # filesystem-write-ok: backup copy of existing agents config file before atomic overwrite
            path.read_bytes()
        )

    path.parent.mkdir(  # filesystem-write-ok: ensuring target directory exists before writing agents config
        parents=True, exist_ok=True
    )
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(  # filesystem-write-ok: transient scratch file written before atomic replace
            fd, "w", encoding="utf-8"
        ) as handle:
            handle.write(text)
            handle.flush()
        # filesystem-write-ok: atomic-replace boundary for agents config file
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(  # filesystem-write-ok: removing transient scratch file on failure
            missing_ok=True
        )
        raise

    logger.info("Wrote %s (trigger: %s, backup: %s)", path, trigger, bak_path)
    return True
