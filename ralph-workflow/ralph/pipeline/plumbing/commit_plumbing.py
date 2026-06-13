"""Commit plumbing: chain iteration + classifier routing for ``commit`` CLI.

The ``commit`` / ``--generate-commit`` CLI command needs pipeline
functionality (chain iteration across commit-capable agents, retry
decisioning, session resume). Previously the CLI command reimplemented
this orchestration inline — which kept failure-classification and
``extract_transport_session_id(...)`` calls duplicated against the
shared retry loop in :func:`ralph.agents.invoke._direct_mcp_recovery.run_with_direct_mcp_recovery`.

This module is the SINGLE owner of commit-time chain iteration. The
CLI surface in :mod:`ralph.cli.commands.commit` calls
:func:`run_commit_plumbing` and stays thin (option parsing, output
formatting, exit codes only).

Inline classifier construction in the pre-fix ``commit.py`` is
MOVED behind :func:`should_reset_tool_registry` so every recoverable
failure in the commit path is routed through the same
:func:`run_with_direct_mcp_recovery` retry loop the pipeline runner
uses. The plumbing module does not construct the classifier
directly (enforced by the anti-drift regression test).

Public surface (kept narrow on purpose):

- :class:`CommitAgentResult` — result dataclass with ``session_id``,
  ``last_error``, ``output`` so the CLI can format the result.
- :func:`run_commit_plumbing` — the chain-iteration entry point.
"""

from __future__ import annotations

import importlib
import json
import typing
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from rich.text import Text

from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    extract_transport_session_id,
    invoke_agent,
)
from ralph.agents.invoke._direct_mcp_recovery import (
    default_direct_mcp_retry_limit,
    run_with_direct_mcp_recovery,
    summarize_retry_failure_evidence,
)

# INVARIANT: must use the public ralph.agents.invoke surface so the per-file
# exclusion in tests/test_no_anti_drift_regression.py stays enforced.
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.cli.commands._commit_agent_attempt import CommitAgentAttempt
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.enums import AgentTransport
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    COMMIT_MESSAGE_TYPE,
    delete_commit_message_artifacts,
    normalize_commit_message_content,
    read_commit_message_artifact,
)
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name_prefix
from ralph.phases.required_artifacts import RequiredArtifact, build_retry_hint
from ralph.pipeline.factory import (
    MaterializeSystemPromptFn,
    PipelineDeps,
    build_default_pipeline_deps,
)
from ralph.pipeline.session_bridge import (
    bridge_env_for,
    build_session_bridge,
    reset_tool_registry_callback,
)
from ralph.prompts.commit import (
    CommitPromptPayloadConfig,
    prompt_commit_message,
    prompt_commit_message_for_opencode,
)
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.prompts.template_registry import TemplateRegistry, default_template_dirs
from ralph.recovery.failure_classifier import (
    is_unsubmitted_artifact_failure,
    should_reset_tool_registry,
)

if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Iterator

    from ralph.cli.commands._commit_chain_config import CommitChainConfig
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.policy.models import AgentsPolicy

# Late-binding reference for the test-patch surface: tests in
# ``tests/test_cli_commit_command.py`` (and friends) patch names
# on ``ralph.cli.commands.commit`` at runtime (e.g.
# ``start_commit_bridge``, ``materialize_system_prompt``,
# ``invoke_agent``). Importing the module here at top level (rather
# than inside the function body) satisfies PLC0415 and gives the
# plumbing a stable handle to look up the latest attribute values
# at call time.
_commit_module: types.ModuleType = importlib.import_module("ralph.cli.commands.commit")

__all__ = [
    "CommitAgentResult",
    "collect_commit_agent_output",
    "invoke_commit_agent_attempt",
    "run_commit_plumbing",
]


_T = TypeVar("_T")

_VERBOSE_THRESHOLD = 2
_SKIP_PREFIX = "skip:"
_MAX_METADATA_PARTS = 5
_MISSING_COMMIT_ARTIFACT_REASON = "agent completed without writing a commit_message artifact"
_MAX_COMMIT_PARSED_OUTPUT_LINES = 128
_MAX_COMMIT_RAW_OUTPUT_LINES = 256
_MODELED_FLAG_PARTS = 2


@dataclass(frozen=True)
class CommitAgentResult:
    """Aggregated result returned after all commit-message agent attempts complete.

    The CLI surface consumes this dataclass for output formatting and
    exit code derivation. The new ``output`` field exposes the captured
    agent lines so the CLI can render transcript content when
    ``verbose`` is enabled.
    """

    message: str = ""
    skipped: bool = False
    failure_details: list[str] = field(default_factory=list)
    session_id: str | None = None
    last_error: Exception | None = None
    output: list[str] = field(default_factory=list)


def run_commit_plumbing(
    *,
    diff: str,
    repo_root: Path,
    chain_config: CommitChainConfig,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps | None = None,
) -> CommitAgentResult:
    """Iterate the commit chain, retrying through the shared recovery loop.

    The chain iterates over each agent in ``chain_config.agents``; for
    each agent, the same retry-recovery machinery the pipeline runner
    uses is invoked via :func:`run_with_direct_mcp_recovery`. Session
    resume is decided through
    :func:`recovery_action_for_failure_reason` and
    :func:`extract_transport_session_id` — the single source of truth
    for those decisions.

    ``pipeline_deps`` carries injectable collaborators. When omitted,
    production defaults are used and the legacy late-bound bridge
    resolver is preserved so existing test monkeypatches continue to
    work.

    No inline failure-classifier construction sites live in this
    module; recovery decisions are routed exclusively through the
    shared retry loop via :func:`should_reset_tool_registry`.
    """
    pipeline_deps_provided = pipeline_deps is not None
    if pipeline_deps is None:
        pipeline_deps = build_default_pipeline_deps(
            cast("UnifiedConfig", chain_config.general_config),
            display_context,
        )
    template_dirs = (repo_root / ".agent" / "prompts" / "commit", *default_template_dirs(repo_root))
    template_registry = TemplateRegistry(template_dirs=template_dirs)
    if pipeline_deps_provided:
        bridge = pipeline_deps.bridge_factory(
            workspace_root=repo_root,
            drain="commit",
            agents_policy=chain_config.agents_policy,
            session_id_prefix="commit",
            model_identity=pipeline_deps.model_identity,
        )
    else:
        # Preserve the legacy late-bound test-patch surface: tests
        # monkeypatch ``ralph.cli.commands.commit.start_commit_bridge``.
        bridge_fn = _resolve_commit_start_commit_bridge()
        try:
            bridge = bridge_fn(repo_root, agents_policy=chain_config.agents_policy)
        except TypeError:
            bridge = bridge_fn(repo_root)
    extra_env: dict[str, str] | None = _commit_bridge_env(bridge)
    # Normalize the key set so downstream consumers (and tests)
    # observe string keys, never the ``McpEnvVar`` enum.
    if extra_env is not None:
        extra_env = _stringify_extra_env(extra_env)
    failure_details: list[str] = []
    last_session_id: str | None = None
    last_error: Exception | None = None
    output_lines: list[str] = []

    materializer = pipeline_deps.system_prompt_materializer
    try:
        for agent_name in chain_config.agents:
            cfg = chain_config.registry.get(agent_name)
            if cfg is None:
                continue
            prompt = _commit_prompt_for_agent(
                cfg,
                diff,
                template_registry=template_registry,
                repo_root=repo_root,
            )
            prompt_file = _resolve_commit_write_prompt_file()(repo_root, prompt)
            attempt_ctx = CommitAttemptContext(
                repo_root=repo_root,
                verbose=chain_config.verbose,
                extra_env=_stringify_extra_env(extra_env) or {},
                general_config=chain_config.general_config,
                bridge=bridge,
            )
            result = _generate_commit_message_with_agent(
                cfg,
                prompt_file=prompt_file,
                attempt_context=attempt_ctx,
                display_context=display_context,
                prior_session_id=last_session_id,
                output_collector=output_lines,
                materializer=materializer,
            )
            failure_details.extend(result.failure_details)

            if result.skipped:
                return CommitAgentResult(
                    skipped=True,
                    failure_details=failure_details,
                    session_id=last_session_id,
                    last_error=last_error,
                    output=list(output_lines),
                )
            if result.message:
                return CommitAgentResult(
                    message=result.message,
                    failure_details=failure_details,
                    session_id=last_session_id,
                    last_error=last_error,
                    output=list(output_lines),
                )
    finally:
        bridge.shutdown()

    return CommitAgentResult(
        failure_details=failure_details,
        session_id=last_session_id,
        last_error=last_error,
        output=list(output_lines),
    )


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
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return SUBMIT_ARTIFACT_TOOL.prompt_aliases(
            tool_name_prefix=claude_tool_name_prefix(),
        )
    return (SUBMIT_ARTIFACT_TOOL,)


def _commit_required_artifact() -> RequiredArtifact:
    return RequiredArtifact(
        phase="commit",
        artifact_type=COMMIT_MESSAGE_TYPE,
        json_path=COMMIT_MESSAGE_ARTIFACT,
        markdown_path=None,
        normalizer=normalize_commit_message_content,
        artifact_required=True,
    )


def _generate_commit_message_with_agent(
    agent: AgentConfig,
    *,
    prompt_file: str,
    attempt_context: CommitAttemptContext,
    display_context: DisplayContext,
    prior_session_id: str | None = None,
    output_collector: list[str] | None = None,
    materializer: MaterializeSystemPromptFn | None = None,
) -> CommitAgentResult:
    failure_details: list[str] = []

    def _record_retry_failure(lines: list[str]) -> None:
        if not lines:
            return
        failure_details.append(
            "retryable failure recovered: " + summarize_retry_failure_evidence(lines)
        )

    raw_max_retries: object = (
        attempt_context.general_config.max_same_agent_retries
        if attempt_context.general_config is not None
        else None
    )
    max_retries = default_direct_mcp_retry_limit(raw_max_retries)
    initial_attempt, _last_session_id, _last_error = _run_commit_agent_attempt_with_recovery(
        agent,
        prompt_file=prompt_file,
        attempt_context=attempt_context,
        display_context=display_context,
        max_retries=max_retries,
        prior_session_id=prior_session_id,
        on_retry_failure=_record_retry_failure,
        output_collector=output_collector,
        materializer=materializer,
    )
    if not initial_attempt.failure_detail:
        return _finalize_commit_attempt(initial_attempt, failure_details)
    failure_details.append(initial_attempt.failure_detail)

    latest_attempt = initial_attempt

    if _is_missing_commit_artifact_failure(latest_attempt.failure_detail):
        if initial_attempt.resume_session_id:
            session_retry, _session_id, _err = _run_commit_agent_attempt_with_recovery(
                agent,
                prompt_file=prompt_file,
                attempt_context=attempt_context,
                display_context=display_context,
                max_retries=max_retries,
                prior_session_id=initial_attempt.resume_session_id,
                on_retry_failure=_record_retry_failure,
                output_collector=output_collector,
                materializer=materializer,
            )
            if not session_retry.failure_detail:
                return _finalize_commit_attempt(session_retry, failure_details)
            failure_details.append(session_retry.failure_detail)
            latest_attempt = session_retry

        if _is_missing_commit_artifact_failure(latest_attempt.failure_detail):
            summary_prompt_file = _resolve_commit_write_prompt_file()(
                attempt_context.repo_root,
                _summarized_retry_prompt(
                    _read_retry_prompt_text(prompt_file),
                    latest_attempt.parsed_output,
                    agent,
                ),
            )
            summary_retry, _sid, _e = _run_commit_agent_attempt_with_recovery(
                agent,
                prompt_file=summary_prompt_file,
                attempt_context=attempt_context,
                display_context=display_context,
                max_retries=max_retries,
                prior_session_id=initial_attempt.resume_session_id,
                on_retry_failure=_record_retry_failure,
                output_collector=output_collector,
                materializer=materializer,
            )
            if not summary_retry.failure_detail:
                return _finalize_commit_attempt(summary_retry, failure_details)
            failure_details.append(summary_retry.failure_detail)

    return CommitAgentResult(failure_details=failure_details)


def _reset_tool_registry_callback(
    bridge: object | None,
) -> typing.Callable[[], object] | None:
    callback = reset_tool_registry_callback(bridge)
    if callback is None:
        return None
    return cast("typing.Callable[[], object]", callback)


def _run_commit_agent_attempt_with_recovery(
    agent: AgentConfig,
    *,
    prompt_file: str,
    attempt_context: CommitAttemptContext,
    display_context: DisplayContext,
    max_retries: int,
    prior_session_id: str | None = None,
    on_retry_failure: typing.Callable[[list[str]], object] | None = None,
    output_collector: list[str] | None = None,
    materializer: MaterializeSystemPromptFn | None = None,
) -> tuple[CommitAgentAttempt, str | None, Exception | None]:
    """Run a single commit-agent attempt with the shared retry loop.

    Returns ``(attempt, last_session_id, last_error)``. The session id
    and last exception are captured so the chain orchestrator can thread
    them into the next attempt (when ``recovery_action`` is ``resume``)
    and report them in the final :class:`CommitAgentResult`.
    """
    last_session_id: str | None = prior_session_id
    last_error: Exception | None = None

    def _capture_session_id(sid: str) -> None:
        nonlocal last_session_id
        last_session_id = sid

    try:
        attempt = run_with_direct_mcp_recovery(
            lambda retry_session_id, capture_session_id: invoke_commit_agent_attempt(
                agent,
                prompt_file=prompt_file,
                attempt_context=attempt_context,
                session_id=retry_session_id or last_session_id,
                display_context=display_context,
                session_id_sink=capture_session_id,
                materializer=materializer,
            ),
            max_retries=max_retries,
            reset_tool_registry=_reset_tool_registry_callback(attempt_context.bridge),
            on_retry_failure=on_retry_failure,
        )
        return attempt, last_session_id, last_error
    except AgentInvocationError as exc:
        last_error = exc
        parsed_output = _parsed_output_from_invocation_error(exc)
        if output_collector is not None:
            output_collector.extend(parsed_output)
        captured = extract_transport_session_id(tuple(parsed_output))
        if captured is not None:
            last_session_id = captured
        return (
            CommitAgentAttempt(
                failure_detail=_format_agent_invocation_failure(
                    agent.cmd,
                    prompt_file,
                    exc,
                    parsed_output=parsed_output,
                ),
                parsed_output=parsed_output,
                resume_session_id=captured,
            ),
            last_session_id,
            last_error,
        )


def _is_skip_response(text: str) -> bool:
    return text.strip().lower().startswith(_SKIP_PREFIX)


def invoke_commit_agent_attempt(
    agent: AgentConfig,
    *,
    prompt_file: str,
    attempt_context: CommitAttemptContext,
    session_id: str | None = None,
    display_context: DisplayContext,
    session_id_sink: typing.Callable[[str], None] | None = None,
    materializer: MaterializeSystemPromptFn | None = None,
) -> CommitAgentAttempt:
    """Run one commit-agent invocation attempt and return its result."""
    # Late-binding: tests patch ``ralph.cli.commands.commit.{X}``; look the
    # names up at call time so the patches take effect even though this
    # function lives in the plumbing module. (The import is module-level
    # to satisfy PLC0415; the function-level ``getattr`` is what makes
    # the patches take effect.)
    materialize = _get_patched(
        _commit_module,
        "materialize_system_prompt",
        materializer if materializer is not None else materialize_system_prompt,
    )
    invoke = _get_patched(_commit_module, "invoke_agent", invoke_agent)
    delete_artifacts = _get_patched(
        _commit_module, "delete_commit_message_artifacts", delete_commit_message_artifacts
    )
    read_artifact = _get_patched(
        _commit_module, "read_commit_message_artifact", read_commit_message_artifact
    )

    delete_artifacts(attempt_context.repo_root)
    system_prompt = materialize(
        workspace_root=attempt_context.repo_root,
        name="commit",
        default_current_prompt="Commit message generation task.",
    )
    if attempt_context.general_config is not None:
        options = build_invoke_options_from_config(
            attempt_context.general_config,
            InvokeRuntimeOptions(
                verbose=attempt_context.verbose,
                workspace_path=attempt_context.repo_root,
                extra_env=_stringify_extra_env(attempt_context.extra_env),
                pure=_is_opencode_agent(agent),
                session_id=session_id,
                system_prompt_file=system_prompt,
                required_artifact=_commit_required_artifact(),
            ),
        )
    else:
        options = InvokeOptions(
            verbose=attempt_context.verbose,
            workspace_path=attempt_context.repo_root,
            extra_env=_stringify_extra_env(attempt_context.extra_env),
            pure=_is_opencode_agent(agent),
            session_id=session_id,
            system_prompt_file=system_prompt,
            required_artifact=_commit_required_artifact(),
        )
    try:
        lines = invoke(
            agent,
            prompt_file,
            options=options,
        )
    except AgentInvocationError as exc:
        parsed_output = _parsed_output_from_invocation_error(exc)
        if exc.parsed_output:
            parsed_output = list(exc.parsed_output)
        resume_session_id = (
            extract_transport_session_id(tuple(parsed_output)) if parsed_output else None
        )
        if should_reset_tool_registry(exc, phase="commit", agent=agent.cmd):
            raise _invocation_error_with_output(exc, parsed_output) from exc
        return CommitAgentAttempt(
            failure_detail=_format_agent_invocation_failure(
                agent.cmd, prompt_file, exc, parsed_output=parsed_output
            ),
            parsed_output=parsed_output,
            resume_session_id=resume_session_id,
        )

    try:
        parsed_output, raw_output, resume_session_id = collect_commit_agent_output(
            lines,
            parser_type=str(agent.json_parser),
            agent_name=agent.cmd.split()[0],
            verbose=attempt_context.verbose,
            display_context=display_context,
            session_id_sink=session_id_sink,
        )
    except AgentInvocationError as exc:
        # The shared classifier is consulted ONLY through the
        # ``should_reset_tool_registry`` helper; we never construct
        # the classifier inline (enforced by the anti-drift test).
        if should_reset_tool_registry(exc, phase="commit", agent=agent.cmd):
            raise _invocation_error_with_output(
                exc, _parsed_output_from_invocation_error(exc)
            ) from exc
        return CommitAgentAttempt(
            failure_detail=_format_agent_invocation_failure(
                agent.cmd,
                prompt_file,
                exc,
                parsed_output=_parsed_output_from_invocation_error(exc),
            )
        )

    try:
        artifact_message = read_artifact(attempt_context.repo_root)
    except Exception as exc:
        return CommitAgentAttempt(
            failure_detail=_format_commit_agent_failure(
                agent.cmd, prompt_file, parsed_output, str(exc)
            ),
            parsed_output=parsed_output,
            raw_output=raw_output,
            resume_session_id=resume_session_id,
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
            resume_session_id=resume_session_id,
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
    # An empty / no-tool-call exit submitted nothing, so it is the same
    # "unsubmitted artifact" condition the pipeline recovers from — route it to
    # the shared detector instead of only matching the clean-exit string.
    return _MISSING_COMMIT_ARTIFACT_REASON in detail or is_unsubmitted_artifact_failure((detail,))


def _summarized_retry_prompt(
    base_prompt: str, parsed_output: list[str], agent: AgentConfig
) -> str:
    """Commit's resubmit prompt — built by the SAME `build_retry_hint` the pipeline
    phase gates use, so the artifact-missing retry guidance cannot drift between
    the commit command and the pipeline. Only the commit-specific example payload
    and submit-tool name are supplied here.
    """
    required = _commit_required_artifact()
    example_content: dict[str, str] = {"type": "commit", "subject": "type(scope): description"}
    example_arguments: dict[str, str] = {
        "artifact_type": required.artifact_type,
        "content": json.dumps(example_content),
    }
    tool_names = _submit_artifact_tool_names_for_transport(agent.transport)
    hint = build_retry_hint(
        required.phase,
        _MISSING_COMMIT_ARTIFACT_REASON,
        registry={required.phase: required},
        prior_output=parsed_output,
        submit_tool_name=tool_names[0] if tool_names else None,
        example_payload=json.dumps(example_arguments),
    )
    return f"{base_prompt}\n\n{hint}"


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


def collect_commit_agent_output(
    lines: Iterable[object],
    *,
    parser_type: str,
    agent_name: str,
    verbose: bool,
    display_context: DisplayContext,
    session_id_sink: typing.Callable[[str], None] | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Consume agent output lines, returning (parsed_lines, raw_lines, resume_session_id)."""
    parser = _resolve_commit_parser(parser_type)
    parsed_output: deque[str] = deque(maxlen=_MAX_COMMIT_PARSED_OUTPUT_LINES)
    raw_output: deque[str] = deque(maxlen=_MAX_COMMIT_RAW_OUTPUT_LINES)
    resume_session_id: str | None = None
    try:

        def _raw_lines() -> Iterator[str]:
            for line in lines:
                raw_line = str(line)
                raw_output.append(raw_line)
                session_id = extract_transport_session_id((raw_line,))
                nonlocal resume_session_id
                if session_id is not None:
                    resume_session_id = session_id
                    if session_id_sink is not None:
                        session_id_sink(session_id)
                yield raw_line

        for parsed_line in parser.parse(_raw_lines()):
            rendered = _render_commit_agent_activity_line(parsed_line, agent_name)
            if rendered is None:
                continue
            parsed_output.append(rendered.plain)
            if verbose:
                display = resolve_active_display(None, display_context)
                display.emit_status(rendered.plain)
    except AgentInvocationError as exc:
        raise _invocation_error_with_output(
            exc,
            list(parsed_output),
            raw_output=list(raw_output),
        ) from exc
    return list(parsed_output), list(raw_output), resume_session_id


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
    match value:
        case str():
            return value.strip()
        case bool():
            return "true" if value else "false"
        case int() | float():
            return str(value)
        case dict() | list() | tuple():
            return json.dumps(value, default=str, sort_keys=True)
        case _:
            return ""


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


def _invocation_error_with_output(
    exc: AgentInvocationError,
    parsed_output: list[str],
    raw_output: list[str] | None = None,
) -> AgentInvocationError:
    return AgentInvocationError(
        exc.agent_name,
        exc.returncode,
        exc.stderr,
        parsed_output=list(raw_output or parsed_output),
    )


def _parsed_output_from_invocation_error(exc: AgentInvocationError) -> list[str]:
    parsed_output: list[str] = exc.parsed_output
    return parsed_output


def _start_commit_bridge(repo_root: Path, *, agents_policy: AgentsPolicy) -> SessionBridgeLike:
    return build_session_bridge(
        workspace_root=repo_root,
        drain="commit",
        agents_policy=agents_policy,
        session_id_prefix="commit",
    )


def _commit_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return bridge_env_for(bridge, run_id_label="commit-plumbing")


# Module-level alias so the test-patch surface in
# ``tests/test_cli_commit_command.py`` (and friends) can patch
# ``ralph.cli.commands.commit.start_commit_bridge`` and have the
# patched value be honored at the canonical call site. The alias
# is intentionally a separate object (not a reference) so
# monkeypatching the commit module does not affect the plumbing
# module's view (and vice versa). The call site uses the
# ``plumbing.start_commit_bridge`` attribute so the patch
# transparently propagates through both surfaces.
start_commit_bridge = _start_commit_bridge


# Resolver helpers: late-binding lookups into the commit module so
# the test-patch surface (which monkeypatches
# ``ralph.cli.commands.commit.start_commit_bridge`` and friends)
# is honored at the canonical plumbing call site. We don't use
# ``cast`` because the project's mypy config disallows explicit
# ``Any``; instead we declare the protocol and assert the
# attribute is callable at the call boundary.
class _CommitStartBridgeProto(typing.Protocol):
    def __call__(self, repo_root: Path, **kwargs: object) -> SessionBridgeLike: ...


class _CommitWritePromptFileProto(typing.Protocol):
    def __call__(self, repo_root: Path, prompt: str) -> str: ...


def _resolve_commit_start_commit_bridge() -> _CommitStartBridgeProto:
    return typing.cast("_CommitStartBridgeProto", _commit_module.start_commit_bridge)


def _resolve_commit_write_prompt_file() -> _CommitWritePromptFileProto:
    return typing.cast("_CommitWritePromptFileProto", _commit_module.write_commit_prompt_file)


def _stringify_extra_env(extra_env: object) -> dict[str, str] | None:
    """Convert an ``extra_env`` mapping to a ``{str: str}`` mapping.

    The plumbing module builds the ``extra_env`` mapping with
    :data:`McpEnvVar` enum keys; downstream consumers (the tests
    in particular) expect plain string keys. This helper preserves
    the original mapping when the keys are already strings, and
    converts enum keys via ``str()`` otherwise.
    """
    if extra_env is None:
        return None
    if not isinstance(extra_env, typing.Mapping):
        return None
    return {str(key): str(value) for key, value in extra_env.items()}


write_commit_prompt_file = _write_commit_prompt_file
render_commit_agent_activity_line = _render_commit_agent_activity_line


def _get_patched(  # noqa: UP047
    module: types.ModuleType,
    name: str,
    fallback: _T,
) -> _T:
    """Return ``getattr(module, name, fallback)`` with proper typing.

    Tests patch the corresponding names on
    ``ralph.cli.commands.commit`` at runtime. We can't statically type
    ``getattr`` as ``_T`` (mypy would warn) without an explicit cast,
    so this helper centralises the cast pattern.
    """
    value: _T = getattr(module, name, fallback)
    if value is None:
        return fallback
    return value


# Reference the dynamic ``_get_patched`` helper so mypy can verify the
# fallback closure (the helper is also used at runtime).
_ = _get_patched
