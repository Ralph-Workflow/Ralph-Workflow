"""MCP session snapshot — persists session_id across respawns."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import MCP_SNAPSHOT_DIR_ENV

if TYPE_CHECKING:
    from collections.abc import Callable


def snapshot_dir(
    workspace_root: Path,
    bridge_run_id: str,
    *,
    override_dir: Path | None = None,
    env_getter: Callable[[str], str | None] | None = None,
) -> Path:
    """Return protected snapshot dir outside the watched workspace.

    Protected location under ~/.cache/ralph-workflow/dev/mcp-snapshots/<bridge_run_id>
    so that workspace cleanup (commit_cleanup, git clean, workspace reset) cannot
    delete it mid-run (E2.5). When override_dir or env_getter is provided,
    that explicit location takes precedence (useful for test isolation).
    """
    override: str | None = None
    if override_dir is not None:
        snap = override_dir / bridge_run_id
    else:
        if env_getter is not None:
            override = env_getter(MCP_SNAPSHOT_DIR_ENV)
        if override:
            snap = Path(override) / bridge_run_id
        else:
            snap = (
                Path.home() / ".cache" / "ralph-workflow" / "dev" / "mcp-snapshots" / bridge_run_id
            )
    snap.mkdir(parents=True, exist_ok=True)
    return snap


def _finger(broker_secret: str | None) -> str | None:
    """Return SHA-256 of broker_secret[:8] if broker_secret is truthy, else None."""
    if not broker_secret:
        return None
    return hashlib.sha256(broker_secret[:8].encode()).hexdigest()


def snapshot_inputs(workspace_root: Path, snap_dir: Path) -> None:
    """Copy critical configuration and inputs into the protected snapshot directory.

    Ensures that candidate spawns retain their inputs even if .agent/tmp or config
    files in the workspace are modified or removed mid-run (E2.5).
    """
    snap_dir.mkdir(parents=True, exist_ok=True)
    mcp_toml = workspace_root / "mcp.toml"
    if mcp_toml.is_file():
        target_toml = snap_dir / "mcp.toml"
        # filesystem-write-ok: protected snapshot file written outside workspace
        shutil.copy2(mcp_toml, target_toml)


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
    # filesystem-write-ok: atomic write-via-tmp-then-replace for the bridge-owned session.json snapshot; the entire JSON payload is written at once, then tmp.replace() either fully replaces or does nothing, so the bridge-owned snapshot is never partially written.
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
        return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


__all__ = [
    "_finger",
    "read_snapshot",
    "snapshot_dir",
    "snapshot_inputs",
    "write_snapshot",
]
