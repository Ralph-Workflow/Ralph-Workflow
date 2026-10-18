"""Startup polling and bounded pre-serve helpers for the MCP server runtime.

Extracted from ``ralph.mcp.server.runtime`` to keep the module under the
1000-line repo-structure limit.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.protocol.session import McpSession
    from ralph.mcp.server.runtime import McpServerExtras
    from ralph.workspace.scope import WorkspaceScope

    class McpConfigLoader(Protocol):
        def __call__(
            self,
            workspace_scope: WorkspaceScope | None = None,
            config_path: Path | None = None,
        ) -> McpConfig: ...


from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.protocol._startup_timeouts import mcp_real_startup_deadline_from_env
from ralph.mcp.protocol.env import MCP_TEST_REGISTRY_BUILD_GATE_ENV
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._pre_serve_overrun import (
    PRE_SERVE_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.workspace.fs import FsWorkspace

_REGISTRY_BUILD_GATE_HARD_DEADLINE_S: float = 0.05


def _workspace_mcp_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp.toml"


def _poll_registry_build_gate(
    env_map: Mapping[str, str],
    t0: float,
    budget_s: float,
    *,
    clock_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Block on a sentinel file; raise typed ``McpServerError`` on deadline.

    Test seam paralleling ``_FallbackStandaloneServer``'s
    ``MCP_TEST_PRE_SERVE_GATE_ENV`` poll. Production never sets the
    env var, so real runs return immediately. When the env var names a
    sentinel path, the gate polls until either the file appears OR
    the deadline fires.
    """
    gate_path = env_map.get(MCP_TEST_REGISTRY_BUILD_GATE_ENV)
    if not gate_path:
        return
    gate_path_obj = Path(gate_path)
    effective_deadline_s = min(budget_s, _REGISTRY_BUILD_GATE_HARD_DEADLINE_S)
    while not DEFAULT_FILE_BACKEND.exists(
        gate_path_obj
    ):  # filesystem-read-ok: subprocess-E2E test seam polls a path the test fixture creates on its side; routed through the shared FileBackend seam
        elapsed = clock_fn() - t0
        if elapsed >= effective_deadline_s:
            raise McpServerError(
                f"registry_built budget exceeded at gate ({elapsed:.3f}s >= {effective_deadline_s:.3f}s)",
                last_stage="registry_built",
                elapsed_s=elapsed,
                restart_count=0,
            )
        # filesystem-poll-ok: test-only registry-build gate polls a sentinel file the subprocess-E2E fixture creates; production never sets this env, and the loop is deadline-bounded
        time.sleep(0.02)


def _poll_bounded_pre_serve(
    *,
    workspace_root: Path,
    effective_session: McpSession,
    workspace: FsWorkspace | None,
    extras: McpServerExtras,
    env_map: Mapping[str, str],
    t0: float,
    load_mcp_config_fn: McpConfigLoader = load_mcp_config,
    clock_fn: Callable[[], float] = time.monotonic,
) -> tuple[FsWorkspace, McpConfig]:
    """Run the pre-serve critical path with a wall-clock deadline.

    Executes allowed_roots access, FsWorkspace construction, and load_mcp_config
    inside a ThreadPoolExecutor worker thread and raises McpServerError
    (last_stage="config_loading") if the budget is exceeded.
    """
    real_deadline_s = mcp_real_startup_deadline_from_env(env_map)
    remaining_s = max(0.0, real_deadline_s - (clock_fn() - t0))
    effective_timeout_s = remaining_s

    def _do_pre_serve() -> tuple[FsWorkspace, McpConfig]:
        allowed_roots = cast(
            "tuple[Path, ...]", getattr(effective_session, "allowed_roots", ())
        )  # cast-policy: seam: structural boundary
        ws = (
            workspace
            if workspace is not None
            else (
                extras.workspace
                if extras.workspace is not None
                else FsWorkspace(
                    workspace_root, allowed_roots=allowed_roots if allowed_roots else None
                )
            )
        )
        cfg = (
            extras.mcp_config
            if extras.mcp_config is not None
            else load_mcp_config_fn(config_path=_workspace_mcp_config_path(workspace_root))
        )
        return (ws, cfg)

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(_do_pre_serve)
        return future.result(timeout=effective_timeout_s)
    except concurrent.futures.TimeoutError:
        elapsed = clock_fn() - t0
        fail_closed_pre_serve_overrun(  # never returns: ``os._exit``
            workspace_root,
            stage="config_loading",
            elapsed_s=elapsed,
            budget_s=effective_timeout_s,
            exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
        )
    except Exception:
        raise
    finally:
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)
