"""Interactive prompt helper — PM-style agent for refining PROMPT.md."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig

from rich.console import Console
from rich.text import Text

from ralph.agents.invoke import (
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    invoke_agent,
)
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.prompt_helper_prompt import build_prompt_helper_prompt
from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.product_spec import (
    read_product_spec_artifact,
    render_product_spec_as_prompt,
)
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.session import MCP_ENDPOINT_ENV, AgentSession
from ralph.mcp.server.lifecycle import McpServerExtras, start_mcp_server
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name
from ralph.workspace.fs import FsWorkspace

PROMPT_HELPER_TMP_DIR = Path(".agent/tmp")
PROMPT_HELPER_PROMPT_FILE = PROMPT_HELPER_TMP_DIR / "prompt_helper_prompt.md"


def _submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    """Return the submit artifact tool name for the given agent transport."""
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return claude_tool_name(SUBMIT_ARTIFACT_TOOL)
    return SUBMIT_ARTIFACT_TOOL


def run_prompt_helper(config: UnifiedConfig, workspace_root: Path) -> None:
    """Run the interactive prompt helper.

    Loads the prompt-helper-agent, starts an MCP bridge with read-only capabilities
    plus artifact submission, invokes the agent, and converts the resulting
    product_spec artifact into PROMPT.md.
    """
    console = Console()

    # 1. Load agent from registry
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(config.prompt_helper.agent)
    if agent_config is None and config.agents:
        first_agent_name = next(iter(config.agents))
        agent_config = registry.get(first_agent_name)
    if agent_config is None:
        raise RuntimeError(
            f"Prompt helper agent '{config.prompt_helper.agent}' is not configured "
            f"and no fallback agent is available in ralph-workflow.toml."
        )

    # 2. Determine submit_artifact_tool_name from transport
    submit_artifact_tool_name = _submit_artifact_tool_name_for_transport(agent_config.transport)

    # 3. Build prompt and write to workspace
    prompt_md_exists = (workspace_root / "PROMPT.md").exists()
    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        prompt_md_exists=prompt_md_exists,
    )
    prompt_file = workspace_root / PROMPT_HELPER_PROMPT_FILE
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_content, encoding="utf-8")

    # 4. Create standalone AgentSession with ARTIFACT_SUBMIT only (no plan-draft write capability)
    session = AgentSession(
        session_id="prompt-helper-agent",
        run_id=str(uuid4()),
        drain="standalone",
        capabilities={
            Capability.WORKSPACE_READ.value,
            Capability.WORKSPACE_METADATA_READ.value,
            Capability.GIT_STATUS_READ.value,
            Capability.GIT_DIFF_READ.value,
            Capability.ARTIFACT_SUBMIT.value,
        },
    )

    # 5. Start MCP bridge
    workspace = FsWorkspace(workspace_root)
    bridge = start_mcp_server(
        session,
        workspace,
        extras=McpServerExtras(extra_env={}),
    )

    try:
        # 6. Build invoke options with MCP_ENDPOINT_ENV
        options = build_invoke_options_from_config(
            config.general,
            InvokeRuntimeOptions(
                verbose=False,
                show_progress=False,
                workspace_path=workspace_root,
                extra_env={MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri()},
            ),
        )

        # 7. Invoke the agent and consume output
        for _line in invoke_agent(agent_config, str(prompt_file), options=options):
            pass
    finally:
        bridge.shutdown()

    # 8. After session ends, read product_spec artifact and render to PROMPT.md
    spec = read_product_spec_artifact(workspace_root)
    if spec is not None:
        prompt_md_content = render_product_spec_as_prompt(spec)
        prompt_md_path = workspace_root / "PROMPT.md"
        prompt_md_path.write_text(prompt_md_content, encoding="utf-8")
        console.print(
            Text("PROMPT.md written from product specification.", style="theme.status.success")
        )
    else:
        console.print(
            Text(
                "No product_spec artifact was submitted. PROMPT.md was not written.",
                style="theme.status.warning",
            )
        )


__all__ = ["run_prompt_helper"]
