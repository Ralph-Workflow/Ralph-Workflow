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

from rich.prompt import Prompt
from rich.text import Text

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.prompt_helper_prompt import build_prompt_helper_prompt
from ralph.display.context import DisplayContext, make_display_context
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
    """Post-artifact review choices owned by the host orchestrator."""

    REFINE = "refine"
    ACCEPT = "accept"


ReviewAction = _PromptHelperAction

_REVIEW_REFINE = ReviewAction.REFINE
_REVIEW_ACCEPT = ReviewAction.ACCEPT

_REVIEW_ACTION_MAP: dict[str, ReviewAction] = {
    "Refine": _REVIEW_REFINE,
    "Accept": _REVIEW_ACCEPT,
}


def _build_review_prompt() -> list[str]:
    """Build the review choice options for the console prompt."""
    return ["Refine", "Accept"]


def _prompt_review_choice(display_context: DisplayContext | None = None) -> ReviewAction:
    """Prompt user for the post-artifact review action and return the choice.

    Added ``display_context`` parameter to remove inline Console construction per
    wt-007-consolidate-display; pass an explicit context to avoid env re-read
    between caller and default resolve.
    """
    ctx = display_context or make_display_context()
    choices = _build_review_prompt()
    choice = Prompt.ask(
        "Refine this specification further, or accept it?",
        console=ctx.console,
        choices=choices,
        default=_REVIEW_ACCEPT.name.capitalize(),
    )
    return _REVIEW_ACTION_MAP.get(choice, _REVIEW_ACCEPT)


def _prompt_for_user_input(
    prompt_text: str,
    *,
    display_context: DisplayContext | None = None,
) -> str:
    """Prompt the user for conversational feedback between agent turns.

    Added ``display_context`` parameter to remove inline Console construction per
    wt-007-consolidate-display; pass an explicit context to avoid env re-read
    between caller and default resolve.
    """
    ctx = display_context or make_display_context()
    return Prompt.ask(prompt_text, console=ctx.console).strip()


def _reinvoke_agent(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    submit_artifact_tool_name: str,
    existing_prompt_context: str | None,
    current_spec: dict[str, object] | None,
    user_input: str,
    session_id: str | None,
    *,
    display_context: DisplayContext | None = None,
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
    resumable_session_id = _run_single_invoke(
        runtime, prompt_file, session_id=session_id, display_context=display_context
    )
    next_session_id = resumable_session_id or session_id
    return read_product_spec_artifact(workspace_root), next_session_id


def _write_prompt_md(
    workspace_root: Path,
    spec: dict[str, object],
    *,
    display_context: DisplayContext | None = None,
) -> None:
    """Write PROMPT.md from the given product spec.

    Added ``display_context`` parameter to remove inline Console construction per
    wt-007-consolidate-display; pass an explicit context to avoid env re-read
    between caller and default resolve.
    """
    ctx = display_context or make_display_context()
    prompt_md_content = render_product_spec_as_prompt(spec)
    prompt_md_path = workspace_root / "PROMPT.md"
    prompt_md_path.write_text(prompt_md_content, encoding="utf-8")
    ctx.console.print(
        Text("PROMPT.md written from product specification.", style="theme.status.success")
    )


def _clear_draft_artifact(workspace_root: Path) -> None:
    """Delete the draft artifact file if it exists."""
    artifact_file = workspace_root / ".agent" / "artifacts" / "product_spec.json"
    if artifact_file.exists():
        artifact_file.unlink()


def _display_agent_line(
    line: str,
    *,
    display_context: DisplayContext | None = None,
) -> None:
    """Render a single agent output line for the user.

    Added ``display_context`` parameter to remove inline Console construction per
    wt-007-consolidate-display; pass an explicit context to avoid env re-read
    between caller and default resolve.
    """
    visible = line.strip()
    if not visible:
        return
    ctx = display_context or make_display_context()
    ctx.console.print(visible)


def _run_single_invoke(
    runtime: _ManagedSessionRuntime,
    prompt_file: Path,
    *,
    session_id: str | None = None,
    display_context: DisplayContext | None = None,
) -> str | None:
    """Run a single agent turn and preserve resumable sessions for host-owned loops.

    Added ``display_context`` parameter to remove inline Console construction per
    wt-007-consolidate-display; pass an explicit context to avoid env re-read
    between caller and default resolve.
    """
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
            _display_agent_line(line, display_context=display_context)
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
    user_idea: str | None,
) -> Path:
    """Build and write the initial helper instructions, returning the path."""
    prompt_content = build_prompt_helper_prompt(
        submit_artifact_tool_name=submit_artifact_tool_name,
        existing_prompt_context=existing_prompt_context,
        has_draft=spec is not None,
        current_draft=spec,
        user_idea=user_idea,
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


def _produce_initial_artifact(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    existing_prompt_context: str | None,
    user_idea: str | None,
    submit_artifact_tool_name: str,
    *,
    display_context: DisplayContext | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """Run one non-interactive agent turn and return the submitted artifact, if any.

    The agent does not converse with the user: it is invoked once with the idea
    and/or existing PROMPT.md context and is expected to submit an artifact.
    """
    prompt_file = _update_prompt_file(
        workspace_root,
        submit_artifact_tool_name,
        existing_prompt_context,
        None,
        user_idea,
    )
    session_id = _run_single_invoke(
        runtime, prompt_file, display_context=display_context
    )
    return read_product_spec_artifact(workspace_root), session_id


def _run_review_loop(
    workspace_root: Path,
    runtime: _ManagedSessionRuntime,
    existing_prompt_context: str | None,
    submit_artifact_tool_name: str,
    spec: dict[str, object],
    session_id: str | None,
    *,
    display_context: DisplayContext | None = None,
) -> None:
    """Drive the host-owned refine/accept loop after an artifact exists.

    The only conversation is between the user and this orchestrator. On Refine,
    the user's prompt is sent to the agent for a fresh non-interactive turn; on
    Accept, PROMPT.md is written from the current draft and the loop ends.
    """
    current_spec = spec
    current_session_id = session_id

    while True:
        action = _prompt_review_choice(display_context=display_context)
        if action == _REVIEW_ACCEPT:
            _write_prompt_md(workspace_root, current_spec, display_context=display_context)
            return

        user_input = _prompt_for_user_input(
            "How should this specification be refined?", display_context=display_context
        )
        new_spec, current_session_id = _reinvoke_agent(
            workspace_root,
            runtime,
            submit_artifact_tool_name,
            existing_prompt_context,
            current_spec,
            user_input,
            current_session_id,
            display_context=display_context,
        )
        if new_spec is not None:
            current_spec = new_spec


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


def _initial_seed(
    workspace_root: Path, *, display_context: DisplayContext | None = None
) -> tuple[str | None, str | None]:
    """Resolve the first-turn seed as ``(existing_prompt_context, user_idea)``.

    When a ``PROMPT.md`` exists, it is read (with workspace-escape protection)
    and refined directly — no user prompt. Otherwise the orchestrator collects
    the idea from the user once, up front.
    """
    if (workspace_root / "PROMPT.md").exists():
        return FsWorkspace(workspace_root).read("PROMPT.md"), None
    return None, _prompt_for_user_input(
        "What do you want to build?", display_context=display_context
    )


def run_prompt_helper(config: UnifiedConfig, workspace_root: Path) -> None:
    """Run the prompt helper.

    This is a host-owned state machine in which the agent never converses with
    the user; the only conversation is between the user and this orchestrator:
    1. Seed the first turn from an existing PROMPT.md, or ask the user once for
       an idea when none exists.
    2. Invoke the agent non-interactively for one turn to produce an artifact.
    3. If no artifact is produced, report the failure and leave PROMPT.md alone.
    4. Otherwise drive the refine/accept loop, writing PROMPT.md only on Accept.
    """
    ctx = make_display_context()
    registry = AgentRegistry.from_config(config)

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
    existing_prompt_context, user_idea = _initial_seed(workspace_root, display_context=ctx)

    with ManagedAgentSessionRuntimeType.open(
        config=config,
        workspace_root=workspace_root,
        agent_config=agent_config,
        request=_prompt_helper_session_request(),
    ) as runtime:
        _clear_draft_artifact(workspace_root)
        spec, session_id = _produce_initial_artifact(
            workspace_root,
            runtime,
            existing_prompt_context,
            user_idea,
            submit_artifact_tool_name,
            display_context=ctx,
        )
        if spec is None:
            ctx.console.print(
                Text(
                    "The agent did not produce a product specification. "
                    "No PROMPT.md was written.",
                    style="theme.status.warning",
                )
            )
            return
        _run_review_loop(
            workspace_root,
            runtime,
            existing_prompt_context,
            submit_artifact_tool_name,
            spec,
            session_id,
            display_context=ctx,
        )


__all__ = ["run_prompt_helper"]
