"""Read-only Pro marker file helpers.

The Pro↔Ralph contract reserves ``<workspace>/.ralph/run.json`` as a
Pro-owned file the engine MUST treat as read-only. The engine never
writes to it, never creates it, and never modifies it; it only reads
the file when it needs to learn the run id, the heartbeat port, or the
heartbeat token.

This module is the single place the engine reads the marker. Drift
detection (see ``make verify-drift``) prevents any other module from
referencing ``.ralph/run.json`` directly.

Marker schema (intentionally minimal — see contract §5):

- ``runId`` (string, required) — the run identifier the engine must
  include in ``/api/heartbeat`` posts.
- ``port`` (int, optional) — the local port Pro is listening on for
  ``/api/heartbeat``. Defaults to 7432 when absent.
- ``heartbeatToken`` (string, optional) — the bearer token to include
  in the heartbeat header / body. When absent the engine falls back to
  a sidecar file at ``<workspace>/.ralph/heartbeat_token``.

All public helpers return ``None`` on any error (missing, unreadable,
invalid JSON, OS errors) rather than raising. This keeps Pro-mode
soft-degrade behaviour intact: a missing or broken marker must not
crash a non-Pro invocation that happens to share a workspace layout.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)


MARKER_RELATIVE_PATH = Path(".ralph") / "run.json"
HEARTBEAT_TOKEN_RELATIVE_PATH = Path(".ralph") / "heartbeat_token"


def read_marker_file(workspace_root: Path | str) -> dict[str, object] | None:
    """Read and parse the Pro-owned marker file, or return ``None`` on any error.

    The engine MUST NOT write to this file. This function only opens
    the file for reading; on any failure (missing, OSError, invalid
    JSON, wrong shape) it logs at debug and returns ``None``.

    Args:
        workspace_root: Absolute or relative workspace root.
    """
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
    """Return the heartbeat token, or ``None`` when unavailable.

    Resolution order:

    1. ``marker['heartbeatToken']`` if present and non-empty.
    2. The sidecar file at ``<workspace>/.ralph/heartbeat_token`` (its
       stripped contents).
    3. ``None`` when both are absent or empty.

    The function never raises; it returns ``None`` on any error so a
    missing token cannot break the rest of the engine.
    """
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
    """Return the heartbeat port from the marker, or the default 7432.

    The default is part of the Pro↔Ralph contract — Pro listens on a
    deterministic port unless the operator explicitly overrides it via
    the marker.
    """
    if marker is None:
        return 7432
    port_obj = marker.get("port")
    if isinstance(port_obj, int) and port_obj > 0:
        return port_obj
    return 7432


def read_run_id(marker: dict[str, object] | None) -> str | None:
    """Return ``marker['runId']`` when present and a non-empty string."""
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
