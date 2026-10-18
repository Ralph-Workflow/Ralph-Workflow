"""Standalone MCP HTTP server runtime for Ralph tools.

Runs the Ralph MCP server as a long-lived HTTP process that AI agents connect
to over the MCP protocol. The server exposes Ralph's tool registry (file
operations, git commands, artifact submission, coordination, etc.) through
the production streamable-HTTP transport (``_FallbackStandaloneServer``).

The architecture is intentionally single-path: there is exactly one server
transport — the production ``_FallbackHttpHandler`` (constructed by
``_FallbackStandaloneServer``) — and every behavior (tool dispatch,
streaming, session handling, concurrency control, error framing) lives on
that one path. See ``docs/agents/architecture.md`` for the rationale.

Key responsibilities:

- ``RalphmcpServer`` - the main server class; call ``start(config)`` to launch
  and ``stop()`` to shut down gracefully. A health-check endpoint listens on
  ``/health``; liveness is polled by ``ralph.process.mcp_supervisor``.
- Environment handshake: the server reads ``MCP_SESSION`` (session JSON) and
  ``MCP_SESSION_FILE`` env vars to populate the agent session, which governs
  which capabilities and upstream MCP servers are enabled.
- Tool capability filtering: tools are registered or skipped based on the
  session's declared ``McpCapability`` set so each agent only sees the tools
  it needs.
- Upstream MCP registry: ``load_upstream_mcp_servers`` discovers additional
  MCP servers from ``UPSTREAM_MCP_CONFIG`` and mounts them alongside Ralph
  tools.

The server is launched by ``ralph.process.manager`` via the
``ralph-mcp`` entry point (``ralph/mcp/server/__main__.py``).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import __version__
from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
)
from ralph.mcp.protocol._startup_timeouts import (
    _CONFIG_LOAD_CUMULATIVE_S,
    _PRE_SERVE_BUDGET_S,
    mcp_discovery_budget_from_env,
    mcp_pre_serve_budget_from_env,
    mcp_preflight_timeout_from_env,
    mcp_registry_built_budget_from_env,
    mcp_registry_read_timeout_from_env,
    mcp_whole_startup_budget_from_env,
    record_stage_elapsed,
)
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability
from ralph.mcp.protocol.env import (
    MCP_OWNER_PID_ENV,
    MCP_RESPAWN_ENV,
    MCP_TEST_REGISTRY_BUILD_GATE_ENV,
)
from ralph.mcp.protocol.session import AgentSession, McpSession
from ralph.mcp.server._activity_relay_sender import (
    ActivityRelaySender,
    scrub_activity_relay_environment,
)
from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._pre_serve_overrun import (
    PRE_SERVE_OVERRUN_EXIT_CODE,
    SESSION_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.server._process_secrecy import (
    erase_broker_secret_environment,
    protect_broker_secret,
)
from ralph.mcp.server._reserved_socket import _adopt_inherited_socket
from ralph.mcp.server._runtime_constants import (
    DEFAULT_HOST,
    DEFAULT_MOUNT_PATH,
    DEFAULT_PORT,
    DEFAULT_TRANSPORT,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._standalone_http_server import _StandaloneHttpServer
from ralph.mcp.server._start_stage_tracker import record as _record_stage
from ralph.mcp.server.runtime_session import FileBackedSession, session_from_env
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.transport.common import mcp_config_as_upstreams, merge_mcp_toml_into_upstreams
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    load_upstream_tool_catalog,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.mcp.upstream.tool_catalog_cache import publish_tool_catalog_to_parent
from ralph.mcp.upstream.validation import strict_mode_from_env
from ralph.process._spawn_env import sanitize_process_environment
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable, Mapping, MutableMapping, Sequence

    from ralph.config.mcp_models import McpConfig


def _log_and_record_stage(workspace_root: Path, name: str) -> None:
    """Emit one flushed ``logger.info`` line and write it to the stage file.

    R4-LOGS: every startup stage is logged as a flushed ``logger.info``
    line AND recorded to the stage-tracker file the supervisor reads on
    preflight failure. The two actions are always coupled so the operator
    has both the real-time stream and the on-disk record.
    """
    logger.info("ralph-mcp startup: stage={stage}", stage=name)
    _record_stage(workspace_root, name)


@dataclass(frozen=True)
class McpServerExtras:
    """Optional DI parameters for building standalone MCP servers.

    The dataclass is the dependency-injection bundle threaded through
    ``build_standalone_http_server`` and ``run_standalone_server``.
    Every field is optional (``None`` means "use the production
    default") so callers can override exactly one seam (e.g. an
    upstream registry for a test) without rebuilding the rest of the
    composition root.

    Attributes:
        session: Pre-built ``McpSession`` (handshake, capabilities,
            upstream mounts). When ``None`` the runtime reads
            ``MCP_SESSION`` / ``MCP_SESSION_FILE`` from the environment
            via ``session_from_env``.
        upstream_registry: Pre-loaded ``UpstreamRegistry`` for
            upstream MCP servers. When ``None`` the runtime loads the
            registry from ``UPSTREAM_MCP_CONFIG`` /
            ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` via
            ``load_runtime_upstream_servers``.
        mcp_config: Pre-parsed ``McpConfig`` (mcp.toml model). When
            ``None`` the runtime calls ``load_mcp_config`` with the
            user-global path.
    """

    session: McpSession | None = None
    upstream_registry: UpstreamRegistry | None = None
    mcp_config: McpConfig | None = None
    workspace: FsWorkspace | None = None


FallbackStandaloneServer = _FallbackStandaloneServer

# Test seam for stage-deadline measurement: tests monkeypatch this to a fake
# monotonic clock (see test_mcp_server_startup_stages_observable).
_clock = time.monotonic

# Hard ceiling for ``MCP_TEST_REGISTRY_BUILD_GATE_ENV`` polling. The
# production deadline comes from the caller's ``budget_s`` argument
# (the whole-startup envelope), but a subprocess-E2E test that
# blocks the gate on purpose would otherwise tie up a single
# test-shard slot for up to ``budget_s`` seconds. This hard ceiling
# keeps the gate's worst-case wall-clock bounded so a single
# subprocess-E2E test does not starve the immutable 60-second
# combined test budget enforced by ``ralph/verify.py``. The
# production behavior is unchanged (production never sets the env
# var, so the gate returns immediately).
_REGISTRY_BUILD_GATE_HARD_DEADLINE_S: float = 0.05

# Import-time invariant: whole startup budget must stay strictly below
# preflight timeout (if/raise, not assert, so it survives python -O).
if not (mcp_whole_startup_budget_from_env() < mcp_preflight_timeout_from_env().total_seconds()):
    raise RuntimeError(
        "import-time invariant violated: mcp_whole_startup_budget_from_env() "
        "must be strictly less than mcp_preflight_timeout_from_env()"
    )


def _check_cumulative_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str],
) -> None:
    """Enforce cumulative spawn-to-serving startup deadline at stage boundary.

    Checks elapsed monotonic time from `t0` against `mcp_whole_startup_budget_from_env(env_map)`.
    Raises typed `McpServerError` with `last_stage=stage` and `elapsed_s` when exceeded.
    Uses `if`/`raise` (not `assert`) so the check survives `python -O`.
    """
    elapsed = _clock() - t0
    whole_budget = mcp_whole_startup_budget_from_env(env_map)
    if elapsed > whole_budget:
        raise McpServerError(
            f"cumulative startup deadline exceeded at stage {stage!r} "
            f"({elapsed:.3f}s > {whole_budget:.3f}s)",
            last_stage=stage,
            elapsed_s=elapsed,
            restart_count=0,
        )


def standalone_session_identity(agent_transport: str | None) -> MultimodalModelIdentity:
    """Return the model identity for a standalone server's own session.

    ``agent_transport`` names the agent CLI on the other end. The
    provider stays unresolved -- a standalone server genuinely does not
    know which model will be used -- but the transport is enough for the
    delivery guards that key on the CLI. Omitted, the identity stays
    fully unknown, which "unresolvable -> capable" treats as permissive.
    """
    # Lowercased as well as stripped: this is the THIRD seam that writes
    # a transport spelling into an identity, and the other two
    # canonicalise. The guards all strip and lower before matching, so a
    # shouted value still works -- but the spelling reaches the session
    # file and the wire-ledger capability digest, and two spellings of
    # one CLI produced two different digests for the same run.
    declared = (agent_transport or "").strip().lower()
    if not declared:
        return UNKNOWN_IDENTITY
    return MultimodalModelIdentity(
        provider=UNKNOWN_IDENTITY.provider,
        model_id=None,
        transport=declared,
    )


def _build_mcp_registry_and_server(
    *,
    effective_session: McpSession,
    workspace: FsWorkspace,
    upstream_reg: UpstreamRegistry | None,
    upstream_servers: Sequence[UpstreamMcpServer],
    mcp_cfg: McpConfig,
    activity_relay_sender: ActivityRelaySender | None,
    t0: float,
    env_map: Mapping[str, str],
) -> McpServer:
    """Construct the Ralph tool registry and wrap it in ``McpServer``.

    Synchronous, no daemon thread, no ``join(timeout=)``: the previous
    implementation used a daemon thread and only recorded
    ``registry_built`` after the ``join`` timed out, which is the
    workaround the plan expressly prohibited. The deadline is enforced
    by ``_poll_registry_build_gate`` at the McpServer constructor
    boundary, where the test seam can simulate a blocked constructor
    and surface a typed ``McpServerError`` with
    ``last_stage="registry_built"`` BEFORE the parent's preflight
    closes.

    The gate is polled BETWEEN the tool-registry build (and its
    "MCP server started with N built-in tools" log) and the
    ``McpServer(...)`` call. This is the exact constructor boundary:
    when the gate fires, the ``McpServer.__init__`` is never called,
    so a real constructor block (one that hangs in ``__init__``)
    is exercised end-to-end — the child exits with a typed
    ``McpServerError`` and the parent's preflight surfaces
    ``last_stage="registry_built"`` from the stage file the caller
    wrote before invoking this function.
    """
    registry_budget_s = mcp_registry_built_budget_from_env(env_map)
    whole_budget_s = mcp_whole_startup_budget_from_env(env_map)
    record_stage_elapsed("registry_built", _clock() - t0, budget_s=registry_budget_s)
    _check_cumulative_startup_deadline("registry_built", t0, env_map)

    remaining_budget_s = max(0.0, min(registry_budget_s, whole_budget_s) - (_clock() - t0))
    read_timeout_ceiling_s = mcp_registry_read_timeout_from_env(env_map)
    effective_read_timeout_s = min(remaining_budget_s, read_timeout_ceiling_s)

    reg = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
        t0=t0,
        budget_s=registry_budget_s,
        timeout_s=effective_read_timeout_s,
    )
    record_stage_elapsed("registry_built", _clock() - t0, budget_s=registry_budget_s)
    _check_cumulative_startup_deadline("registry_built", t0, env_map)
    n_builtin = len(list(reg.list_definitions()))
    if upstream_reg and upstream_servers:
        n_proxied = len(list(upstream_reg.tool_definitions()))
        n_servers = len(upstream_servers)
        logger.info(
            "MCP server started with {n} built-in tools + "
            "{m} proxied upstream tools from {k} servers",
            n=n_builtin,
            m=n_proxied,
            k=n_servers,
        )
    else:
        logger.info("MCP server started with {n} built-in tools", n=n_builtin)

    # R2 blocked-operation seam: poll the gate AT THE McpServer
    # constructor boundary. If the gate fires, ``McpServer.__init__``
    # is never called, so a real constructor block is exercised. The
    # gate's deadline uses the WHOLE-STARTUP envelope so the child
    # can surface a typed ``McpServerError`` with
    # ``last_stage="registry_built"`` BEFORE the parent's preflight
    # closes. Production never sets ``MCP_TEST_REGISTRY_BUILD_GATE_ENV``,
    # so the gate returns immediately in the fast path.
    whole_startup_budget_s = mcp_whole_startup_budget_from_env(env_map)
    _poll_registry_build_gate(env_map, t0, whole_startup_budget_s)

    return McpServer(
        effective_session,
        workspace,
        reg,
        mcp_activity_sink=(
            activity_relay_sender.emit if activity_relay_sender is not None else None
        ),
    )


def _poll_registry_build_gate(
    env_map: Mapping[str, str],
    t0: float,
    budget_s: float,
) -> None:
    """Block on a sentinel file; raise typed ``McpServerError`` on deadline.

    Test seam paralleling ``_FallbackStandaloneServer``'s
    ``MCP_TEST_PRE_SERVE_GATE_ENV`` poll. Production never sets the
    env var, so real runs return immediately. When the env var names a
    sentinel path, the gate polls until either the file appears OR
    the deadline fires. The deadline is the *minimum* of
    ``budget_s`` (the production whole-startup envelope, supplied by
    the call site so a real constructor block surfaces a typed
    ``last_stage="registry_built"`` error BEFORE the parent's
    preflight closes) and ``_REGISTRY_BUILD_GATE_HARD_DEADLINE_S``
    (a small hard ceiling so the gate cannot starve the in-shard
    test budget when ``budget_s`` is itself large — e.g. the
    default 29.8 s whole_startup envelope). The hard ceiling keeps
    the gate's worst-case wall-clock in the sub-second range so a
    single subprocess-E2E test does not tip the 60 s combined test
    budget. On deadline, the gate raises a typed ``McpServerError``
    with ``last_stage="registry_built"`` and the measured
    ``elapsed_s`` so the parent's preflight surfaces the stage
    attribution rather than the generic 30 s timeout.
    ``if``/``raise`` is used (not ``assert``) so the deadline
    check survives ``python -O``.
    """
    gate_path = env_map.get(MCP_TEST_REGISTRY_BUILD_GATE_ENV)
    if not gate_path:
        return
    gate_path_obj = Path(gate_path)
    # The hard ceiling bounds the gate's worst-case wall-clock
    # independently of ``budget_s`` so a single subprocess-E2E test
    # never starves the 60 s combined test budget, while the
    # ``budget_s`` argument preserves the production
    # "raise-before-preflight" contract for the typical case where
    # the parent's preflight is the actual binding constraint.
    effective_deadline_s = min(budget_s, _REGISTRY_BUILD_GATE_HARD_DEADLINE_S)
    while not DEFAULT_FILE_BACKEND.exists(
        gate_path_obj
    ):  # filesystem-read-ok: subprocess-E2E test seam polls a path the test fixture creates on its side; routed through the shared FileBackend seam
        elapsed = _clock() - t0
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
) -> tuple[FsWorkspace, McpConfig]:
    """Run the pre-serve critical path with a wall-clock deadline.

    Executes allowed_roots access, FsWorkspace construction, and load_mcp_config
    inside a ThreadPoolExecutor worker thread and raises McpServerError
    (last_stage="config_loading") if the budget is exceeded.  Event.wait()
    (which releases the GIL) is used in test doubles so the timeout fires;
    path.open("rb") on a FIFO holds the GIL and cannot be interrupted.
    """
    pre_serve_budget = mcp_pre_serve_budget_from_env(env_map)
    whole_budget_s = mcp_whole_startup_budget_from_env(env_map)
    remaining_s = max(0.0, whole_budget_s - (_clock() - t0))
    effective_timeout_s = min(pre_serve_budget.total_seconds(), remaining_s)

    def _do_pre_serve() -> tuple[FsWorkspace, McpConfig]:
        # allowed_roots access triggers FileBackedSession._load() which
        # does blocking stat() + read_text() on the session file.
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
            else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
        )
        return (ws, cfg)

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        # ThreadPoolExecutor (not bare daemon Thread) is required by the
        # resource-lifecycle audit; max_workers=1 serialises all blocking
        # operations so the budget covers the whole sequence.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(_do_pre_serve)
        return future.result(timeout=effective_timeout_s)
    except concurrent.futures.TimeoutError:
        # Worker is wedged on real I/O holding the GIL;
        # ``shutdown(wait=False)`` cannot interrupt it (see ``_pre_serve_overrun``).
        elapsed = _clock() - t0
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
            ex.shutdown(wait=False)  # do not accumulate executor threads across calls


def build_standalone_http_server(
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    workspace: FsWorkspace | None = None,
    extras: McpServerExtras | None = None,
    agent_transport: str | None = None,
    activity_relay_sender: ActivityRelaySender | None = None,
    env: Mapping[str, str] | None = None,
    parent_socket: socket.socket | None = None,
    t0: float | None = None,
) -> _StandaloneHttpServer:
    """Build a standalone HTTP MCP server backed by the Ralph tool registry.

    ``agent_transport`` names the agent CLI this server will be serving
    (distinct from the MCP wire transport). Some delivery decisions key
    on the CLI rather than on the provider, so a server that does not
    know what it serves cannot make them: an operator pointing a
    restricted CLI at a manually started server would be handed content
    that kills its turn. There is no way to infer this, so it is
    declared -- and omitted, the server keeps the permissive default.

    SCOPE: the declaration reaches the session this function MINTS. A
    caller that supplies its own session in ``extras`` is stating who it
    is talking to, and that session's identity is used as given --
    ``agent_transport`` does not override it. ``run_standalone_server``
    is the path that always honours the declaration: it threads it into
    ``session_from_env``, which applies it to both the file-backed and
    the JSON handshake shapes.
    """
    _extras = extras or McpServerExtras()
    env_map = os.environ if env is None else env
    if t0 is None:
        t0 = _clock()
    effective_session = _extras.session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
        model_identity=standalone_session_identity(agent_transport),
    )
    # An environment-handshake session gets the declaration inside
    # ``session_from_env``, which applies it to both the file-backed and
    # the JSON shape; the minted fallback above gets it from
    # ``standalone_session_identity``. Nothing is needed here.

    # R2: bound the synchronous pre-serve critical path (allowed_roots access,
    # FsWorkspace construction, load_mcp_config). A hung filesystem (NFS,
    # container, sandboxed mount) would otherwise block the child indefinitely.
    # Record config_loading BEFORE the bounded block so the stage file captures
    # where the child was when the budget fires. ThreadPoolExecutor (not bare
    # daemon Thread) is required by the resource-lifecycle audit.
    _log_and_record_stage(workspace_root, "config_loading")
    _check_cumulative_startup_deadline("config_loading", t0, env_map)
    effective_workspace, mcp_cfg = _poll_bounded_pre_serve(
        workspace_root=workspace_root,
        effective_session=effective_session,
        workspace=workspace,
        extras=_extras,
        env_map=env_map,
        t0=t0,
    )
    _log_and_record_stage(workspace_root, "config_loaded")
    record_stage_elapsed(
        "config_loaded",
        _clock() - t0,
        budget_s=_CONFIG_LOAD_CUMULATIVE_S,
    )
    _check_cumulative_startup_deadline("config_loaded", t0, env_map)

    # Keep this composition seam unary: callers and test doubles can replace
    # it without knowing which environment source the standalone runtime uses.
    # The production loader defaults to ``os.environ``; ``env_map`` remains
    # the explicit source for the adjacent cached-catalog lookup below.
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    # R2: presence-aware catalog load. The env var is the canonical
    # presence signal: SET (even if the value is "{}") means the parent
    # has an authoritative snapshot and the child MUST NOT probe upstreams;
    # UNSET means the catalog is absent and the child must discover. The
    # 3-state contract (absent / present-empty / present-with-tools) is
    # defined in ralph/mcp/upstream/tool_catalog_cache.py:UpstreamToolCatalogSnapshot
    # and materialized at the parent end by apply_tool_catalog_env.
    catalog_raw = env_map.get(UPSTREAM_MCP_TOOL_CATALOG_ENV)
    catalog_present = catalog_raw is not None
    if catalog_present:
        # load_upstream_tool_catalog is fail-closed: malformed JSON or
        # non-mapping payload raises McpServerError, which propagates up.
        tool_catalog = load_upstream_tool_catalog(catalog_raw)
        # Filter configured upstreams to those with at least one tool in
        # the catalog. A present-empty catalog ("{}" or {"remote":[]})
        # filters every server out, which is the documented
        # "I have an authoritative empty snapshot" case: the child serves
        # built-ins with upstream_reg=None below.
        upstream_servers = tuple(
            server for server in upstream_servers if server.name in tool_catalog
        )
    else:
        tool_catalog = {}
    # R2 respawn invariant: a child started with ``RALPH_MCP_RESPAWN=1``
    # must NOT probe upstreams. The parent owns the catalog and hands
    # it via ``UPSTREAM_MCP_TOOL_CATALOG_ENV``; a respawn that cannot
    # find that catalog would silently fall through to the network
    # probe above and burn the readiness budget before serving. The
    # contract: respawn without a catalog is a typed McpServerError
    # BEFORE any preflight and BEFORE any UpstreamRegistry.build() call,
    # so the supervisor never sees a half-bound child.
    is_respawn = env_map.get(MCP_RESPAWN_ENV) == "1"
    if is_respawn and upstream_servers and not catalog_present:
        _log_and_record_stage(workspace_root, "respawn_precheck")
        raise McpServerError(
            "MCP respawn missing tool catalog (RALPH_MCP_RESPAWN=1 set but no "
            "UPSTREAM_MCP_TOOL_CATALOG_ENV); refusing to probe upstreams during "
            f"respawn (servers={[s.name for s in upstream_servers]})",
            restart_count=0,
            last_stage="respawn_precheck",
        )

    if _extras.upstream_registry is not None:
        upstream_reg = _extras.upstream_registry
    elif upstream_servers and catalog_present:
        # Parent has an authoritative snapshot (even if empty). Use the
        # catalog-only path; ``build_from_tool_catalog`` returns an empty
        # registry when ``tool_catalog`` is empty, so a present-empty
        # snapshot correctly drops every server without ever reaching
        # ``UpstreamRegistry.build()`` (which would burn the readiness
        # budget on a network probe the parent already settled).
        upstream_reg = UpstreamRegistry.build_from_tool_catalog(upstream_servers, tool_catalog)
    elif upstream_servers:
        # RALPH_MCP_STRICT ... works.
        try:
            upstream_reg = UpstreamRegistry.build(
                upstream_servers,
                on_unreachable="raise" if strict_mode_from_env(env_map) else "warn_and_skip",
            )
            # First start owns the catalog: publish the freshly discovered
            # mapping so the parent (and any respawn) sees it on disk.
            publish_tool_catalog_to_parent(
                workspace_root,
                {
                    server.name: [
                        pt.tool
                        for pt in upstream_reg.tool_definitions()
                        if pt.server_name == server.name
                    ]
                    for server in upstream_servers
                },
            )
        except McpServerError:
            (workspace_root / ".agent" / "mcp" / "parent_catalog.json").unlink(missing_ok=True)
            raise
        except Exception as exc:
            # A failed first start must not leave a stale/partial published
            # catalog behind: the parent would serve tools the child could not
            # bind. Unlink before re-raising so respawn never reads a ghost.
            # UpstreamValidationError is normalized to the typed McpServerError
            # so the supervisor's restart budget sees a typed stage failure.
            (workspace_root / ".agent" / "mcp" / "parent_catalog.json").unlink(missing_ok=True)
            raise McpServerError(
                f"first-start upstream discovery failed: {exc}",
                restart_count=0,
                last_stage="upstream_catalog_or_discovery_done",
            ) from exc
    else:
        upstream_reg = None
    # R4-LOGS: upstream_catalog_or_discovery_done is ALWAYS logged and
    # recorded — even when there are zero upstreams, the stage marks
    # the boundary after which Ralph tools are registered. The n_servers
    # detail line is kept as an extra emit when upstreams exist.
    # ``ready_to_serve`` is recorded inside ``server.run()`` via the
    # ``on_ready`` callback so it lands when the socket is actually
    # accepting connections, not before.
    if upstream_reg is not None and upstream_servers:
        n_proxied = len(list(upstream_reg.tool_definitions()))
        n_servers = len(upstream_servers)
        logger.info(
            "ralph-mcp startup: stage={stage} n_servers={k} n_proxied_tools={m}",
            stage="upstream_catalog_or_discovery_done",
            k=n_servers,
            m=n_proxied,
        )
    _log_and_record_stage(workspace_root, "upstream_catalog_or_discovery_done")
    record_stage_elapsed(
        "upstream_catalog_or_discovery_done",
        _clock() - t0,
        budget_s=mcp_discovery_budget_from_env().total_seconds(),
    )
    _check_cumulative_startup_deadline("upstream_catalog_or_discovery_done", t0, env_map)
    registry_budget_s = mcp_registry_built_budget_from_env(env_map)

    # R4-LOGS: ``registry_built`` is recorded at the boundary, BEFORE
    # calling ``_build_mcp_registry_and_server``. This is essential:
    # the gate inside that function can fire BEFORE the
    # ``McpServer.__init__`` runs, so a real constructor block must
    # still leave ``registry_built`` in the stage file. The semantic
    # is "we are entering the registry_built phase"; the
    # post-construction ``record_stage_elapsed`` below is the
    # checkpoint that catches a slow (but not blocked) constructor.
    _log_and_record_stage(workspace_root, "registry_built")
    _check_cumulative_startup_deadline("registry_built", t0, env_map)

    server = _build_mcp_registry_and_server(
        effective_session=effective_session,
        workspace=effective_workspace,
        upstream_reg=upstream_reg,
        upstream_servers=upstream_servers,
        mcp_cfg=mcp_cfg,
        activity_relay_sender=activity_relay_sender,
        t0=t0,
        env_map=env_map,
    )
    record_stage_elapsed("registry_built", _clock() - t0, budget_s=registry_budget_s)
    _check_cumulative_startup_deadline("registry_built", t0, env_map)

    return _StandaloneHttpServer(host, port, server, parent_socket=parent_socket)


def _all_capability_values() -> set[str]:
    values = {cap.value for cap in Capability}
    values.update(cap.value for cap in McpCapability)
    return values


def _workspace_mcp_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp.toml"


def _mcp_toml_upstream_servers(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    return mcp_config_as_upstreams(mcp_config)


def load_runtime_upstream_servers(
    mcp_config: McpConfig,
    env: Mapping[str, str] | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Merge upstream MCP servers from the environment variable and mcp.toml."""
    env_map = os.environ if env is None else env
    raw_upstream = env_map.get(UPSTREAM_MCP_CONFIG_ENV)
    env_servers = load_upstream_mcp_servers(raw_upstream)
    return merge_mcp_toml_into_upstreams(env_servers, _mcp_toml_upstream_servers(mcp_config))


def run_standalone_server(
    workspace_root: Path,
    *,
    transport: str = DEFAULT_TRANSPORT,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    agent_transport: str | None = None,
    ready_event: Event | None = None,
    stop_event: Event | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Run the standalone Ralph MCP server over HTTP.

    ``transport`` is the MCP wire transport; ``agent_transport`` names
    the agent CLI being served. ``env`` is the composition-root
    environment map for the inherited listening FD and the respawn
    sentinel. When omitted, the process environment is that map.

    R4: every startup stage is logged as a flushed ``logger.info``
    line AND recorded to a stage-tracker file the supervisor reads
    on preflight failure. The six stage markers form a closed
    vocabulary; the audit at
    :mod:`ralph.testing.audit_resource_lifecycle` validates that the
    runtime emits each in the right order on the readiness path.
    ``PYTHONUNBUFFERED=1`` is set in the PARENT's ``_subprocess_env``
    so the child inherits unbuffered streams from the start.
    """
    if transport != DEFAULT_TRANSPORT:
        raise ValueError(f"Unsupported transport: {transport}")

    env_map = os.environ if env is None else env
    t0 = _clock()

    def _stage(name: str) -> None:
        _log_and_record_stage(workspace_root, name)

    _stage("env_loaded")
    # Synchronous block: fsync cannot be interrupted mid-call; deadline enforced at next boundary with correct stage attribution.
    _check_cumulative_startup_deadline("env_loaded", t0, env_map)

    # WU-B: switch the child's stdio to line-buffered so the listening
    # banner (``_fallback_standalone_server.py``) and every other
    # stdout line reaches the parent's pipe even when the parent did
    # NOT pre-set ``PYTHONUNBUFFERED`` (the W4 regression: setting
    # ``PYTHONUNBUFFERED`` inside the already-running child cannot
    # reconfigure its own stdio, and the parent sometimes forgets).
    # A closed/redirected stream raises ``ValueError`` from
    # ``reconfigure``; treat it as a no-op so embedded callers and
    # the test suite do not need a live tty.
    with contextlib.suppress(ValueError, AttributeError, OSError):
        # ``sys.stdout`` is typed ``TextIO | Any`` by mypy; ``reconfigure``
        # is a runtime method on the concrete text stream class, not on
        # the ``TextIO`` ABC, so the type stub does not declare it.
        # Use a ``getattr`` so the call is dynamic and a non-text
        # stream (e.g. a captured/redirected one) returns ``None``
        # instead of raising ``AttributeError`` (the suppress handles
        # the ValueError that ``reconfigure`` raises on a closed
        # stream).
        reconfigure_fn: object = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure_fn):
            reconfigure_fn(line_buffering=True)

    protect_broker_secret()
    sanitize_process_environment()
    relay_sender = ActivityRelaySender.from_environment(dict(os.environ))
    scrub_activity_relay_environment(os.environ)

    # R2: Session loading can perform blocking I/O (file read/stat, explore index SQLite open).
    # Isolate behind ThreadPoolExecutor with timeout <= remaining deadline so it cannot hang
    # indefinitely.
    whole_startup_budget_s = mcp_whole_startup_budget_from_env(env_map)
    remaining_s = max(0.0, whole_startup_budget_s - (_clock() - t0))
    session_timeout_s = min(_PRE_SERVE_BUDGET_S, remaining_s)

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(
            session_from_env,
            declared_agent_transport=agent_transport,
            workspace_root=workspace_root,
        )
        session = future.result(timeout=session_timeout_s)
    except concurrent.futures.TimeoutError:
        # FIFO / hung FS / wedged explore-index blocks the worker holding the
        # GIL; ``shutdown(wait=False)`` cannot interrupt it (see ``_pre_serve_overrun``).
        elapsed = _clock() - t0
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="session_ready",
            elapsed_s=elapsed,
            budget_s=session_timeout_s,
            exit_code=SESSION_OVERRUN_EXIT_CODE,
        )
    finally:
        if ex is not None:
            ex.shutdown(wait=False)

    erase_broker_secret_environment()
    # Synchronous block: fsync cannot be interrupted mid-call; deadline enforced at next boundary with correct stage attribution.
    _stage("session_ready")
    _check_cumulative_startup_deadline("session_ready", t0, env_map)

    # R1: when the parent passed a listening FD via
    # ``RALPH_MCP_INHERITED_FD``, adopt it. The bridge owns the
    # socket for the full lifetime; this child serves on it without
    # re-binding, so a client that connects during a respawn queues
    # in the kernel backlog instead of being refused.
    # Fail-closed invalid-inherited-FD: when MCP_INHERITED_FD_ENV is set
    # but the FD fails validation/adoption, child fails closed with typed
    # McpServerError (not log-and-fresh-bind). Absent env keeps the
    # fallback to fresh bind.
    parent_socket = _adopt_inherited_socket(env_map, t0, _stage, clock_fn=_clock)

    # R2: a respawn runs with ``MCP_RESPAWN_ENV=1`` set. The
    # upstream registry must come from the cached tool catalog the
    # parent hands us, never from a live probe — a respawn has no
    # network budget. First start keeps the existing path so
    # discovery still happens once.
    is_respawn = env_map.get(MCP_RESPAWN_ENV) == "1"
    server = build_standalone_http_server(
        workspace_root,
        host=host,
        port=port,
        extras=McpServerExtras(session=session),
        agent_transport=agent_transport,
        activity_relay_sender=relay_sender,
        env=env_map,
        parent_socket=parent_socket,
        t0=t0,
    )
    if is_respawn:
        logger.info(
            "ralph-mcp respawn on inherited FD (no network discovery, "
            "registry built from cached tool catalog)"
        )

    ready_budget_s = mcp_whole_startup_budget_from_env(env_map)

    # R4: record ready_to_serve and emit the banner INSIDE
    # ``server.run()`` so the banner fires when serving actually
    # starts, not before. The stage file is the authoritative
    # marker the supervisor reads on preflight failure.
    def _record_ready_to_serve() -> None:
        _check_cumulative_startup_deadline("ready_to_serve", t0, env_map)
        record_stage_elapsed("ready_to_serve", _clock() - t0, budget_s=ready_budget_s)
        _stage("ready_to_serve")

    if stop_event is not None:
        server.run(
            transport=DEFAULT_TRANSPORT,
            ready_event=ready_event,
            stop_event=stop_event,
            on_ready=_record_ready_to_serve,
        )
    else:
        server.run(
            transport=DEFAULT_TRANSPORT,
            ready_event=ready_event,
            on_ready=_record_ready_to_serve,
        )


def _agent_transport_choices() -> list[str]:
    """Return the agent-CLI values accepted by ``--agent-transport``."""
    values: list[str] = [transport.value for transport in AgentTransport]
    return sorted(values)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse standalone MCP server CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the standalone Ralph MCP HTTP server")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root exposed to Ralph MCP tools",
    )
    parser.add_argument(
        "--agent-transport",
        default=None,
        choices=_agent_transport_choices(),
        help=(
            "Agent CLI this server will serve. Some media-delivery decisions "
            "key on the CLI rather than the provider; declaring it lets the "
            "server withhold content that CLI cannot carry."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP bind port")
    return parser.parse_args(list(argv) if argv is not None else None)


def exit_when_owner_exits(
    env: MutableMapping[str, str],
    *,
    get_parent_pid: Callable[[], int] = os.getppid,
    sleep: Callable[[float], None] = time.sleep,
    exit_process: Callable[[int], None] = os._exit,
) -> threading.Thread | None:
    """Exit this server once the Ralph process that spawned it is gone.

    A server spawned by the lifecycle is stopped by its owner, so an owner
    killed without that cleanup (SIGKILL, a crashed test worker) used to
    leave the server running indefinitely. The owner names itself in
    ``RALPH_MCP_OWNER_PID``; while it is still our parent, a daemon thread
    watches for reparenting and exits the process. Standalone launches
    (``ralph-mcp`` without the variable) are left alone.

    Returns:
        The watcher thread, or ``None`` when there is no owner to watch.
    """
    raw_owner = env.pop(MCP_OWNER_PID_ENV, "")
    if not raw_owner.isdigit():
        return None
    owner = int(raw_owner)
    if owner != get_parent_pid():
        exit_process(0)
        return None

    def watch() -> None:
        while get_parent_pid() == owner:
            sleep(1.0)
        exit_process(0)

    watcher = threading.Thread(target=watch, name="mcp-owner-watch", daemon=True)
    watcher.start()
    return watcher


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for the standalone Ralph MCP HTTP server.

    The handler is the ``ralph-mcp`` console script entry point
    declared in ``pyproject.toml`` (``ralph-mcp =
    ralph.mcp.server.runtime:main``). It parses ``--workspace``,
    ``--host``, and ``--port`` via ``parse_args`` and delegates to
    ``run_standalone_server`` with the production
    ``DEFAULT_TRANSPORT`` (``streamable-http``). The environment
    handshake (``MCP_SESSION`` / ``MCP_SESSION_FILE``) is performed
    inside ``run_standalone_server``; this entry point does not touch
    the environment directly.

    Args:
        argv: Optional argv override. When ``None``,
            ``argparse`` reads from ``sys.argv[1:]``; tests pass a
            sequence of strings to avoid mutating process state.

    Returns:
        ``None``. The handler blocks until the server is shut down
        (Ctrl-C / SIGTERM). The process exit code is whatever the
        underlying HTTP server / signal handler produces.

    Side effects:
        Starts a long-lived HTTP server bound to ``--host:--port`` and
        a ``/health`` endpoint; the liveness probe is polled by
        ``ralph.process.mcp_supervisor``. Reads the MCP session
        environment variables.
    """
    sanitize_process_environment()
    exit_when_owner_exits(os.environ)
    args = parse_args(argv)
    run_standalone_server(
        cast(
            "Path", args.workspace
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        transport=DEFAULT_TRANSPORT,
        host=cast(
            "str", args.host
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        port=cast(
            "int", args.port
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        agent_transport=cast(
            "str | None", args.agent_transport
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MOUNT_PATH",
    "DEFAULT_PORT",
    "DEFAULT_TRANSPORT",
    "FallbackStandaloneServer",
    "FileBackedSession",
    "JsonRpcRequest",
    "McpServer",
    "McpServerExtras",
    "ServerState",
    "__version__",
    "build_standalone_http_server",
    "exit_when_owner_exits",
    "load_runtime_upstream_servers",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
    "standalone_session_identity",
]
