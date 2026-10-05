"""Change-aware READY cache for the project-policy readiness preflight.

The cache is the FAST PATH for the orchestrator: when the same project
state was already validated READY on a prior preflight, the validator is
skipped on the current preflight. The cache hashes the
:func:`ralph.project_policy.evidence.evidence_signature` of the project —
the same signature the validator iterates — so any edit OR deletion of
any evidence file invalidates a cached READY automatically.

The cache is stored at :data:`markers.CACHE_REL_PATH` (relative to the
workspace root) so it travels with the workspace but stays out of source
control. The cache only ever stores a READY status — remediation-required
and blocked states are never cached because they require an agent run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.project_policy import evidence, markers
from ralph.project_policy.models import ReadinessStatus

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

_STATUS_KEY = "status"
_SIGNATURE_KEY = "signature"


def _read_cache(workspace: Workspace) -> dict[str, str] | None:
    """Read the cache file via the workspace seam; return None when missing."""
    if not workspace.exists(markers.CACHE_REL_PATH):
        return None
    try:
        raw = workspace.read(markers.CACHE_REL_PATH)
    except FileNotFoundError:
        return None
    parsed: object
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    payload: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None
        payload[key] = value
    return payload


def read_cached_ready(workspace: Workspace, stack: ProjectStack) -> bool:
    """Return True only when the cache holds a matching READY signature.

    A cached READY whose signature does NOT match the current
    :func:`ralph.project_policy.evidence.evidence_signature` is treated as
    stale: any edit OR deletion of an evidence file changes the signature,
    so a stale cache can never return a false READY.
    """
    payload = _read_cache(workspace)
    if payload is None:
        return False
    if payload.get(_STATUS_KEY) != ReadinessStatus.READY.value:
        return False
    cached_signature = payload.get(_SIGNATURE_KEY)
    if not isinstance(cached_signature, str):
        return False
    current_signature = evidence.evidence_signature(workspace, stack)
    return cached_signature == current_signature


def write_cache(workspace: Workspace, stack: ProjectStack, status: ReadinessStatus) -> None:
    """Persist the current evidence signature and status to the cache file."""
    if status is not ReadinessStatus.READY:
        # We only cache READY. Any other status would be misleading.
        return
    payload = {
        _STATUS_KEY: status.value,
        _SIGNATURE_KEY: evidence.evidence_signature(workspace, stack),
    }
    cache_path = markers.CACHE_REL_PATH
    parent_dir = "/".join(cache_path.split("/")[:-1])
    if parent_dir:
        workspace.mkdirs(parent_dir)
    workspace.write(cache_path, json.dumps(payload, sort_keys=True))


__all__ = ["read_cached_ready", "write_cache"]
