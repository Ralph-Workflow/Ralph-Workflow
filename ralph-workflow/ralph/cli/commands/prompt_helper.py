"""Interactive prompt helper — PM-style agent for refining PROMPT.md."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig

from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from ralph.agents.invoke import (
    InvokeOptions,
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


class ReviewAction(Enum):
    """User choices after an artifact is submitted."""

    CONTINUE = "continue"
    UPDATE_SECTION = "update"
    START_OVER = "start_over"
    FINISH = "finish"


# Map review choice strings to ReviewAction values
_REVIEW_ACTION_MAP: dict[str, ReviewAction] = {
    "Continue refining": ReviewAction.CONTINUE,
    "Update a section": ReviewAction.UPDATE_SECTION,
    "Start over": ReviewAction.START_OVER,
    "Finish": ReviewAction.FINISH,
}


def _submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    """Return the submit artifact tool name for the given agent transport."""
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return claude_tool_name(SUBMIT_ARTIFACT_TOOL)
    return SUBMIT_ARTIFACT_TOOL


def _build_review_prompt() -> list[str]:
    """Build the review choice options for the console prompt."""
    return ["Continue refining", "Update a section", "Start over", "Finish"]


def _prompt_review_choice() -> ReviewAction:
    """Prompt user for review action and return the chosen action."""
    console = Console()
    choices = _build_review_prompt()
    choice = Prompt.ask(
        "What would you like to do? ",
        console=console,
        choices=choices,
        default=choices[0],
    )
    return _REVIEW_ACTION_MAP.get(choice, ReviewAction.FINISH)


def _write_prompt_md(workspace_root: Path, spec: dict[str, object]) -> None:
    """Write PROMPT.md from the given product spec."""
    console = Console()
    prompt_md_content = render_product_spec_as_prompt(spec)
    prompt_md_path = workspace_root / "PROMPT.md"
    prompt_md_path.write_text(prompt_md_content, encoding="utf-8")
    console.print(
        Text("PROMPT.md written from product specification.", style="theme.status.success")
    )


def _clear_draft_artifact(workspace_root: Path) -> None:
    """Delete the draft artifact file if it exists."""
    artifact_file = workspace_root / ".agent" / "artifacts" / "product_spec.json"
    if artifact_file.exists():
        artifact_file.unlink()


def _run_single_invoke(
    agent_config: AgentConfig,
    prompt_file: Path,
    options: InvokeOptions,
) -> None:
    """Run a single agent invocation, consuming all output."""
    for _line in invoke_agent(agent_config, str(prompt_file), options=options):
        pass


def _update_prompt_file(
    workspace_root: Path,
    submit_artifact_tool_name: str,
    prompt_md_exists: bool,
    spec: dict[str, object] | None,
) -> Path:
    """Build and write the prompt file, returning the path."""
    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        prompt_md_exists=prompt_md_exists,
        has_draft=spec is not None,
        current_draft=spec,
    )
    prompt_file = workspace_root / PROMPT_HELPER_PROMPT_FILE
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_content, encoding="utf-8")
    return prompt_file


def _handle_artifact_exists(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
    spec: dict[str, object],
) -> None:
    """Handle the case where an artifact exists - present review choices to user."""
    console = Console()
    choices = _build_review_prompt()
    choice = Prompt.ask(
        "What would you like to do? ",
        console=console,
        choices=choices,
        default=choices[0],
    )
    action = _REVIEW_ACTION_MAP.get(choice, ReviewAction.FINISH)

    if action == ReviewAction.FINISH:
        _write_prompt_md(workspace_root, spec)
        return
    elif action == ReviewAction.START_OVER:
        console.print(Text("Starting over with a fresh specification."))
        _clear_draft_artifact(workspace_root)
        return
    elif action == ReviewAction.UPDATE_SECTION:
        console.print(Text("Tell me which section to update and what changes you'd like."))
    # else: Continue refining

    # Continue refining or update section — loop back with current draft
    _continue_review_loop(
        workspace_root,
        agent_config,
        options,
        prompt_md_exists,
        submit_artifact_tool_name,
        spec,
    )


def _continue_review_loop(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
    current_spec: dict[str, object],
) -> None:
    """Continue the review loop by invoking the agent again."""
    prompt_file = _update_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        prompt_md_exists,
        current_spec,
    )
    _run_single_invoke(agent_config, prompt_file, options)
    spec = read_product_spec_artifact(workspace_root)
    if spec is not None:
        _handle_artifact_exists(
            workspace_root,
            agent_config,
            options,
            prompt_md_exists,
            submit_artifact_tool_name,
            spec,
        )


def run_prompt_helper(config: UnifiedConfig, workspace_root: Path) -> None:
    """Run the interactive prompt helper.

    This is a state machine that:
    1. Invokes the agent to interact with the user and produce a product_spec artifact
    2. After artifact submission, presents review choices to the user
    3. Only writes PROMPT.md when the user explicitly chooses Finish

    The agent session continues across multiple invocations when the user chooses
    to continue refining, with state persisted through the artifact file.
    """
    # 1. Load agent from registry
    registry = AgentRegistry.from_config(config)

    # Resolve agent: explicit setting > first configured > built-in opencode
    named_agent = config.prompt_helper.agent
    if named_agent is not None:
        agent_config = registry.get(named_agent)
    elif config.agents:
        first_agent_name = next(iter(config.agents))
        agent_config = registry.get(first_agent_name)
    else:
        agent_config = registry.get("opencode")

    if agent_config is None:
        msg = (
            f"Prompt helper agent '{named_agent or 'opencode'}' is not available "
            f"and no fallback agent is available in ralph-workflow.toml."
        )
        raise RuntimeError(msg)

    # 2. Determine submit_artifact_tool_name from transport
    submit_artifact_tool_name = _submit_artifact_tool_name_for_transport(agent_config.transport)

    # 3. Create standalone AgentSession (capabilities set excludes declare_complete)
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

    # 4. Start MCP bridge (stays alive across multiple invocations)
    workspace = FsWorkspace(workspace_root)
    bridge = start_mcp_server(
        session,
        workspace,
        extras=McpServerExtras(extra_env={}),
    )

    try:
        # 5. Build invoke options with MCP_ENDPOINT_ENV
        options = build_invoke_options_from_config(
            config.general,
            InvokeRuntimeOptions(
                verbose=False,
                show_progress=False,
                workspace_path=workspace_root,
                extra_env={MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri()},
            ),
        )

        # 6. Initial prompt_md_exists check
        prompt_md_exists = (workspace_root / "PROMPT.md").exists()

        # 7. Initial invocation
        prompt_file = _update_prompt_file(
            workspace_root,
            submit_artifact_tool_name,
            prompt_md_exists,
            None,
        )
        _run_single_invoke(agent_config, prompt_file, options)

        # 8. Check if artifact was submitted - if so, enter review loop
        spec = read_product_spec_artifact(workspace_root)
        if spec is not None:
            _handle_artifact_exists(
                workspace_root,
                agent_config,
                options,
                prompt_md_exists,
                submit_artifact_tool_name,
                spec,
            )
        # If no artifact, session ends silently (agent may still be gathering requirements)

    finally:
        bridge.shutdown()


__all__ = ["run_prompt_helper"]
