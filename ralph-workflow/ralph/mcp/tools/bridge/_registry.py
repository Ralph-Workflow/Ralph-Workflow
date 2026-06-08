"""Tool registry builder for MCP bridge."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.capability_mapping import McpCapability
from ralph.mcp.tools.bridge._lazy_tool_handler import LazyToolHandler
from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from ralph.mcp.tools.bridge._specs_file_list import file_list_specs
from ralph.mcp.tools.bridge._specs_file_read import file_read_specs
from ralph.mcp.tools.bridge._specs_file_write import file_write_specs
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.bridge._specs_web_media import web_media_specs
from ralph.mcp.tools.bridge._tool_bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.bridge._upstream_proxy_handler import UpstreamProxyHandler

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
    return tuple(specs)


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
) -> ToolBridge:
    """Build the default Ralph MCP tool registry."""
    mcp_config_cls = cast("type[McpConfig]", import_module("ralph.config.mcp_models").McpConfig)
    mcp_cfg = mcp_config or mcp_config_cls()
    bridge = ToolBridge(session=session)
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
