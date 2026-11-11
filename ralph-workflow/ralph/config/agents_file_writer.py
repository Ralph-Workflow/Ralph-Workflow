
from __future__ import annotations

import contextlib
import logging
import os
import stat
import tempfile
import tomllib
from pathlib import Path

from loguru import logger

_std_logger = logging.getLogger(__name__)

_NEW_FILE_MODE = 0o644


def _read_existing_bytes(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _resolve_mode(target: Path) -> int:
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return _NEW_FILE_MODE


def _publish_backup(bak_path: Path, previous_bytes: bytes, directory: Path) -> None:
    staging = directory / f".{bak_path.name}.stage.{os.urandom(8).hex()}"
    descriptor = os.open(
        staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    try:
        staged = os.fdopen(
            descriptor, "wb"
        )
    except BaseException:
        os.close(descriptor)
        raise
    try:
        with staged:
            staged.write(previous_bytes)
            staged.flush()
        staging.replace(bak_path)
    except BaseException:
        with contextlib.suppress(OSError):
            staging.unlink(
                missing_ok=True
            )
        raise


def write_agents_toml(path: Path, text: str, *, trigger: str) -> bool:
    path = Path(path)

    try:
        tomllib.loads(text)
    except ValueError as err:
        raise ValueError(f"Invalid TOML content for {path}: {err}") from err

    new_bytes = text.encode("utf-8")
    write_target = path.resolve() if path.is_symlink() else path
    bak_path = path.with_name(f"{path.name}.bak")

    existing_bytes = _read_existing_bytes(write_target)
    if existing_bytes == new_bytes:
        return False

    file_mode = _resolve_mode(write_target)

    write_target.parent.mkdir(
        parents=True, exist_ok=True
    )
    tmp_path: Path | None = None
    try:
        fd, tmp_path_str = tempfile.mkstemp(
            dir=write_target.parent,
            prefix=f".{write_target.name}.tmp.",
        )
        tmp_path = Path(tmp_path_str)
        with os.fdopen(
            fd, "wb"
        ) as handle:
            handle.write(new_bytes)
            handle.flush()
        tmp_path.chmod(
            file_mode
        )
        if existing_bytes is not None:
            _publish_backup(bak_path, existing_bytes, write_target.parent)
        tmp_path.replace(write_target)
    except BaseException:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(
                    missing_ok=True
                )
        raise

    logger.info("Wrote {} (trigger: {}, backup: {})", path, trigger, bak_path)
    _std_logger.info("Wrote %s (trigger: %s, backup: %s)", path, trigger, bak_path)
    return True
