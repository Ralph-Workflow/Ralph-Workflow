
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)


MARKER_RELATIVE_PATH = Path(".ralph") / "run.json"
HEARTBEAT_TOKEN_RELATIVE_PATH = Path(".ralph") / "heartbeat_token"


def read_marker_file(workspace_root: Path | str) -> dict[str, object] | None:
    marker_path = Path(workspace_root).expanduser().resolve() / MARKER_RELATIVE_PATH
    try:
        raw = marker_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
        logger.debug("Pro marker not readable at %s: %s", marker_path, exc)
        return None
    try:
        parsed = cast("object", json.loads(raw))
    except json.JSONDecodeError as exc:
        logger.debug("Pro marker at %s is not valid JSON: %s", marker_path, exc)
        return None
    if not isinstance(parsed, dict):
        logger.debug("Pro marker at %s is not a JSON object", marker_path)
        return None
    return cast("dict[str, object]", parsed)


def read_heartbeat_token(workspace_root: Path | str) -> str | None:
    marker = read_marker_file(workspace_root)
    if marker is not None:
        token_obj = marker.get("heartbeatToken")
        if isinstance(token_obj, str) and token_obj:
            return token_obj

    sidecar_path = Path(workspace_root).expanduser().resolve() / HEARTBEAT_TOKEN_RELATIVE_PATH
    try:
        contents = sidecar_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
        logger.debug("Pro heartbeat sidecar not readable at %s: %s", sidecar_path, exc)
        return None
    return contents or None


def read_heartbeat_port(marker: dict[str, object] | None) -> int:
    if marker is None:
        return 7432
    port_obj = marker.get("port")
    if isinstance(port_obj, int) and port_obj > 0:
        return port_obj
    return 7432


def read_run_id(marker: dict[str, object] | None) -> str | None:
    if marker is None:
        return None
    run_id_obj = marker.get("runId")
    if isinstance(run_id_obj, str) and run_id_obj:
        return run_id_obj
    return None


__all__ = [
    "HEARTBEAT_TOKEN_RELATIVE_PATH",
    "MARKER_RELATIVE_PATH",
    "read_heartbeat_port",
    "read_heartbeat_token",
    "read_marker_file",
    "read_run_id",
]
