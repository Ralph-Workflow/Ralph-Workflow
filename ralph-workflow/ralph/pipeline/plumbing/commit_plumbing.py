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

import dataclasses
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
    summarize_retry_failure_evidence,
)
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser, resolve_parser_key
from ralph.cli.commands._commit_agent_attempt import CommitAgentAttempt
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.enums import AgentTransport
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    COMMIT_MESSAGE_TYPE,
    delete_commit_message_artifacts,
    normalize_commit_message_content,
    read_commit_message_artifact,
)
from ralph.mcp.artifacts.completion_receipts import clear_run_receipts
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name_prefix
from ralph.phases.required_artifacts import RequiredArtifact, build_retry_hint
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.factory import (
    DefaultPipelineFactory,
    MaterializeSystemPromptFn,
    PipelineCore,
    PipelineDeps,
    _resolve_phase_required_artifact,
)
from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime
from ralph.pipeline.session_bridge import (
    BridgeFactory,
    bridge_env_for,
    build_session_bridge,
    reset_tool_registry_callback,
)
from ralph.policy.models import AgentsPolicy
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
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Iterator

    from ralph.cli.commands._commit_chain_config import CommitChainConfig
    from ralph.config.models import AgentConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge, SessionBridgeLike
    from ralph.pro_support.hooks import ProPipelineHooks

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
_COMMIT_RUN_ID = "commit-plumbing"


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


def _commit_required_artifact() -> RequiredArtifact:
    return RequiredArtifact(
        phase="commit",
        artifact_type=COMMIT_MESSAGE_TYPE,
        json_path=COMMIT_MESSAGE_ARTIFACT,
        markdown_path=None,
        normalizer=normalize_commit_message_content,
        artifact_required=True,
    )


def _commit_artifact_requirements_resolver(
    pipeline_policy: object,
    artifacts_policy: object,
    *,
    phase: str,
    drain: str | None = None,
) -> RequiredArtifact | None:
    del pipeline_policy, artifacts_policy, phase, drain
    return _commit_required_artifact()


def _apply_commit_deps_overrides(
    deps: PipelineDeps,
    *,
    materializer: MaterializeSystemPromptFn | None,
    registry: object | None,
) -> PipelineDeps:
    """Apply commit-specific overrides to a ``PipelineDeps`` bundle.

    Ensures the commit plumbing path uses the commit-specific artifact
    resolver only when no custom resolver was injected, composes the chain
    registry, and swaps in the late-bound test-patch bridge factory when the
    default production bridge is in use. Core collaborators are replaced on
    the embedded :class:`PipelineCore`; extended fields are replaced on
    ``deps`` itself.
    """
    core = deps.core
    if materializer is not None:
        core = dataclasses.replace(core, system_prompt_materializer=materializer)
    # Only replace the default artifact resolver. If Pro or a test injected a
    # custom resolver via PipelineCore/ProPipelineHooks, preserve it so the
    # commit path shares the same injectable collaborator contract as the
    # main pipeline.
    if core.artifact_requirements_resolver is _resolve_phase_required_artifact:
        core = dataclasses.replace(
            core, artifact_requirements_resolver=_commit_artifact_requirements_resolver
        )

    if registry is not None:

        def _registry_factory(_config: UnifiedConfig) -> object:
            return registry

        deps = dataclasses.replace(deps, registry_factory=_registry_factory)
    # Preserve the legacy late-bound test-patch surface when no explicit
    # bridge factory was injected. Tests monkeypatch
    # ``ralph.cli.commands.commit.start_commit_bridge``; the wrapper below
    # resolves that attribute at call time so the patch is honored while
    # still routing default commit bridge creation through PipelineDeps.
    if deps.bridge_factory is build_session_bridge:
        deps = dataclasses.replace(deps, bridge_factory=_default_commit_bridge_factory)
    if core is not deps.core:
        deps = dataclasses.replace(deps, core=core)
    return deps


def _commit_pipeline_deps(
    config: UnifiedConfig,
    display_context: DisplayContext,
    materializer: MaterializeSystemPromptFn | None,
    registry: object | None = None,
    pro_hooks: ProPipelineHooks | None = None,
) -> PipelineDeps:
    """Build PipelineDeps for the commit plumbing fallback path.

    Routes through :class:`DefaultPipelineFactory` so a Pro subclassed
    factory is honored on the plumbing-direct-call path.
    """
    deps = DefaultPipelineFactory().build(
        config, display_context, pro_hooks=pro_hooks
    )
    return _apply_commit_deps_overrides(
        deps, materializer=materializer, registry=registry
    )


def run_commit_plumbing(
    *,
    diff: str,
    repo_root: Path,
    chain_config: CommitChainConfig,
    display_context: DisplayContext | None = None,
    pipeline_core: PipelineCore | None = None,
    bridge_factory: BridgeFactory | None = None,
    pipeline_deps: PipelineDeps | None = None,
    pro_hooks: ProPipelineHooks | None = None,
) -> CommitAgentResult:
    """Iterate the commit chain, delegating each agent to the shared execution core.

    The chain iterates over each agent in ``chain_config.agents``; for
    each agent, the actual invocation is delegated to
    :func:`ralph.pipeline.effect_executor.execute_agent_effect` so the
    commit path shares the same bridge lifecycle, retry, and output
    handling as the main pipeline.

    Callers may supply either the modular ``pipeline_core`` + ``bridge_factory``
    surface or the legacy extended ``pipeline_deps`` bundle. When
    ``pipeline_deps`` is provided it is used for backward compatibility and
    its ``core`` and ``bridge_factory`` are derived automatically. When both
    are omitted, production defaults are used and the legacy late-bound
    bridge resolver is preserved so existing test monkeypatches continue to
    work.

    ``pro_hooks`` is forwarded to :class:`DefaultPipelineFactory` when the
    fallback path builds a fresh ``PipelineDeps``, so Pro factory subclassing
    is honored even for direct plumbing callers.

    No inline failure-classifier construction sites live in this
    module; recovery decisions are routed exclusively through the
    shared execution core.
    """
    if pipeline_deps is not None:
        if display_context is None:
            display_context = pipeline_deps.display_context
        effective_pipeline_deps = _apply_commit_deps_overrides(
            pipeline_deps,
            materializer=None,
            registry=chain_config.registry,
        )
        effective_core = effective_pipeline_deps.core
        effective_bridge_factory = effective_pipeline_deps.bridge_factory
    elif pipeline_core is not None:
        if display_context is None:
            display_context = pipeline_core.display_context
        effective_pipeline_deps = PipelineDeps(
            core=pipeline_core,
            bridge_factory=bridge_factory
            if bridge_factory is not None
            else build_session_bridge,
        )
        effective_pipeline_deps = _apply_commit_deps_overrides(
            effective_pipeline_deps,
            materializer=None,
            registry=chain_config.registry,
        )
        effective_core = effective_pipeline_deps.core
        effective_bridge_factory = effective_pipeline_deps.bridge_factory
    else:
        if display_context is None:
            raise ValueError(
                "display_context is required when pipeline_deps and pipeline_core are not provided"
            )
        effective_pipeline_deps = _commit_pipeline_deps(
            cast("UnifiedConfig", chain_config.general_config),
            display_context,
            materializer=None,
            registry=chain_config.registry,
            pro_hooks=pro_hooks,
        )
        display_context = effective_pipeline_deps.display_context
        effective_core = effective_pipeline_deps.core
        effective_bridge_factory = effective_pipeline_deps.bridge_factory

    template_dirs = (repo_root / ".agent" / "prompts" / "commit", *default_template_dirs(repo_root))
    template_registry = TemplateRegistry(template_dirs=template_dirs)
    extra_env: dict[str, str] | None = None
    failure_details: list[str] = []
    last_session_id: str | None = None
    last_error: Exception | None = None
    output_lines: list[str] = []

    materializer = effective_core.system_prompt_materializer
    with with_bridge_lifetime(
        effective_core,
        effective_bridge_factory,
        repo_root=repo_root,
        drain="commit",
        session_id_prefix="commit",
        agents_policy=chain_config.agents_policy,
    ) as bridge:
        extra_env = _commit_bridge_env(bridge)
        # Normalize the key set so downstream consumers (and tests)
        # observe string keys, never the ``McpEnvVar`` enum.
        if extra_env is not None:
            extra_env = _stringify_extra_env(extra_env)
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
                agent_name,
                cfg,
                prompt_file=prompt_file,
                attempt_context=attempt_ctx,
                display_context=display_context,
                prior_session_id=last_session_id,
                output_collector=output_lines,
                materializer=materializer,
                pipeline_deps=effective_pipeline_deps,
            )
            failure_details.extend(result.failure_details)
            last_session_id = result.session_id or last_session_id
            last_error = result.last_error or last_error

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


def _generate_commit_message_with_agent(
    agent_name: str,
    agent: AgentConfig,
    *,
    prompt_file: str,
    attempt_context: CommitAttemptContext,
    display_context: DisplayContext,
    prior_session_id: str | None = None,
    output_collector: list[str] | None = None,
    materializer: MaterializeSystemPromptFn | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> CommitAgentResult:
    failure_details: list[str] = []

    def _record_retry_failure(lines: list[str]) -> None:
        if not lines:
            return
        failure_details.append(
            "retryable failure recovered: " + summarize_retry_failure_evidence(lines)
        )

    raw_max_retries: object = None
    general_cfg = attempt_context.general_config
    if general_cfg is not None:
        if isinstance(general_cfg, GeneralConfig):
            raw_max_retries = getattr(general_cfg, "max_same_agent_retries", None)
        else:
            raw_max_retries = getattr(general_cfg.general, "max_same_agent_retries", None)
    max_retries = default_direct_mcp_retry_limit(raw_max_retries)
    (
        initial_attempt,
        last_session_id,
        last_error,
    ) = _run_commit_agent_attempt_with_recovery(
        agent_name,
        agent,
        prompt_file=prompt_file,
        attempt_context=attempt_context,
        display_context=display_context,
        max_retries=max_retries,
        prior_session_id=prior_session_id,
        on_retry_failure=_record_retry_failure,
        output_collector=output_collector,
        materializer=materializer,
        pipeline_deps=pipeline_deps,
    )
    if not initial_attempt.failure_detail:
        return _finalize_commit_attempt(
            initial_attempt, failure_details, last_session_id, last_error
        )
    failure_details.append(initial_attempt.failure_detail)

    latest_attempt = initial_attempt

    if _is_missing_commit_artifact_failure(latest_attempt.failure_detail):
        if initial_attempt.resume_session_id:
            (
                session_retry,
                last_session_id,
                last_error,
            ) = _run_commit_agent_attempt_with_recovery(
                agent_name,
                agent,
                prompt_file=prompt_file,
                attempt_context=attempt_context,
                display_context=display_context,
                max_retries=max_retries,
                prior_session_id=initial_attempt.resume_session_id,
                on_retry_failure=_record_retry_failure,
                output_collector=output_collector,
                materializer=materializer,
                pipeline_deps=pipeline_deps,
            )
            if not session_retry.failure_detail:
                return _finalize_commit_attempt(
                    session_retry, failure_details, last_session_id, last_error
                )
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
            (
                summary_retry,
                last_session_id,
                last_error,
            ) = _run_commit_agent_attempt_with_recovery(
                agent_name,
                agent,
                prompt_file=summary_prompt_file,
                attempt_context=attempt_context,
                display_context=display_context,
                max_retries=max_retries,
                prior_session_id=initial_attempt.resume_session_id,
                on_retry_failure=_record_retry_failure,
                output_collector=output_collector,
                materializer=materializer,
                pipeline_deps=pipeline_deps,
            )
            if not summary_retry.failure_detail:
                return _finalize_commit_attempt(
                    summary_retry, failure_details, last_session_id, last_error
                )
            failure_details.append(summary_retry.failure_detail)

    return CommitAgentResult(
        failure_details=failure_details,
        session_id=last_session_id,
        last_error=last_error,
    )


def _reset_tool_registry_callback(
    bridge: object | None,
) -> typing.Callable[[], object] | None:
    callback = reset_tool_registry_callback(bridge)
    if callback is None:
        return None
    return cast("typing.Callable[[], object]", callback)


def _run_commit_agent_attempt_with_recovery(
    agent_name: str,
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
    pipeline_deps: PipelineDeps | None = None,
) -> tuple[CommitAgentAttempt, str | None, Exception | None]:
    """Run a single commit-agent attempt through the shared execution core.

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

    def _capture_error(exc: Exception) -> None:
        nonlocal last_error
        last_error = exc

    raw_output: deque[str] = deque(maxlen=_MAX_COMMIT_RAW_OUTPUT_LINES)
    rendered_output: deque[str] = deque(maxlen=_MAX_COMMIT_PARSED_OUTPUT_LINES)

    delete_artifacts = _get_patched(
        _commit_module, "delete_commit_message_artifacts", delete_commit_message_artifacts
    )
    delete_artifacts(attempt_context.repo_root)
    # The commit run_id is fixed ("commit-plumbing") and reused across
    # attempts so the receipt ↔ gate key stays stable. A receipt left
    # over from a prior attempt would otherwise satisfy the gate's
    # "already submitted → done" check on a fresh attempt whose agent
    # never even ran, producing a false completion. Mirrors the AGY
    # branch's per-attempt clear in :mod:`ralph.agents.invoke` so a
    # retry that reuses ``run_id`` cannot inherit stale success state.
    clear_run_receipts(attempt_context.repo_root, _COMMIT_RUN_ID)

    try:
        effect = InvokeAgentEffect(
            agent_name=agent_name,
            phase="commit",
            prompt_file=prompt_file,
            drain="commit",
        )
        workspace_scope = WorkspaceScope(attempt_context.repo_root)
        general_cfg = attempt_context.general_config
        if general_cfg is None:
            effective_general_config: UnifiedConfig = UnifiedConfig(agents={agent.cmd: agent})
        elif isinstance(general_cfg, GeneralConfig):
            effective_general_config = UnifiedConfig(
                general=general_cfg,
                agents={agent.cmd: agent},
            )
        else:
            effective_general_config = general_cfg
        effective_pipeline_deps = pipeline_deps or _commit_pipeline_deps(
            effective_general_config,
            display_context,
            materializer,
            pro_hooks=None,
        )
        event = execute_agent_effect(
            effect,
            effective_general_config,
            effective_pipeline_deps,
            workspace_scope,
            bridge=cast("RestartAwareMcpBridge", attempt_context.bridge),
            display_context=display_context,
            run_id=_COMMIT_RUN_ID,
            session_id=prior_session_id,
            raw_output_sink=raw_output,
            rendered_output_sink=rendered_output,
            set_session_id_cb=_capture_session_id,
            invoke_agent=_get_patched(_commit_module, "invoke_agent", invoke_agent),
            on_retry_failure=on_retry_failure,
            agent_invocation_error_sink=_capture_error,
        )
        parsed_output, raw_lines, resume_session_id = collect_commit_agent_output(
            list(raw_output),
            parser_type=resolve_parser_key(
                agent.cmd, agent.json_parser, cast("AgentTransport", agent.transport)
            ),
            agent_name=agent.cmd.split()[0],
            verbose=attempt_context.verbose,
            display_context=display_context,
            session_id_sink=_capture_session_id,
        )
        if output_collector is not None:
            output_collector.extend(raw_lines)
        if event.value == "agent_success":
            read_artifact = _get_patched(
                _commit_module, "read_commit_message_artifact", read_commit_message_artifact
            )
            artifact_message = read_artifact(attempt_context.repo_root)
            if artifact_message:
                if _is_skip_response(artifact_message):
                    return (
                        CommitAgentAttempt(
                            skipped=True,
                            parsed_output=parsed_output,
                            raw_output=raw_lines,
                        ),
                        last_session_id,
                        last_error,
                    )
                return (
                    CommitAgentAttempt(
                        message=artifact_message,
                        parsed_output=parsed_output,
                        raw_output=raw_lines,
                    ),
                    last_session_id,
                    last_error,
                )
            return (
                CommitAgentAttempt(
                    failure_detail=_format_commit_agent_failure(
                        agent.cmd,
                        prompt_file,
                        parsed_output,
                        _MISSING_COMMIT_ARTIFACT_REASON,
                    ),
                    parsed_output=parsed_output,
                    raw_output=raw_lines,
                    resume_session_id=resume_session_id or last_session_id,
                ),
                last_session_id,
                last_error,
            )
        if isinstance(last_error, AgentInvocationError):
            failure_detail = _format_agent_invocation_failure(
                agent.cmd,
                prompt_file,
                last_error,
                parsed_output=parsed_output or _parsed_output_from_invocation_error(last_error),
            )
        else:
            failure_detail = _format_commit_agent_failure(
                agent.cmd,
                prompt_file,
                parsed_output,
                "agent invocation failed",
            )
        return (
            CommitAgentAttempt(
                failure_detail=failure_detail,
                parsed_output=parsed_output,
                raw_output=raw_lines,
                resume_session_id=resume_session_id or last_session_id,
            ),
            last_session_id,
            last_error,
        )
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
    """Run one commit-agent invocation attempt and return its result.

    .. deprecated::
        Kept as a thin late-binding wrapper for tests that patch
        ``ralph.cli.commands.commit.{materialize_system_prompt,invoke_agent,
        delete_commit_message_artifacts,read_commit_message_artifact}``.
        New code should call :func:`execute_agent_effect` through
        :func:`_run_commit_agent_attempt_with_recovery`.
    """
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
        general_cfg = attempt_context.general_config
        if isinstance(general_cfg, UnifiedConfig):
            general_cfg = general_cfg.general
        options = build_invoke_options_from_config(
            general_cfg,
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
            parser_type=resolve_parser_key(
                agent.cmd, agent.json_parser, cast("AgentTransport", agent.transport)
            ),
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
    session_id: str | None = None,
    last_error: Exception | None = None,
) -> CommitAgentResult:
    if attempt.skipped:
        return CommitAgentResult(
            skipped=True,
            failure_details=failure_details,
            session_id=session_id,
            last_error=last_error,
        )
    return CommitAgentResult(
        message=attempt.message,
        failure_details=failure_details,
        session_id=session_id,
        last_error=last_error,
    )


def _is_missing_commit_artifact_failure(detail: str) -> bool:
    # An empty / no-tool-call exit submitted nothing, so it is the same
    # "unsubmitted artifact" condition the pipeline recovers from — route it to
    # the shared detector instead of only matching the clean-exit string.
    return _MISSING_COMMIT_ARTIFACT_REASON in detail or is_unsubmitted_artifact_failure((detail,))


def _summarized_retry_prompt(base_prompt: str, parsed_output: list[str], agent: AgentConfig) -> str:
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


def _default_commit_bridge_factory(
    *,
    workspace_root: Path,
    drain: str,
    agents_policy: AgentsPolicy | None,
    transport: AgentTransport | None = None,
    capabilities: frozenset[str] | None = None,
    session_id_prefix: str | None = None,
    run_id: str | None = None,
    model_identity: object | None = None,
    parallel_worker: bool = False,
    worker_namespace: Path | None = None,
    worker_artifact_dir: Path | None = None,
    allowed_roots: tuple[Path, ...] | None = None,
    build_session_mcp_plan_fn: object | None = None,
    start_mcp_server_fn: object | None = None,
    workspace_factory: object | None = None,
) -> SessionBridgeLike:
    """Default commit bridge factory that honors the test-patch surface.

    Implements the :class:`ralph.pipeline.session_bridge.BridgeFactory`
    protocol while forwarding to the legacy
    ``ralph.cli.commands.commit.start_commit_bridge`` attribute. The
    late-bound lookup lets tests monkeypatch the commit module's bridge
    starter and have the patch propagate through the shared PipelineDeps
    path. The injected ``model_identity`` is forwarded when the patched
    starter accepts it, preserving the same model-context flow as the
    main pipeline.
    """
    del (
        drain,
        transport,
        capabilities,
        session_id_prefix,
        run_id,
        parallel_worker,
        worker_namespace,
        worker_artifact_dir,
        allowed_roots,
        build_session_mcp_plan_fn,
        start_mcp_server_fn,
        workspace_factory,
    )
    bridge_fn = _resolve_commit_start_commit_bridge()
    policy = agents_policy or AgentsPolicy()
    try:
        return bridge_fn(
            workspace_root,
            agents_policy=policy,
            model_identity=model_identity,
        )
    except TypeError:
        try:
            return bridge_fn(workspace_root, agents_policy=policy)
        except TypeError:
            return bridge_fn(workspace_root)


def _start_commit_bridge(
    repo_root: Path,
    *,
    agents_policy: AgentsPolicy,
    model_identity: MultimodalModelIdentity | None = None,
) -> SessionBridgeLike:
    return build_session_bridge(
        workspace_root=repo_root,
        drain="commit",
        agents_policy=agents_policy,
        session_id_prefix="commit",
        model_identity=model_identity,
        # BINDING: the session's run_id is the same value the completion
        # gate reads from MCP_RUN_ID_ENV (threaded via _commit_bridge_env
        # → bridge.run_id). Pre-fix this was None → uuid4() and the receipt
        # was stamped under a value the gate could never find.
        run_id=_COMMIT_RUN_ID,
    )


def _commit_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return bridge_env_for(bridge)


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
