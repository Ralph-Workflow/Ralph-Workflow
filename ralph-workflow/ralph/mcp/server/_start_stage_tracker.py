from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.env import MCP_STAGE_ID_ENV

if TYPE_CHECKING:
    from collections.abc import Callable

type EnvGetter = Callable[[str], str | None]

_STAGE_FILE_NAME_COMPONENT: str = ".agent/.ralph_mcp_start_stage"


def _initial_spawn_id(env_getter: EnvGetter = os.environ.get) -> str:
    stage_id = env_getter(MCP_STAGE_ID_ENV)
    return stage_id if stage_id else str(uuid.uuid4())


_spawn_id_holder: list[str] = [_initial_spawn_id()]


def _get_spawn_id() -> str:
    return _spawn_id_holder[0]


def reset_spawn_id() -> None:
    _spawn_id_holder[0] = str(uuid.uuid4())


def stage_file_path(root: Path, *, spawn_id: str | None = None) -> Path:
    sid = spawn_id if spawn_id is not None else _get_spawn_id()
    return root / f"{_STAGE_FILE_NAME_COMPONENT}.{sid}"


def _do_record(root: Path, stage: str) -> None:
    path = stage_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".tmp.{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, stage.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp_path.replace(path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
    if timeout_s is None:
        _do_record(root, stage)
        return

    if timeout_s <= 0.0:
        raise TimeoutError(f"stage recording timed out (budget {timeout_s:.3f}s <= 0)")

    error: list[BaseException] = []

    def _worker() -> None:
        try:
            _do_record(root, stage)
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(
        target=_worker,
        name="ralph-mcp-stage-record",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=max(0.0, timeout_s))
    if thread.is_alive():
        raise TimeoutError(f"stage recording timed out after {timeout_s:.3f}s")
    if error:
        raise error[0]


def read_last_stage(root: Path, *, spawn_id: str | None = None) -> str | None:
    path = stage_file_path(root, spawn_id=spawn_id)
    try:
        return path.read_text(encoding="ascii").strip() or None
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return None


RUNTIME_STAGES: tuple[str, ...] = (
    "env_loaded",
    "session_ready",
    "config_loading",
    "config_loaded",
    "upstream_catalog_or_discovery_done",
    "registry_built",
    "ready_to_serve",
)


_DEFAULT_DIAGNOSTIC_TIMEOUT_S: float = 0.2


def record_overrun(
    root: Path,
    stage: str,
    elapsed_s: float,
    budget_s: float,
    remaining_s: float,
    headroom_s: float | None = None,
    *,
    timeout_s: float | None = None,
) -> None:
    path = stage_file_path(root)
    overruns_path = Path(str(path) + ".overruns")
    _warn_id: str = uuid.uuid4().hex
    _now: float = time.time()
    record: dict[str, object] = {
        "stage": stage,
        "elapsed_s": elapsed_s,
        "budget_s": budget_s,
        "remaining_s": remaining_s,
        "headroom_s": headroom_s,
        "observed_at": _now,
        "warning_id": _warn_id,
    }
    line: str = json.dumps(record, separators=(",", ":")) + "\n"
    eff_timeout_s = timeout_s if timeout_s is not None else _DEFAULT_DIAGNOSTIC_TIMEOUT_S

    def _write_record() -> None:
        try:
            overruns_path.parent.mkdir(parents=True, exist_ok=True)
            with overruns_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            pass

    diag_thread = threading.Thread(
        target=_write_record,
        name="ralph-mcp-overrun-record",
        daemon=True,
    )
    diag_thread.start()
    diag_thread.join(timeout=max(0.0, eff_timeout_s))


def read_overruns(root: Path, *, spawn_id: str | None = None) -> list[dict[str, object]]:
    path = stage_file_path(root, spawn_id=spawn_id)
    overruns_path = Path(str(path) + ".overruns")
    try:
        content = overruns_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return []

    records: list[dict[str, object]] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
            parsed: object = json.loads(stripped)
            if isinstance(parsed, dict):
                records.append(cast("dict[str, object]", parsed))
    return records


__all__ = [
    "RUNTIME_STAGES",
    "read_last_stage",
    "read_overruns",
    "record",
    "record_overrun",
    "reset_spawn_id",
    "stage_file_path",
]
