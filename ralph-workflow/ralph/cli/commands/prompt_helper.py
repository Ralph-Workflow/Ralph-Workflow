"""Interactive prompt helper — PM-style agent for refining PROMPT.md."""

from __future__ import annotations

from dataclasses import replace
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
    OpenCodeResumableExitError,
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


def _prompt_for_user_input(prompt_text: str) -> str:
    """Prompt the user for conversational feedback between agent turns."""
    return Prompt.ask(prompt_text, console=Console()).strip()


def _reinvoke_agent(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
    current_spec: dict[str, object] | None,
    user_input: str,
    session_id: str | None,
) -> tuple[dict[str, object] | None, str | None]:
    """Re-invoke the agent with user feedback, continuing the same session when possible."""
    prompt_file = _write_follow_up_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        prompt_md_exists,
        current_spec,
        user_input,
        session_id=session_id,
    )
    resumable_session_id = _run_single_invoke(
        agent_config,
        prompt_file,
        _options_for_session(options, session_id),
    )
    next_session_id = resumable_session_id or session_id
    return read_product_spec_artifact(workspace_root), next_session_id


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


def _display_agent_line(line: str) -> None:
    """Render a single agent output line for the user."""
    visible = line.strip()
    if not visible:
        return
    Console().print(visible)


def _run_single_invoke(
    agent_config: AgentConfig,
    prompt_file: Path,
    options: InvokeOptions,
) -> str | None:
    """Run a single agent turn and preserve resumable sessions for host-owned loops."""
    try:
        for line in invoke_agent(agent_config, str(prompt_file), options=options):
            _display_agent_line(line)
    except OpenCodeResumableExitError as exc:
        return exc.resumable_session_id
    return None


def _write_prompt_file(workspace_root: Path, prompt_content: str) -> Path:
    """Write prompt helper content to the canonical temp file."""
    prompt_file = workspace_root / PROMPT_HELPER_PROMPT_FILE
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_content, encoding="utf-8")
    return prompt_file


def _update_prompt_file(
    workspace_root: Path,
    submit_artifact_tool_name: str,
    prompt_md_exists: bool,
    spec: dict[str, object] | None,
) -> Path:
    """Build and write the initial helper instructions, returning the path."""
    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        prompt_md_exists=prompt_md_exists,
        has_draft=spec is not None,
        current_draft=spec,
    )
    return _write_prompt_file(workspace_root, prompt_content)


def _write_follow_up_prompt_file(
    workspace_root: Path,
    submit_artifact_tool_name: str,
    prompt_md_exists: bool,
    current_spec: dict[str, object] | None,
    user_input: str,
    *,
    session_id: str | None,
) -> Path:
    """Write the next user message, falling back to a self-contained prompt when needed."""
    if session_id is not None:
        return _write_prompt_file(workspace_root, user_input)

    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        prompt_md_exists=prompt_md_exists,
        has_draft=current_spec is not None,
        current_draft=current_spec,
    )
    prompt_content += f"\n\nThe user said:\n{user_input}\n"
    return _write_prompt_file(workspace_root, prompt_content)


def _options_for_session(options: InvokeOptions, session_id: str | None) -> InvokeOptions:
    """Return invoke options that continue a resumable agent session when available."""
    if session_id is None:
        return options
    return replace(options, session_id=session_id, initial_session_id=session_id)


def _run_conversational_intake(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Run the intake loop until an artifact exists or the agent can no longer resume."""
    prompt_file = _update_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        prompt_md_exists,
        None,
    )
    session_id = _run_single_invoke(agent_config, prompt_file, options)
    spec = read_product_spec_artifact(workspace_root)

    while spec is None and session_id is not None:
        user_input = _prompt_for_user_input("Your response")
        spec, session_id = _reinvoke_agent(
            workspace_root,
            agent_config,
            options,
            prompt_md_exists,
            submit_artifact_tool_name,
            None,
            user_input,
            session_id,
        )

    return spec, session_id


def _handle_artifact_exists(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
    spec: dict[str, object],
    session_id: str | None,
) -> None:
    """Handle the case where an artifact exists - present review choices to user."""
    console = Console()
    action = _prompt_review_choice()

    if action == ReviewAction.FINISH:
        _write_prompt_md(workspace_root, spec)
        return
    if action == ReviewAction.START_OVER:
        console.print(Text("Starting over with a fresh specification."))
        _clear_draft_artifact(workspace_root)
        new_spec, new_session_id = _run_conversational_intake(
            workspace_root,
            agent_config,
            options,
            prompt_md_exists,
            submit_artifact_tool_name,
        )
        if new_spec is not None:
            _handle_artifact_exists(
                workspace_root,
                agent_config,
                options,
                prompt_md_exists,
                submit_artifact_tool_name,
                new_spec,
                new_session_id,
            )
        return

    if action == ReviewAction.UPDATE_SECTION:
        user_input = _prompt_for_user_input(
            "Which section should be updated, and what should change?"
        )
    else:
        user_input = _prompt_for_user_input("What would you like to change or add?")

    _continue_review_loop(
        workspace_root,
        agent_config,
        options,
        prompt_md_exists,
        submit_artifact_tool_name,
        spec,
        user_input,
        session_id,
    )


def _continue_review_loop(
    workspace_root: Path,
    agent_config: AgentConfig,
    options: InvokeOptions,
    prompt_md_exists: bool,
    submit_artifact_tool_name: str,
    current_spec: dict[str, object],
    user_input: str,
    session_id: str | None,
) -> None:
    """Continue the review loop by sending user feedback back to the agent."""
    spec, next_session_id = _reinvoke_agent(
        workspace_root,
        agent_config,
        options,
        prompt_md_exists,
        submit_artifact_tool_name,
        current_spec,
        user_input,
        session_id,
    )
    if spec is not None:
        _handle_artifact_exists(
            workspace_root,
            agent_config,
            options,
            prompt_md_exists,
            submit_artifact_tool_name,
            spec,
            next_session_id,
        )


def run_prompt_helper(config: UnifiedConfig, workspace_root: Path) -> None:
    """Run the interactive prompt helper.

    This is a host-owned state machine that:
    1. Starts a conversational intake turn with the agent
    2. Prompts the user for freeform replies between resumable turns
    3. After artifact submission, presents review choices to the user
    4. Only writes PROMPT.md when the user explicitly chooses Finish
    """
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

    submit_artifact_tool_name = _submit_artifact_tool_name_for_transport(agent_config.transport)

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

    workspace = FsWorkspace(workspace_root)
    bridge = start_mcp_server(
        session,
        workspace,
        extras=McpServerExtras(extra_env={}),
    )

    try:
        options = build_invoke_options_from_config(
            config.general,
            InvokeRuntimeOptions(
                verbose=False,
                show_progress=False,
                workspace_path=workspace_root,
                extra_env={MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri()},
            ),
        )

        prompt_md_exists = (workspace_root / "PROMPT.md").exists()
        spec, session_id = _run_conversational_intake(
            workspace_root,
            agent_config,
            options,
            prompt_md_exists,
            submit_artifact_tool_name,
        )
        if spec is not None:
            _handle_artifact_exists(
                workspace_root,
                agent_config,
                options,
                prompt_md_exists,
                submit_artifact_tool_name,
                spec,
                session_id,
            )
    finally:
        bridge.shutdown()


__all__ = ["run_prompt_helper"]
