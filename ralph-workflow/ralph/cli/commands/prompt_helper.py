"""Interactive prompt helper — PM-style agent for refining PROMPT.md."""

from __future__ import annotations

from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Protocol

    from ralph.agents.idle_watchdog import WaitingStatusEvent
    from ralph.config.models import UnifiedConfig
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.session_runtime import ManagedAgentSessionRequest, ManagedAgentSessionRuntime

    class _ManagedSessionRuntime(Protocol):
        def invoke_prompt_file(
            self,
            prompt_file: str | Path,
            *,
            session_id: str | None = None,
            session_id_sink: Callable[[str], None] | None = None,
            required_artifact: RequiredArtifact | None = None,
            waiting_listener: Callable[[WaitingStatusEvent], None] | None = None,
            permission_prompt_listener: Callable[[str], None] | None = None,
            extra_env: dict[str, str] | None = None,
        ) -> Iterable[str]: ...

else:
    _ManagedSessionRuntime = object

from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.prompt_helper_prompt import build_prompt_helper_prompt
from ralph.mcp.artifacts.product_spec import (
    read_product_spec_artifact,
    render_product_spec_as_prompt,
)
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.fs import FsWorkspace

_session_runtime_module = import_module("ralph.session_runtime")
ManagedAgentSessionRequestCtor = cast(
    "type[ManagedAgentSessionRequest]",
    _session_runtime_module.ManagedAgentSessionRequest,
)
ManagedAgentSessionRuntimeType = cast(
    "type[ManagedAgentSessionRuntime]",
    _session_runtime_module.ManagedAgentSessionRuntime,
)

PROMPT_HELPER_TMP_DIR = Path(".agent/tmp")
PROMPT_HELPER_PROMPT_FILE = PROMPT_HELPER_TMP_DIR / "prompt_helper_prompt.md"


class _PromptHelperAction(StrEnum):
    """Shared action enum for prompt-helper review and existing-prompt choices."""

    CONTINUE = "continue"
    UPDATE_SECTION = "update"
    START_OVER = "start_over"
    FINISH = "finish"
    REPLACE = "replace"
    REFINE = "refine"


ReviewAction = _PromptHelperAction
ExistingPromptAction = _PromptHelperAction


_REVIEW_CONTINUE = ReviewAction.CONTINUE
_REVIEW_UPDATE_SECTION = ReviewAction.UPDATE_SECTION
_REVIEW_START_OVER = ReviewAction.START_OVER
_REVIEW_FINISH = ReviewAction.FINISH
_EXISTING_PROMPT_REPLACE = ExistingPromptAction.REPLACE
_EXISTING_PROMPT_REFINE = ExistingPromptAction.REFINE

# Map review choice strings to ReviewAction values
_REVIEW_ACTION_MAP: dict[str, ReviewAction] = {
    "Continue refining": _REVIEW_CONTINUE,
    "Update a section": _REVIEW_UPDATE_SECTION,
    "Start over": _REVIEW_START_OVER,
    "Finish": _REVIEW_FINISH,
}

_EXISTING_PROMPT_ACTION_MAP: dict[str, ExistingPromptAction] = {
    "Replace it": _EXISTING_PROMPT_REPLACE,
    "Refine it": _EXISTING_PROMPT_REFINE,
}


def _build_review_prompt() -> list[str]:
    """Build the review choice options for the console prompt."""
    return ["Continue refining", "Update a section", "Start over", "Finish"]


def _build_existing_prompt_prompt() -> list[str]:
    """Build the existing-PROMPT choice options for the console prompt."""
    return ["Replace it", "Refine it"]


def _prompt_existing_prompt_choice() -> ExistingPromptAction:
    """Prompt the user before the first agent turn when PROMPT.md already exists."""
    console = Console()
    choices = _build_existing_prompt_prompt()
    choice = Prompt.ask(
        "I found an existing PROMPT.md in this workspace. What should prompt-helper do?",
        console=console,
        choices=choices,
        default=choices[1],
    )
    return _EXISTING_PROMPT_ACTION_MAP.get(choice, _EXISTING_PROMPT_REFINE)


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
    return _REVIEW_ACTION_MAP.get(choice, _REVIEW_FINISH)


def _prompt_for_user_input(prompt_text: str) -> str:
    """Prompt the user for conversational feedback between agent turns."""
    return Prompt.ask(prompt_text, console=Console()).strip()


def _reinvoke_agent(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    submit_artifact_tool_name: str,
    existing_prompt_context: str | None,
    current_spec: dict[str, object] | None,
    user_input: str,
    session_id: str | None,
) -> tuple[dict[str, object] | None, str | None]:
    """Re-invoke the agent with user feedback, continuing the same session when possible."""
    prompt_file = _write_follow_up_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        existing_prompt_context,
        current_spec,
        user_input,
        session_id=session_id,
    )
    resumable_session_id = _run_single_invoke(runtime, prompt_file, session_id=session_id)
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
    runtime: _ManagedSessionRuntime,
    prompt_file: Path,
    *,
    session_id: str | None = None,
) -> str | None:
    """Run a single agent turn and preserve resumable sessions for host-owned loops."""
    observed_session_id = session_id

    def _capture_session_id(captured_session_id: str) -> None:
        nonlocal observed_session_id
        observed_session_id = captured_session_id

    try:
        for line in runtime.invoke_prompt_file(
            prompt_file,
            session_id=session_id,
            session_id_sink=_capture_session_id,
        ):
            _display_agent_line(line)
    except OpenCodeResumableExitError as exc:
        return exc.resumable_session_id or observed_session_id
    return observed_session_id


def _write_prompt_file(workspace_root: Path, prompt_content: str) -> Path:
    """Write prompt helper content to the canonical temp file."""
    prompt_file = workspace_root / PROMPT_HELPER_PROMPT_FILE
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_content, encoding="utf-8")
    return prompt_file


def _update_prompt_file(
    workspace_root: Path,
    submit_artifact_tool_name: str,
    existing_prompt_context: str | None,
    spec: dict[str, object] | None,
) -> Path:
    """Build and write the initial helper instructions, returning the path."""
    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        existing_prompt_context=existing_prompt_context,
        has_draft=spec is not None,
        current_draft=spec,
    )
    return _write_prompt_file(workspace_root, prompt_content)


def _write_follow_up_prompt_file(
    workspace_root: Path,
    submit_artifact_tool_name: str,
    existing_prompt_context: str | None,
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
        existing_prompt_context=existing_prompt_context,
        has_draft=current_spec is not None,
        current_draft=current_spec,
    )
    prompt_content += f"\n\nThe user said:\n{user_input}\n"
    return _write_prompt_file(workspace_root, prompt_content)


def _run_conversational_intake(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    existing_prompt_context: str | None,
    submit_artifact_tool_name: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Run the intake loop until an artifact exists or the agent can no longer resume."""
    prompt_file = _update_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        existing_prompt_context,
        None,
    )
    session_id = _run_single_invoke(runtime, prompt_file)
    spec = read_product_spec_artifact(workspace_root)

    while spec is None and session_id is not None:
        user_input = _prompt_for_user_input("Your response")
        spec, session_id = _reinvoke_agent(
            workspace_root,
            runtime,
            submit_artifact_tool_name,
            existing_prompt_context,
            None,
            user_input,
            session_id,
        )

    return spec, session_id


def _handle_artifact_exists(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    existing_prompt_context: str | None,
    submit_artifact_tool_name: str,
    spec: dict[str, object],
    session_id: str | None,
) -> None:
    """Handle the case where an artifact exists - present review choices to user."""
    console = Console()
    action = _prompt_review_choice()

    if action == _REVIEW_FINISH:
        _write_prompt_md(workspace_root, spec)
        return
    if action == _REVIEW_START_OVER:
        console.print(Text("Starting over with a fresh specification."))
        _clear_draft_artifact(workspace_root)
        new_spec, new_session_id = _run_conversational_intake(
            workspace_root,
            runtime,
            existing_prompt_context,
            submit_artifact_tool_name,
        )
        if new_spec is not None:
            _handle_artifact_exists(
                workspace_root,
                runtime,
                existing_prompt_context,
                submit_artifact_tool_name,
                new_spec,
                new_session_id,
            )
        return

    if action == _REVIEW_UPDATE_SECTION:
        user_input = _prompt_for_user_input(
            "Which section should be updated, and what should change?"
        )
    else:
        user_input = _prompt_for_user_input("What would you like to change or add?")

    _continue_review_loop(
        workspace_root,
        runtime,
        existing_prompt_context,
        submit_artifact_tool_name,
        spec,
        user_input,
        session_id,
    )


def _continue_review_loop(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    existing_prompt_context: str | None,
    submit_artifact_tool_name: str,
    current_spec: dict[str, object],
    user_input: str,
    session_id: str | None,
) -> None:
    """Continue the review loop by sending user feedback back to the agent."""
    spec, next_session_id = _reinvoke_agent(
        workspace_root,
        runtime,
        submit_artifact_tool_name,
        existing_prompt_context,
        current_spec,
        user_input,
        session_id,
    )
    if spec is not None:
        _handle_artifact_exists(
            workspace_root,
            runtime,
            existing_prompt_context,
            submit_artifact_tool_name,
            spec,
            next_session_id,
        )


def _prompt_helper_session_request() -> ManagedAgentSessionRequest:
    """Return the managed-session request used by prompt-helper."""
    return ManagedAgentSessionRequestCtor(
        session_id_prefix="prompt-helper",
        drain="standalone",
        capabilities=frozenset(
            {
                Capability.WORKSPACE_READ.value,
                Capability.WORKSPACE_METADATA_READ.value,
                Capability.GIT_STATUS_READ.value,
                Capability.GIT_DIFF_READ.value,
                Capability.ARTIFACT_SUBMIT.value,
            }
        ),
    )


def _existing_prompt_context_for_intake(workspace_root: Path) -> str | None:
    """Return existing PROMPT.md content when the user chooses to refine it."""
    prompt_md_path = workspace_root / "PROMPT.md"
    if not prompt_md_path.exists():
        return None
    action = _prompt_existing_prompt_choice()
    if action == _EXISTING_PROMPT_REPLACE:
        _clear_draft_artifact(workspace_root)
        return None
    return FsWorkspace(workspace_root).read("PROMPT.md")


def run_prompt_helper(config: UnifiedConfig, workspace_root: Path) -> None:
    """Run the interactive prompt helper.

    This is a host-owned state machine that:
    1. Resolves existing PROMPT.md choices before the first agent turn
    2. Starts a conversational intake turn with the agent
    3. Prompts the user for freeform replies between resumable turns
    4. After artifact submission, presents review choices to the user
    5. Only writes PROMPT.md when the user explicitly chooses Finish
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

    submit_artifact_tool_name = submit_artifact_tool_name_for_transport(agent_config.transport)
    existing_prompt_context = _existing_prompt_context_for_intake(workspace_root)

    with ManagedAgentSessionRuntimeType.open(
        config=config,
        workspace_root=workspace_root,
        agent_config=agent_config,
        request=_prompt_helper_session_request(),
    ) as runtime:
        spec, session_id = _run_conversational_intake(
            workspace_root,
            runtime,
            existing_prompt_context,
            submit_artifact_tool_name,
        )
        if spec is not None:
            _handle_artifact_exists(
                workspace_root,
                runtime,
                existing_prompt_context,
                submit_artifact_tool_name,
                spec,
                session_id,
            )


__all__ = ["run_prompt_helper"]
