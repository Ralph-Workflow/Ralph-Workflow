from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import MCP_SNAPSHOT_DIR_ENV

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.mcp_models import McpConfig


def snapshot_dir(
    workspace_root: Path,
    bridge_run_id: str,
    *,
    override_dir: Path | None = None,
    env_getter: Callable[[str], str | None] | None = None,
) -> Path:
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
    if not broker_secret:
        return None
    return hashlib.sha256(broker_secret[:8].encode()).hexdigest()


def snapshot_inputs(workspace_root: Path, snap_dir: Path) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    mcp_toml = workspace_root / "mcp.toml"
    if mcp_toml.is_file():
        target_toml = snap_dir / "mcp.toml"
        shutil.copy2(mcp_toml, target_toml)


def write_snapshot(
    snapshot_dir: Path,
    *,
    session_id: str,
    run_id: str,
    broker_secret_fingerprint: str | None,
    effective_config_fingerprint: str | None = None,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "run_id": run_id,
        "broker_secret_fingerprint": broker_secret_fingerprint,
        "effective_config_fingerprint": effective_config_fingerprint,
    }
    tmp = snapshot_dir / ".session.json.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    tmp.replace(snapshot_dir / "session.json")


def read_snapshot(snapshot_dir: Path) -> dict[str, str]:
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


def compute_effective_config_fingerprint(workspace_root: Path) -> str | None:
    from ralph.config.mcp_loader import load_mcp_config
    from ralph.workspace.scope import WorkspaceScope

    try:
        scope = WorkspaceScope(workspace_root)
        cfg: McpConfig = load_mcp_config(workspace_scope=scope)
    except Exception:
        return None
    try:
        canonical = cfg.model_dump_json()
    except Exception:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "_finger",
    "compute_effective_config_fingerprint",
    "read_snapshot",
    "snapshot_dir",
    "snapshot_inputs",
    "write_snapshot",
]
