"""Commit plumbing commands for Ralph CLI.

This module implements commit-related commands for generating
and applying commit messages.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo
from rich.text import Text

from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    extract_session_id,
    invoke_agent,
)
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.artifact_renderer import render_commit_message
from ralph.display.context import DisplayContext, make_display_context
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    has_staged_changes,
    stage_all,
)
from ralph.mcp.artifacts.commit_message import (
    delete_commit_message_artifacts,
    read_commit_message_artifact,
)
from ralph.mcp.protocol.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.mcp.server.lifecycle import SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name, claude_tool_name_prefix
from ralph.prompts.commit import (
    CommitPromptPayloadConfig,
    prompt_commit_message,
    prompt_commit_message_for_opencode,
)
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.prompts.template_registry import TemplateRegistry, default_template_dirs
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext

# Maximum number of staged files to display in output
_MAX_DISPLAY_FILES = 5
_DEFAULT_COMMIT_AGENT = "claude"
_VERBOSE_THRESHOLD = 2
_SKIP_PREFIX = "skip:"
_MAX_METADATA_PARTS = 5
_MISSING_COMMIT_ARTIFACT_REASON = "agent completed without writing a commit_message artifact"


@dataclass(frozen=True)
class CommitAgentAttempt:
    message: str = ""
    skipped: bool = False
    failure_detail: str = ""
    parsed_output: list[str] = field(default_factory=list)
    raw_output: list[str] = field(default_factory=list)
    resume_session_id: str | None = None


@dataclass(frozen=True)
class CommitAttemptContext:
    """Runtime context threaded into each commit agent invocation attempt.

    Attributes:
        repo_root: Repository root path.
        verbose: Whether verbose output is enabled.
        extra_env: Extra environment variables for the agent subprocess.
        agent_idle_timeout_seconds: Maximum idle time before killing a stalled agent.
    """

    repo_root: Path
    verbose: bool
    extra_env: dict[str, str]
    agent_idle_timeout_seconds: float = 300.0


@dataclass(frozen=True)
class CommitPlumbingOptions:
    """Options for commit plumbing operations.

    Attributes:
        generate_commit_msg: Generate commit message without applying.
        generate_commit: Generate and apply commit.
        show_commit_msg: Show current commit message.
        config_path: Path to configuration file.
        cli_overrides: CLI flag overrides.
    """

    generate_commit_msg: bool = False
    generate_commit: bool = False
    show_commit_msg: bool = False
    config_path: Path | None = None
    cli_overrides: dict[str, object] | None = None


@dataclass(frozen=True)
class CommitAgentResult:
    message: str = ""
    skipped: bool = False
    failure_details: list[str] = field(default_factory=list)


def commit_plumbing(
    *,
    options: CommitPlumbingOptions | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Handle commit plumbing operations.

    Args:
        options: Commit plumbing options.
        display_context: Display context for consistent rendering.
            If None, a context is created using make_display_context().
    """
    ctx = display_context if display_context is not None else make_display_context()
    console = ctx.console
    opts = options or CommitPlumbingOptions()

    try:
        repo_root = find_repo_root()
    except Exception as e:
        console.print(
            _styled_commit_status("Error", f"Not in a git repository: {e}", "theme.status.error")
        )
        return

    # Load configuration
    try:
        workspace_scope = (
            None if opts.config_path is not None else resolve_workspace_scope(repo_root)
        )
        config = load_config(opts.config_path, opts.cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        console.print(_styled_commit_status("Error loading config", str(e), "theme.status.error"))
        return

    if opts.show_commit_msg:
        _show_commit_message(repo_root, display_context=ctx)
        return

    if opts.generate_commit_msg or opts.generate_commit:
        _handle_agent_commit_generation(
            repo_root=repo_root,
            config=config,
            options=opts,
            display_context=ctx,
        )
        return

    if not has_staged_changes(repo_root):
        console.print(Text("No staged changes to commit", style="theme.status.warning"))
        return


def _handle_agent_commit_generation(
    *,
    repo_root: Path,
    config: UnifiedConfig,
    options: CommitPlumbingOptions,
    display_context: DisplayContext,
) -> None:
    ctx = display_context
    console = ctx.console
    generate = options.generate_commit_msg or options.generate_commit
    apply = options.generate_commit
    git_user_name = config.general.git_user_name
    git_user_email = config.general.git_user_email

    if not generate:
        return

    delete_commit_message_artifacts(repo_root)
    diff = _working_tree_diff(repo_root)
    if not diff.strip():
        console.print(Text("No changes to commit", style="theme.status.warning"))
        return

    registry = AgentRegistry.from_config(config)
    agents = _resolve_commit_message_agents(config, registry)
    if not agents:
        console.print(
            Text(
                "No commit-capable agents available in commit/review drains",
                style="theme.status.error",
            )
        )
        return

    result = _generate_commit_message_with_chain(
        diff=diff,
        repo_root=repo_root,
        registry=registry,
        agents=agents,
        verbose=config.general.verbosity >= _VERBOSE_THRESHOLD,
        agent_idle_timeout_seconds=config.general.agent_idle_timeout_seconds,
        display_context=ctx,
    )

    if result.skipped:
        delete_commit_message_artifacts(repo_root)
        console.print(Text("Skipping commit: agent requested skip", style="theme.status.warning"))
        return

    if not result.message:
        console.print(
            Text(
                "Failed to generate commit message from commit drain agents",
                style="theme.status.error",
            )
        )
        _print_commit_failure_details(result.failure_details, display_context=ctx)
        return

    persisted_message = read_commit_message_artifact(repo_root)
    if persisted_message is None:
        console.print(
            Text("Failed to persist generated commit message", style="theme.status.error")
        )
        return

    # Use the shared render_commit_message for consistent UI
    console.print(Text("\nGenerated commit message:", style="theme.status.success"))
    render_commit_message(repo_root, ctx)

    if apply:
        stage_all(repo_root)
        try:
            sha = create_commit(
                repo_root,
                persisted_message,
                author_name=git_user_name,
                author_email=git_user_email,
            )
            delete_commit_message_artifacts(repo_root)
            console.print(
                _styled_commit_status(
                    "Created commit", sha[:8], "theme.status.success", leading_newline=True
                )
            )
        except Exception as e:
            console.print(
                _styled_commit_status(
                    "Commit failed", str(e), "theme.status.error", leading_newline=True
                )
            )


def _resolve_commit_message_agents(config: UnifiedConfig, registry: AgentRegistry) -> list[str]:
    commit_chain_name = config.agent_drains.get("commit")
    commit_chain = config.agent_chains.get(commit_chain_name, []) if commit_chain_name else []

    review_chain_name = config.agent_drains.get("review")
    review_chain = config.agent_chains.get(review_chain_name, []) if review_chain_name else []

    commit_candidates = [
        name for name in commit_chain if _commit_drain_agent_supported(registry, name)
    ]
    if commit_candidates:
        return commit_candidates

    review_candidates = [
        name for name in review_chain if _commit_drain_agent_supported(registry, name)
    ]
    if review_candidates:
        return review_candidates

    default_candidates = [_DEFAULT_COMMIT_AGENT]
    return [name for name in default_candidates if _commit_drain_agent_supported(registry, name)]


def _commit_drain_agent_supported(registry: AgentRegistry, agent_name: str) -> bool:
    cfg = registry.get(agent_name)
    return cfg is not None and bool(cfg.can_commit)


def _working_tree_diff(repo_root: Path) -> str:
    repo = Repo(repo_root)
    if repo.head.is_valid():
        return cast("str", repo.git.diff("HEAD"))
    return cast("str", repo.git.diff("--cached"))


def _commit_submit_artifact_tool_names(
    registry: AgentRegistry,
    agents: list[str],
) -> tuple[str, ...]:
    names: list[str] = []
    for agent_name in agents:
        agent = registry.get(agent_name)
        if agent is None:
            continue
        tool_name = _submit_artifact_tool_name_for_transport(agent.transport)
        if tool_name not in names:
            names.append(tool_name)
    return tuple(names) or (SUBMIT_ARTIFACT_TOOL,)


def _submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    if transport == AgentTransport.CLAUDE:
        return claude_tool_name(SUBMIT_ARTIFACT_TOOL)
    return SUBMIT_ARTIFACT_TOOL


def _is_opencode_agent(agent: AgentConfig | None) -> bool:
    return agent is not None and agent.transport == AgentTransport.OPENCODE


def _commit_prompt_for_agent(
    agent: AgentConfig,
    diff: str,
    *,
    template_registry: TemplateRegistry,
    repo_root: Path,
) -> str:
    payload_output_dir = repo_root / ".agent" / "tmp" / "prompt_payloads"
    if _is_opencode_agent(agent):
        return prompt_commit_message_for_opencode(
            diff,
            submit_artifact_tool_name=SUBMIT_ARTIFACT_TOOL,
            payload_config=CommitPromptPayloadConfig(
                output_dir=payload_output_dir,
                name_prefix="commit_plumbing",
            ),
        )
    return prompt_commit_message(
        diff,
        template_registry=template_registry,
        submit_artifact_tool_names=_submit_artifact_tool_names_for_transport(agent.transport),
        payload_config=CommitPromptPayloadConfig(
            output_dir=payload_output_dir,
            name_prefix="commit_plumbing",
        ),
    )


def _submit_artifact_tool_names_for_transport(
    transport: AgentTransport | None,
) -> tuple[str, ...]:
    if transport == AgentTransport.CLAUDE:
        return SUBMIT_ARTIFACT_TOOL.prompt_aliases(
            tool_name_prefix=claude_tool_name_prefix(),
        )
    return (SUBMIT_ARTIFACT_TOOL,)


def _generate_commit_message_with_chain(  # noqa: PLR0913
    *,
    diff: str,
    repo_root: Path,
    registry: AgentRegistry,
    agents: list[str],
    verbose: bool,
    agent_idle_timeout_seconds: float = 300.0,
    display_context: DisplayContext,
) -> CommitAgentResult:
    template_dirs = (repo_root / ".agent" / "prompts" / "commit", *default_template_dirs(repo_root))
    template_registry = TemplateRegistry(template_dirs=template_dirs)
    bridge = _start_commit_bridge(repo_root)
    extra_env = _commit_bridge_env(bridge)
    failure_details: list[str] = []

    try:
        for agent_name in agents:
            cfg = registry.get(agent_name)
            if cfg is None:
                continue
            prompt = _commit_prompt_for_agent(
                cfg,
                diff,
                template_registry=template_registry,
                repo_root=repo_root,
            )
            prompt_file = _write_commit_prompt_file(repo_root, prompt)
            result = _generate_commit_message_with_agent(
                cfg,
                repo_root=repo_root,
                prompt_file=prompt_file,
                verbose=verbose,
                extra_env=extra_env,
                agent_idle_timeout_seconds=agent_idle_timeout_seconds,
                display_context=display_context,
            )
            failure_details.extend(result.failure_details)

            if result.skipped:
                return CommitAgentResult(skipped=True, failure_details=failure_details)
            if result.message:
                return CommitAgentResult(message=result.message)
    finally:
        bridge.shutdown()

    return CommitAgentResult(failure_details=failure_details)


def _generate_commit_message_with_agent(  # noqa: PLR0913
    agent: AgentConfig,
    *,
    repo_root: Path,
    prompt_file: str,
    verbose: bool,
    extra_env: dict[str, str],
    agent_idle_timeout_seconds: float = 300.0,
    display_context: DisplayContext,
) -> CommitAgentResult:
    failure_details: list[str] = []
    attempt_context = CommitAttemptContext(
        repo_root=repo_root,
        verbose=verbose,
        extra_env=extra_env,
        agent_idle_timeout_seconds=agent_idle_timeout_seconds,
    )
    initial_attempt = _invoke_commit_agent_attempt(
        agent,
        prompt_file=prompt_file,
        attempt_context=attempt_context,
        display_context=display_context,
    )
    if initial_attempt.failure_detail:
        failure_details.append(initial_attempt.failure_detail)
    else:
        return _finalize_commit_attempt(initial_attempt, failure_details)

    if not _is_missing_commit_artifact_failure(initial_attempt.failure_detail):
        return CommitAgentResult(failure_details=failure_details)

    if initial_attempt.resume_session_id:
        session_retry = _invoke_commit_agent_attempt(
            agent,
            prompt_file=prompt_file,
            attempt_context=attempt_context,
            session_id=initial_attempt.resume_session_id,
            display_context=display_context,
        )
        if session_retry.failure_detail:
            failure_details.append(session_retry.failure_detail)
        else:
            return _finalize_commit_attempt(session_retry, failure_details)

        if not _is_missing_commit_artifact_failure(session_retry.failure_detail):
            return CommitAgentResult(failure_details=failure_details)

    summary_prompt_file = _write_commit_prompt_file(
        repo_root,
        _summarized_retry_prompt(
            _read_retry_prompt_text(prompt_file),
            initial_attempt.parsed_output,
        ),
    )
    summary_retry = _invoke_commit_agent_attempt(
        agent,
        prompt_file=summary_prompt_file,
        attempt_context=attempt_context,
        session_id=initial_attempt.resume_session_id,
        display_context=display_context,
    )
    if summary_retry.failure_detail:
        failure_details.append(summary_retry.failure_detail)
    else:
        return _finalize_commit_attempt(summary_retry, failure_details)

    return CommitAgentResult(failure_details=failure_details)


def _is_skip_response(text: str) -> bool:
    return text.strip().lower().startswith(_SKIP_PREFIX)


def _invoke_commit_agent_attempt(
    agent: AgentConfig,
    *,
    prompt_file: str,
    attempt_context: CommitAttemptContext,
    session_id: str | None = None,
    display_context: DisplayContext,
) -> CommitAgentAttempt:
    delete_commit_message_artifacts(attempt_context.repo_root)
    try:
        lines = invoke_agent(
            agent,
            prompt_file,
            options=InvokeOptions(
                verbose=attempt_context.verbose,
                workspace_path=attempt_context.repo_root,
                extra_env=attempt_context.extra_env,
                pure=_is_opencode_agent(agent),
                session_id=session_id,
                idle_timeout_seconds=attempt_context.agent_idle_timeout_seconds,
                system_prompt_file=materialize_system_prompt(
                    workspace_root=attempt_context.repo_root,
                    name="commit",
                    default_current_prompt="Commit message generation task.",
                ),
            ),
        )
    except AgentInvocationError as exc:
        return CommitAgentAttempt(
            failure_detail=_format_agent_invocation_failure(
                agent.cmd, prompt_file, exc, parsed_output=[]
            )
        )

    try:
        parsed_output, raw_output = _collect_commit_agent_output(
            lines,
            parser_type=str(agent.json_parser),
            agent_name=agent.cmd.split()[0],
            verbose=attempt_context.verbose,
            display_context=display_context,
        )
    except AgentInvocationError as exc:
        return CommitAgentAttempt(
            failure_detail=_format_agent_invocation_failure(
                agent.cmd,
                prompt_file,
                exc,
                parsed_output=_parsed_output_from_invocation_error(exc),
            )
        )

    try:
        artifact_message = read_commit_message_artifact(attempt_context.repo_root)
    except Exception as exc:
        return CommitAgentAttempt(
            failure_detail=_format_commit_agent_failure(
                agent.cmd, prompt_file, parsed_output, str(exc)
            ),
            parsed_output=parsed_output,
            raw_output=raw_output,
            resume_session_id=extract_session_id(raw_output),
        )

    if not artifact_message:
        return CommitAgentAttempt(
            failure_detail=_format_commit_agent_failure(
                agent.cmd,
                prompt_file,
                parsed_output,
                _MISSING_COMMIT_ARTIFACT_REASON,
            ),
            parsed_output=parsed_output,
            raw_output=raw_output,
            resume_session_id=extract_session_id(raw_output),
        )

    if _is_skip_response(artifact_message):
        return CommitAgentAttempt(skipped=True, parsed_output=parsed_output, raw_output=raw_output)

    return CommitAgentAttempt(
        message=artifact_message, parsed_output=parsed_output, raw_output=raw_output
    )


def _finalize_commit_attempt(
    attempt: CommitAgentAttempt,
    failure_details: list[str],
) -> CommitAgentResult:
    if attempt.skipped:
        return CommitAgentResult(skipped=True, failure_details=failure_details)
    return CommitAgentResult(message=attempt.message, failure_details=failure_details)


def _is_missing_commit_artifact_failure(detail: str) -> bool:
    return _MISSING_COMMIT_ARTIFACT_REASON in detail


def _summarized_retry_prompt(base_prompt: str, parsed_output: list[str]) -> str:
    output_lines = "\n".join(parsed_output[-12:]) if parsed_output else "(no output captured)"
    example_content: dict[str, str] = {
        "type": "commit",
        "subject": "type(scope): description",
    }
    example_arguments: dict[str, str] = {
        "artifact_type": "commit_message",
        "content": json.dumps(example_content),
    }
    example_payload = json.dumps(example_arguments)
    return (
        f"{base_prompt}\n\n"
        "RETRY CONTEXT:\n"
        "Previous attempt failed to submit the required commit_message artifact.\n"
        "Treat the prior conversational output as a failure, not as permission "
        "to ask the user a question.\n"
        "Do not repeat the mistake. Submit the artifact now.\n"
        'Call the submit-artifact MCP tool with artifact_type="commit_message" '
        "and put the commit payload in the content field as a JSON string.\n"
        "Example MCP arguments:\n"
        f"{example_payload}\n"
        "If the submit-artifact MCP tool is still unavailable, write the raw commit payload JSON "
        "to .agent/tmp/commit_message.json instead.\n"
        "Write only the inner payload object, such as "
        '{"type":"commit","subject":"fix(scope): message"}, without artifact metadata.\n'
        "Do not use content_path for this retry.\n"
        "Do not use Bash, python, tee, printf, shell redirection, or any file-writing path "
        "other than writing .agent/tmp/commit_message.json directly.\n"
        "Message quality mistakes to avoid:\n"
        "- Bad: chore: update files -> Good: feat(mcp): add structured commit retries\n"
        "- Bad: fix: stuff -> Good: fix(parser): preserve prefixed transcript lines\n"
        "- Use chore only for repo maintenance, not meaningful code changes.\n"
        "- Omit the scope when the change spans multiple subsystems.\n"
        "- Include a body when the why is not obvious from the subject alone.\n"
        "Previous output summary:\n"
        f"{output_lines}\n"
    )


def _read_retry_prompt_text(prompt_file: str) -> str:
    path = Path(prompt_file)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_commit_prompt_file(repo_root: Path, prompt: str) -> str:
    prompt_dir = repo_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in prompt_dir.glob("commit_prompt*.md"):
        stale_path.unlink(missing_ok=True)
    prompt_path = prompt_dir / f"commit_prompt_{uuid.uuid4().hex}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    return str(prompt_path)


def _show_commit_message(repo_root: Path, *, display_context: DisplayContext) -> None:
    ctx = display_context
    console = ctx.console
    commit_message = read_commit_message_artifact(repo_root)
    if commit_message is None:
        console.print(Text("No commit message generated yet", style="theme.status.error"))
        return

    # Use the shared render_commit_message for consistent UI
    render_commit_message(repo_root, ctx)


def _print_commit_failure_details(
    failure_details: list[str],
    *,
    display_context: DisplayContext,
) -> None:
    ctx = display_context
    console = ctx.console
    for detail in failure_details:
        console.print(Text(detail, style="theme.status.error"))


def _format_agent_invocation_failure(
    agent_name: str,
    prompt_file: str,
    exc: AgentInvocationError,
    *,
    parsed_output: list[str] | None = None,
) -> str:
    stderr = exc.stderr.strip() or "(no stderr)"
    lines = [
        f"Agent: {agent_name}",
        f"Prompt file: {prompt_file}",
        f"Exit code: {exc.returncode}",
    ]
    if parsed_output:
        lines.extend(["Agent output:", *parsed_output])
    lines.extend(["Stderr:", stderr])
    return "\n".join(lines)


def _format_commit_agent_failure(
    agent_name: str,
    prompt_file: str,
    parsed_output: list[str],
    reason: str,
) -> str:
    lines = [
        f"Agent: {agent_name}",
        f"Prompt file: {prompt_file}",
        f"Reason: {reason}",
    ]
    if parsed_output:
        lines.extend(["Agent output:", *parsed_output])
    else:
        lines.append("Agent output: (no output captured)")
    return "\n".join(lines)


def _collect_commit_agent_output(
    lines: Iterable[object],
    *,
    parser_type: str,
    agent_name: str,
    verbose: bool,
    display_context: DisplayContext,
) -> tuple[list[str], list[str]]:
    ctx = display_context
    console = ctx.console
    parser = _resolve_commit_parser(parser_type)
    parsed_output: list[str] = []
    raw_output: list[str] = []
    try:

        def _raw_lines() -> Iterator[str]:
            for line in lines:
                raw_line = str(line)
                raw_output.append(raw_line)
                yield raw_line

        for parsed_line in parser.parse(_raw_lines()):
            rendered = _render_commit_agent_activity_line(parsed_line, agent_name)
            if rendered is None:
                continue
            parsed_output.append(rendered.plain)
            if verbose:
                console.print(rendered)
    except AgentInvocationError as exc:
        raise _invocation_error_with_output(exc, parsed_output) from exc
    return parsed_output, raw_output


def _resolve_commit_parser(parser_type: str) -> AgentParser:
    try:
        return get_parser(parser_type)
    except ValueError:
        return get_parser("generic")


def _render_commit_agent_activity_line(output: AgentOutputLine, agent_name: str) -> Text | None:
    rendered: Text | None = None

    if output.type == "text":
        content = output.content.strip()
        if content:
            rendered = _styled_commit_prefix(agent_name, "theme.text.emphasis")
            rendered.append(content)
    elif output.type == "tool_use":
        tool_name = output.content.strip() or "unknown-tool"
        summary = _tool_input_summary(output.metadata)
        rendered = _styled_commit_prefix(f"{agent_name} tool", "theme.phase.review_analysis")
        rendered.append(tool_name)
        if summary:
            rendered.append(f" ({summary})")
    elif output.type == "tool_result":
        result = output.content.strip() or _event_summary(output)
        if result:
            rendered = _styled_commit_prefix(f"{agent_name} tool result", "theme.text.muted")
            rendered.append(result)
    elif output.type == "error":
        error = output.content.strip() or "unknown error"
        rendered = _styled_commit_prefix(f"{agent_name} error", "theme.status.error")
        rendered.append(error)
    else:
        rendered = _styled_commit_prefix(f"{agent_name} {output.type}", "theme.text.muted")
        rendered.append(_event_summary(output))

    return rendered


def _styled_commit_prefix(label: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    return text


def _styled_commit_status(
    label: str,
    detail: str,
    style: str,
    *,
    leading_newline: bool = False,
) -> Text:
    text = Text()
    if leading_newline:
        text.append("\n")
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _event_summary(output: AgentOutputLine) -> str:
    content = output.content.strip()
    if content:
        return content
    if output.metadata:
        summary = _metadata_summary(output.metadata)
        if summary:
            return summary
    return "(no details)"


def _tool_input_summary(metadata: dict[str, object]) -> str:
    input_obj = metadata.get("input")
    if isinstance(input_obj, dict):
        return _metadata_summary(cast("dict[str, object]", input_obj))
    return ""


def _metadata_summary(metadata: dict[str, object]) -> str:
    preferred_keys = (
        "status",
        "summary",
        "phase",
        "tool",
        "name",
        "command",
        "workdir",
        "path",
        "result",
        "output",
        "error",
        "message",
    )
    parts: list[str] = []
    for key in preferred_keys:
        if key not in metadata:
            continue
        value = _format_metadata_value(metadata[key])
        if value:
            parts.append(f"{key}={value}")
    if parts:
        return "; ".join(parts)
    for key, value_obj in metadata.items():
        value = _format_metadata_value(value_obj)
        if value:
            parts.append(f"{key}={value}")
        if len(parts) >= _MAX_METADATA_PARTS:
            break
    return "; ".join(parts)


def _format_metadata_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    return ""


def _invocation_error_with_output(
    exc: AgentInvocationError,
    parsed_output: list[str],
) -> AgentInvocationError:
    return AgentInvocationError(
        exc.agent_name,
        exc.returncode,
        exc.stderr,
        parsed_output=list(parsed_output),
    )


def _parsed_output_from_invocation_error(exc: AgentInvocationError) -> list[str]:
    parsed_output: list[str] = exc.parsed_output
    return parsed_output


def _start_commit_bridge(repo_root: Path) -> SessionBridgeLike:
    session_mcp_plan = build_session_mcp_plan(
        transport=None,
        drain="commit",
        workspace_path=repo_root,
    )
    session = AgentSession(
        session_id=f"commit-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="commit",
        capabilities=set(session_mcp_plan.capabilities),
    )
    workspace = FsWorkspace(repo_root)
    return start_mcp_server(session, workspace, extra_env=session_mcp_plan.server_env)


def _commit_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return {
        MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
        MCP_RUN_ID_ENV: "commit-plumbing",
    }
