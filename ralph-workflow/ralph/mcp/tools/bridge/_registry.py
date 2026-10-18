"""Tool registry builder for MCP bridge."""

from __future__ import annotations

import sys
import threading
import time
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.protocol._startup_timeouts import _REGISTRY_BUILD_BUDGET_S
from ralph.mcp.protocol.capability_mapping import McpCapability
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.tools.bridge._lazy_tool_handler import LazyToolHandler
from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from ralph.mcp.tools.bridge._specs_explore import explore_specs
from ralph.mcp.tools.bridge._specs_file_list import file_list_specs
from ralph.mcp.tools.bridge._specs_file_read import file_read_specs
from ralph.mcp.tools.bridge._specs_file_write import file_write_specs
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.bridge._specs_web_media import web_media_specs
from ralph.mcp.tools.bridge._tool_bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.bridge._upstream_proxy_handler import UpstreamProxyHandler
from ralph.visual.policy_facts import DESIGN_SYSTEM_POLICY_RELPATH, parse_policy_facts


def _warn_over_budget(
    stage: str, elapsed_s: float, budget_s: float
) -> None:
    """Log a diagnostic warning for a per-stage budget overrun (non-fatal)."""
    remaining_s = max(0.0, budget_s - elapsed_s)
    print(
        f"WARNING: startup stage {stage!r} over budget: "
        f"elapsed_s={elapsed_s:.3f}s budget_s={budget_s:.3f}s remaining_s={remaining_s:.3f}s",
        file=sys.stderr,
    )

if TYPE_CHECKING:
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec
    from ralph.mcp.upstream.registry import UpstreamRegistry


def tool_specs(mcp_config: McpConfig) -> tuple[ToolSpec, ...]:
    """Build the full ordered list of tool specifications for the MCP bridge."""
    specs: list[ToolSpec] = []
    specs.extend(file_read_specs())
    specs.extend(file_list_specs())
    specs.extend(file_write_specs())
    specs.extend(git_exec_specs())
    specs.extend(artifact_specs())
    specs.extend(web_media_specs(mcp_config))
    specs.extend(explore_specs())
    return tuple(specs)


_clock = time.monotonic


class _ReadableWorkspace(Protocol):
    """Minimal workspace contract needed for policy-gated tool exposure."""

    def read(self, path: str) -> str: ...


def _read_workspace_policy(
    workspace: object,
    *,
    timeout_s: float,
    t0: float | None = None,
) -> str:
    """Read design system policy with a strict, fail-closed timeout bound."""
    if not hasattr(workspace, "read"):
        raise AttributeError("Workspace object has no 'read' method")
    readable = cast("_ReadableWorkspace", workspace)

    result: list[str] = []
    error: list[BaseException] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            result.append(readable.read(DESIGN_SYSTEM_POLICY_RELPATH))
        except BaseException as exc:
            error.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True, name="ralph-policy-reader")
    thread.start()
    if not done.wait(timeout=timeout_s):
        elapsed = (_clock() - t0) if t0 is not None else timeout_s
        raise McpServerError(
            f"workspace policy read exceeded budget ({elapsed:.3f}s >= {timeout_s:.3f}s)",
            restart_count=0,
            last_stage="registry_built",
            elapsed_s=elapsed,
        )
    if error:
        raise error[0]
    return result[0]


def _capture_policy_is_resolved(
    workspace: object,
    *,
    timeout_s: float | None = None,
    t0: float | None = None,
) -> bool:
    """Return whether this workspace declares a complete capture matrix."""
    effective_timeout_s = timeout_s if timeout_s is not None else _REGISTRY_BUILD_BUDGET_S
    try:
        policy_markdown = _read_workspace_policy(
            workspace,
            timeout_s=effective_timeout_s,
            t0=t0,
        )
        parse_policy_facts(policy_markdown, target="_capture_policy_probe")
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _attach_upstream_registry(bridge: ToolBridge, upstream_registry: UpstreamRegistry) -> None:
    for proxied_tool in upstream_registry.tool_definitions():
        metadata = ToolMetadata(
            definition=ToolDefinition(
                name=proxied_tool.alias,
                description=proxied_tool.tool.description,
                input_schema=proxied_tool.tool.input_schema,
            ),
            required_capability=McpCapability.UPSTREAM_TOOL_USE.value,
        )
        handler = UpstreamProxyHandler(
            alias=proxied_tool.alias,
            upstream_registry=upstream_registry,
        )
        bridge.register(metadata, handler)


def build_ralph_tool_registry(
    session: object,
    workspace: object,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    mcp_config: McpConfig | None = None,
    t0: float | None = None,
    budget_s: float | None = None,
    timeout_s: float | None = None,
) -> ToolBridge:
    """Build the default Ralph MCP tool registry."""
    mcp_config_cls = cast(
        "type[McpConfig]", import_module("ralph.config.mcp_models").McpConfig
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    mcp_cfg = mcp_config or mcp_config_cls()
    bridge = ToolBridge(session=session)
    capture_policy_is_resolved = _capture_policy_is_resolved(
        workspace,
        timeout_s=timeout_s,
        t0=t0,
    )
    if t0 is not None and budget_s is not None:
        elapsed = _clock() - t0
        if elapsed > budget_s:
            _warn_over_budget("registry_built", elapsed, budget_s)
    for spec in tool_specs(mcp_cfg):
        is_websearch = (
            spec.module_name == "ralph.mcp.tools.websearch"
            and spec.handler_name == "handle_web_search"
        )
        is_webvisit = (
            spec.module_name == "ralph.mcp.tools.webvisit"
            and spec.handler_name == "handle_visit_url"
        )
        is_webdownload = (
            spec.module_name == "ralph.mcp.tools.webvisit"
            and spec.handler_name == "handle_download_url"
        )
        is_read_image = (
            spec.module_name == "ralph.mcp.tools.workspace"
            and spec.handler_name == "handle_read_image"
        )
        is_read_media = (
            spec.module_name == "ralph.mcp.tools.workspace"
            and spec.handler_name == "handle_read_media"
        )
        is_media_capture = (
            spec.module_name == "ralph.mcp.tools.workspace"
            and spec.handler_name == "handle_media_capture_tool"
        )
        if is_media_capture and not capture_policy_is_resolved:
            continue
        if is_websearch:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"web_search_config": mcp_cfg.web_search},
                ),
            )
        elif is_webvisit or is_webdownload:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"web_visit_config": mcp_cfg.web_visit},
                ),
            )
        elif is_read_image or is_read_media:
            bridge.register(
                spec.metadata,
                LazyToolHandler(
                    module_name=spec.module_name,
                    handler_name=spec.handler_name,
                    session=session,
                    workspace=workspace,
                    extra_kwargs={"max_inline_bytes": mcp_cfg.media.max_inline_bytes},
                ),
            )
        else:
            bridge.register_spec(spec, session=session, workspace=workspace)
    if upstream_registry is not None:
        _attach_upstream_registry(bridge, upstream_registry)
    return bridge
