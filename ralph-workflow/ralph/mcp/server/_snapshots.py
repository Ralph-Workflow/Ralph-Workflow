"""MCP session snapshot — persists session_id across respawns."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def snapshot_dir(workspace_root: Path, bridge_run_id: str) -> Path:
    """Return <workspace_root>/.agent/tmp/mcp-snapshots/<bridge_run_id>."""
    snap = workspace_root / ".agent" / "tmp" / "mcp-snapshots" / bridge_run_id
    snap.mkdir(parents=True, exist_ok=True)
    return snap


def _finger(broker_secret: str | None) -> str | None:
    """Return SHA-256 of broker_secret[:8] if broker_secret is truthy, else None."""
    if not broker_secret:
        return None
    return hashlib.sha256(broker_secret[:8].encode()).hexdigest()


def write_snapshot(
    snapshot_dir: Path,
    *,
    session_id: str,
    run_id: str,
    broker_secret_fingerprint: str | None,
) -> None:
    """Write session.json under snapshot_dir.

    Atomic write: write to a temp file in the same dir, then os.replace().
    Uses json.dumps with sort_keys=True.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "run_id": run_id,
        "broker_secret_fingerprint": broker_secret_fingerprint,
    }
    tmp = snapshot_dir / ".session.json.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    tmp.replace(snapshot_dir / "session.json")


def read_snapshot(snapshot_dir: Path) -> dict[str, str]:
    """Read session.json and return the dict. If file missing, return {}."""
    path = snapshot_dir / "session.json"
    if not path.exists():
        return {}
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return dict(raw)
    except (OSError, ValueError):
        return {}
