"""Pro support — engine-side integration with Ralph-Workflow-Pro.

Ralph-Workflow-Pro launches the engine as a subprocess and expects the
engine to honor a small, Pro-owned contract. This package implements the
engine's half of that contract:

- ``env`` — pure helpers that read the three env vars Pro is allowed to
  set on the engine (``RALPH_WORKFLOW_PRO``, ``RALPH_WORKSPACE``,
  ``PROMPT_PATH``). The contract limits Pro to exactly these three
  engine-facing env vars, so the engine MUST NOT require any additional
  variables.
- ``workspace`` — resolves the workspace root, preferring
  ``RALPH_WORKSPACE`` over the current working directory.
- ``prompt`` — resolves the operator-visible source prompt path,
  preferring ``PROMPT_PATH`` over ``<workspace>/PROMPT.md``. Callers
  operating on the materialised ``CURRENT_PROMPT.md`` MUST NOT use this
  resolver.
- ``marker`` — read-only reader for the Pro-owned
  ``<workspace>/.ralph/run.json`` marker file and an optional
  ``.ralph/heartbeat_token`` sidecar.
- ``heartbeat`` — bounded ``/api/heartbeat`` client. The client runs in a
  daemon thread, uses bounded ``httpx`` timeouts on every call, and is
  idempotent on ``stop()`` without ever joining the worker thread.

Engine invariants preserved by this package:

- The engine never writes to the marker file, the heartbeat sidecar, or
  any path under ``<workspace>/.ralph/``.
- The engine never modifies the operator-visible ``PROMPT.md`` during a
  Pro-mode run.
- The engine returns exit code 0 on clean completion and non-zero on
  failure regardless of whether it is running under Pro.
- All stdout/stderr output remains valid UTF-8 newline-terminated text.

The pro_support package is a thin, read-only, non-blocking layer. It does
not introduce global mutable state, does not register a singleton, and
does not perform I/O at import time.
"""

from ralph.pro_support.env import (
    PROMPT_PATH,
    RALPH_WORKFLOW_PRO,
    RALPH_WORKSPACE,
    get_prompt_path,
    get_ralph_workspace,
    is_pro_mode,
)
from ralph.pro_support.heartbeat import ProHeartbeatClient
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.marker import read_heartbeat_token, read_marker_file
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.pro_support.state_query import (
    PipelineStateSnapshot,
    SnapshotRegistry,
    build_pipeline_state_snapshot,
)
from ralph.pro_support.watcher import ProMarkerWatcher
from ralph.pro_support.workspace import resolve_pro_workspace

__all__ = [
    "PROMPT_PATH",
    "RALPH_WORKFLOW_PRO",
    "RALPH_WORKSPACE",
    "PipelineStateSnapshot",
    "ProHeartbeatClient",
    "ProMarkerWatcher",
    "ProPipelineHooks",
    "SnapshotRegistry",
    "build_pipeline_state_snapshot",
    "get_prompt_path",
    "get_ralph_workspace",
    "is_pro_mode",
    "read_heartbeat_token",
    "read_marker_file",
    "resolve_effective_prompt_path",
    "resolve_pro_workspace",
]
