"""Run pipeline command for Ralph Workflow CLI.

This module implements the main pipeline execution command.
"""

from __future__ import annotations

import shutil
from importlib import import_module
from inspect import signature
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypedDict, cast

from loguru import logger
from rich.panel import Panel
from rich.text import Text

from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.onboarding import GETTING_STARTED_DOC, fresh_workspace_next_steps
from ralph.pipeline import checkpoint as ckpt
from ralph.policy.loader import (
    load_policy as _dir_load_policy,
)
from ralph.policy.loader import (
    load_policy_for_workspace_scope,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
    validate_agent_chains_satisfiable,
    validate_checkpoint_against_policy,
    validate_drain_contracts,
    validate_policy_completeness,
    validate_recovery_config,
    validate_required_inputs,
)
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle

from ralph.display.context import make_display_context


class _RunnerFunc(Protocol):
    def __call__(
        self,
        config: UnifiedConfig,
        initial_state: PipelineState | None,
        **kwargs: object,
    ) -> int: ...


class _RunnerModule(Protocol):
    """Typed accessor for the lazily imported pipeline runner module."""

    run: _RunnerFunc


_RUN_FUNC_UNSET: object = object()
_run_func: _RunnerFunc | None | object = _RUN_FUNC_UNSET


def _get_run_func() -> _RunnerFunc | None:
    """Return the pipeline runner callable, importing it lazily on first call.

    The module-level ``_run_func`` is exposed so tests can inject a fake runner
    via ``monkeypatch.setattr``. A sentinel distinguishes "not yet loaded" from
    the genuine ``None`` produced by an ImportError, ensuring repeated calls do
    not retry the import after a failure.
    """
    global _run_func  # noqa: PLW0603

    if _run_func is not _RUN_FUNC_UNSET:
        return cast("_RunnerFunc | None", _run_func)

    try:
        module = cast("_RunnerModule", import_module("ralph.pipeline.runner"))
    except ImportError:
        _run_func = None
        return None

    _run_func = module.run
    return module.run


ConfigOverrides = dict[str, object]


# Exit codes
_EXIT_SUCCESS = 0
_EXIT_CONFIG_ERROR = 1
_EXIT_INTERRUPT = 130
_EXIT_PREFLIGHT = 2
load_policy = _dir_load_policy

_GENERATED_AGENT_STATE_DIRS: tuple[str, ...] = (
    "artifacts",
    "tmp",
    "prompt_history",
    "workers",
)

_GENERATED_AGENT_STATE_FILES: tuple[str, ...] = (
    "CURRENT_PROMPT.md",
    "PLAN.md",
    "ISSUES.md",
    "DEVELOPMENT_RESULT.md",
    "FIX_RESULT.md",
    "DEVELOPMENT_ANALYSIS_DECISION.md",
    "REVIEW_ANALYSIS_DECISION.md",
    "checkpoint.json",
    "rebase_checkpoint.json",
    "rebase_checkpoint.json.bak",
    "rebase.lock",
    "start_commit",
)


class _LoadResult(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    initial_state: PipelineState | None
    policy_bundle: PolicyBundle | None


class _PolicyPreflightRequest(NamedTuple):
    config: UnifiedConfig
    policy_bundle: PolicyBundle
    initial_state: PipelineState | None
    counter_overrides: dict[str, int]


class _PreflightRequest(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    policy_bundle: object
    initial_state: PipelineState | None
    counter_overrides: dict[str, int]
    inline_prompt: str | None = None


class _ExecutePipelineRequest(NamedTuple):
    config: UnifiedConfig
    initial_state: PipelineState | None
    policy_bundle: object
    verbosity: Verbosity | None
    counter_overrides: dict[str, int]


class _LegacyRunPipelineKwargs(TypedDict, total=False):
    config_path: Path
    cli_overrides: ConfigOverrides
    dry_run: bool
    resume: bool
    verbosity: Verbosity
    counter_overrides: dict[str, int]
    inline_prompt: str


class RunPipelineRequest(NamedTuple):
    config_path: Path | None = None
    cli_overrides: ConfigOverrides | None = None
    dry_run: bool = False
    resume: bool = False
    verbosity: Verbosity | None = None
    counter_overrides: dict[str, int] | None = None
    inline_prompt: str | None = None


def _prompt_changed_since_last_materialization(workspace_root: Path) -> bool:
    prompt_path = workspace_root / "PROMPT.md"
    current_prompt_path = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    if not prompt_path.exists() or not current_prompt_path.exists():
        return False
    try:
        return prompt_path.read_text(encoding="utf-8") != current_prompt_path.read_text(
            encoding="utf-8"
        )
    except OSError:
        return False


def _clear_generated_pipeline_state(workspace_root: Path) -> None:
    agent_dir = workspace_root / ".agent"
    for relative_dir in _GENERATED_AGENT_STATE_DIRS:
        shutil.rmtree(agent_dir / relative_dir, ignore_errors=True)
    for relative_file in _GENERATED_AGENT_STATE_FILES:
        (agent_dir / relative_file).unlink(missing_ok=True)


def _invalidate_pipeline_state_if_prompt_changed(workspace_root: Path) -> bool:
    if not _prompt_changed_since_last_materialization(workspace_root):
        return False
    _clear_generated_pipeline_state(workspace_root)
    return True


def _load_configuration(
    config_path: Path | None,
    cli_overrides: ConfigOverrides,
    resume: bool,
    *,
    display_context: DisplayContext,
    inline_prompt: str | None = None,
) -> _LoadResult | int:
    """Load configuration and resolve workspace scope.

    Returns:
        _LoadResult on success, or int error code on failure.
    """
    console = display_context.console
    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        logger.error("Failed to load configuration: {}", e)
        return _EXIT_CONFIG_ERROR

    initial_state: PipelineState | None = None
    policy_bundle: PolicyBundle | None = None

    if (
        workspace_scope is not None
        and inline_prompt is None
        and _invalidate_pipeline_state_if_prompt_changed(workspace_scope.root)
    ):
        console.print(
            Text(
                "PROMPT.md changed since the last materialized run context; "
                "cleared saved pipeline state and caches.",
                style="theme.status.warning",
            )
        )

    if workspace_scope is not None:
        try:
            if load_policy is not _dir_load_policy:
                policy_dir = workspace_scope.resolve_agent_file("pipeline.toml").parent
                policy_bundle = load_policy(policy_dir, config=config)
            else:
                policy_bundle = load_policy_for_workspace_scope(workspace_scope, config=config)
        except Exception as e:
            logger.warning("Failed to load policy bundle: {}", e)
            err_text = Text()
            err_text.append("Preflight error:", style="theme.status.error")
            err_text.append(f" {e}")
            console.print(err_text, soft_wrap=True)
            return _EXIT_PREFLIGHT

    if resume:
        initial_state = ckpt.load()
        if initial_state is None:
            console.print(Text("No checkpoint found to resume from", style="theme.status.warning"))

    return _LoadResult(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=initial_state,
        policy_bundle=policy_bundle,
    )


def _print_not_initialized_panel(*, display_context: DisplayContext) -> None:
    """Print a friendly 'not initialized' panel for completely fresh workspaces."""
    console = display_context.console
    content = Text()
    content.append(
        "Ralph Workflow orchestrates AI coding agents through a "
        "planning → development loop "
        "driven by your PROMPT.md.\n\n"
    )
    content.append("Next steps:\n", style="theme.banner.title")
    for index, line in enumerate(fresh_workspace_next_steps(), start=1):
        content.append(f"  {index}. {line}\n")
    content.append("\nDocs: ", style="theme.text.muted")
    content.append(GETTING_STARTED_DOC, style="theme.text.muted")
    content.append(" — step-by-step walkthrough for new users", style="theme.text.muted")
    panel = Panel(
        content,
        title="Ralph Workflow is not initialized here yet",
        border_style="theme.status.warning",
        padding=(1, 2),
    )
    console.print(panel)


def _validate_loaded_policy_bundle(policy_bundle: PolicyBundle) -> None:
    """Validate cross-drain policy contracts for an already loaded bundle."""
    validate_drain_contracts(policy_bundle)


def _run_policy_preflight_checks(
    request: _PolicyPreflightRequest,
    *,
    display_context: DisplayContext,
) -> int:
    """Run policy-backed preflight checks against the already loaded bundle."""
    console = display_context.console
    try:
        agent_registry = AgentRegistry.from_config(request.config)
        validate_agent_chains_satisfiable(request.policy_bundle, agent_registry)
    except PolicyValidationError as e:
        console.print(_preflight_error_text(e.message), soft_wrap=True)
        return _EXIT_PREFLIGHT

    try:
        validate_recovery_config(request.policy_bundle)
    except PolicyValidationError as e:
        console.print(_preflight_error_text(e.message), soft_wrap=True)
        return _EXIT_PREFLIGHT

    if request.counter_overrides:
        try:
            validate_policy_completeness(
                request.policy_bundle,
                cli_counter_overrides=request.counter_overrides,
            )
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    if request.initial_state is not None:
        try:
            validate_checkpoint_against_policy(request.initial_state, request.policy_bundle)
        except CheckpointPolicyMismatchError as e:
            console.print(_checkpoint_mismatch_text(str(e)), soft_wrap=True)
            return _EXIT_PREFLIGHT
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    return _EXIT_SUCCESS


def _run_preflight_checks(
    request: _PreflightRequest,
    *,
    display_context: DisplayContext,
) -> int:
    """Run all preflight validation checks.

    Returns:
        _EXIT_SUCCESS if all checks pass, _EXIT_PREFLIGHT if any check fails.
    """
    console = display_context.console
    # validate_required_inputs requires workspace_scope
    if request.workspace_scope is not None and request.inline_prompt is None:
        # Fresh-state detection: workspace has neither PROMPT.md nor .agent
        prompt_path = request.workspace_scope.root / "PROMPT.md"
        agent_dir = request.workspace_scope.root / ".agent"
        if not prompt_path.exists() and not agent_dir.exists():
            _print_not_initialized_panel(display_context=display_context)
            return _EXIT_PREFLIGHT

        try:
            validate_required_inputs(request.workspace_scope)
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    # Only run policy-based validations if we have a loaded policy bundle.
    if request.policy_bundle is not None:
        loaded_policy_bundle = cast("PolicyBundle", request.policy_bundle)
        try:
            _validate_loaded_policy_bundle(loaded_policy_bundle)
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT
        return _run_policy_preflight_checks(
            _PolicyPreflightRequest(
                config=request.config,
                policy_bundle=loaded_policy_bundle,
                initial_state=request.initial_state,
                counter_overrides=request.counter_overrides,
            ),
            display_context=display_context,
        )

    return _EXIT_SUCCESS


def _print_dry_run(
    initial_state: PipelineState | None,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle | None,
    *,
    display_context: DisplayContext,
) -> None:
    """Print dry-run information."""
    console = display_context.console
    console.print(Text("Dry run mode", style="theme.cat.meta"))
    fallback_phase = policy_bundle.pipeline.entry_phase if policy_bundle is not None else "unknown"
    phase = initial_state.phase if initial_state else fallback_phase
    console.print(_detail_text("Phase", phase))
    console.print(_detail_text("Iterations", str(config.general.developer_iters)))


def _execute_pipeline(
    request: _ExecutePipelineRequest,
    *,
    display_context: DisplayContext,
) -> int:
    """Execute the pipeline.

    Returns:
        Exit code from pipeline runner.
    """
    console = display_context.console
    run_func = _get_run_func()
    if run_func is None:
        logger.error("Pipeline runner is unavailable")
        console.print(Text("Pipeline runner is unavailable", style="theme.status.error"))
        return _EXIT_CONFIG_ERROR

    try:
        kwargs: dict[str, object] = {}
        runner_params = signature(run_func).parameters
        if request.verbosity is not None and "verbosity" in runner_params:
            kwargs["verbosity"] = request.verbosity
        if request.policy_bundle is not None and "policy_bundle" in runner_params:
            kwargs["policy_bundle"] = request.policy_bundle
        if "display_context" in runner_params:
            kwargs["display_context"] = display_context
        if request.counter_overrides and "counter_overrides" in runner_params:
            kwargs["counter_overrides"] = request.counter_overrides
        return run_func(request.config, request.initial_state, **kwargs)
    except KeyboardInterrupt:
        console.print(Text("\nInterrupted by user", style="theme.status.warning"))
        if request.initial_state is not None:
            _save_interrupt_checkpoint(request.initial_state)
        return _EXIT_INTERRUPT
    except CheckpointPolicyMismatchError as e:
        console.print(_checkpoint_mismatch_text(str(e)))
        return _EXIT_PREFLIGHT
    except PolicyValidationError as e:
        console.print(_pipeline_config_error_text(e.message))
        return _EXIT_PREFLIGHT
    except Exception as e:
        logger.exception("Pipeline execution failed: {}")
        console.print(_status_text("Pipeline failed", str(e), "theme.status.error"))
        return _EXIT_CONFIG_ERROR


def _save_interrupt_checkpoint(initial_state: PipelineState) -> None:
    """Save checkpoint on interrupt."""
    try:
        update_data: ConfigOverrides = {"interrupted_by_user": True}
        interrupted_state = initial_state.model_copy(update=update_data)
        ckpt.save(interrupted_state)
    except Exception:
        logger.warning("Checkpoint save failed during interrupt", exc_info=True)


def _preflight_error_text(message: str) -> Text:
    text = Text()
    text.append("Preflight error:", style="theme.status.error")
    text.append(f" {message}")
    return text


def _checkpoint_mismatch_text(message: str) -> Text:
    text = Text()
    text.append("Checkpoint mismatch:", style="theme.status.error")
    text.append(f" {message}")
    return text


def _pipeline_config_error_text(message: str) -> Text:
    text = Text()
    text.append("Pipeline configuration error:", style="theme.status.error")
    text.append(f" {message}")
    return text


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _detail_text(label: str, detail: str) -> Text:
    text = Text()
    text.append(f"  {label}: ")
    text.append(detail)
    return text


# Backward compatibility: expose run_pipeline for direct invocation
def run_pipeline(
    request: RunPipelineRequest | None = None,
    *,
    display_context: DisplayContext | None = None,
    **kwargs: _LegacyRunPipelineKwargs,
) -> int:
    """Run the Ralph Workflow pipeline (backward compatibility wrapper).

    Args:
        request: RunPipelineRequest namedtuple with all pipeline options.
        display_context: Display context for consistent rendering. If None, a default
            context is created using make_display_context().
        **kwargs: Additional keyword arguments for backward compatibility.
            Accepted keys: config_path, cli_overrides, dry_run, resume, verbosity,
            counter_overrides, inline_prompt.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    ctx = display_context if display_context is not None else make_display_context()
    if request is None:
        request = RunPipelineRequest(
            config_path=cast("Path | None", kwargs.get("config_path")),
            cli_overrides=cast("ConfigOverrides | None", kwargs.get("cli_overrides")),
            dry_run=cast("bool", kwargs.get("dry_run", False)),
            resume=cast("bool", kwargs.get("resume", False)),
            verbosity=cast("Verbosity | None", kwargs.get("verbosity")),
            counter_overrides=cast("dict[str, int] | None", kwargs.get("counter_overrides")),
            inline_prompt=cast("str | None", kwargs.get("inline_prompt")),
        )
    effective_request = request
    effective_counter_overrides = effective_request.counter_overrides or {}

    if effective_request.inline_prompt is not None:
        workspace_scope = resolve_workspace_scope()
        current_prompt_path = workspace_scope.root / ".agent" / "CURRENT_PROMPT.md"
        current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
        current_prompt_path.write_text(effective_request.inline_prompt, encoding="utf-8")

    # Phase 1: Load configuration
    load_result = _load_configuration(
        effective_request.config_path,
        effective_request.cli_overrides or {},
        effective_request.resume,
        display_context=ctx,
        inline_prompt=effective_request.inline_prompt,
    )
    if isinstance(load_result, int):
        return load_result

    # Phase 2: Preflight validation (before any pipeline activity)
    preflight_result = _run_preflight_checks(
        _PreflightRequest(
            config=load_result.config,
            workspace_scope=load_result.workspace_scope,
            policy_bundle=load_result.policy_bundle,
            initial_state=load_result.initial_state,
            counter_overrides=effective_counter_overrides,
            inline_prompt=effective_request.inline_prompt,
        ),
        display_context=ctx,
    )
    if preflight_result != _EXIT_SUCCESS:
        return preflight_result

    # Phase 3: Handle dry-run
    if effective_request.dry_run:
        _print_dry_run(
            load_result.initial_state,
            load_result.config,
            load_result.policy_bundle,
            display_context=ctx,
        )
        return _EXIT_SUCCESS

    # Phase 4: Execute pipeline
    return _execute_pipeline(
        _ExecutePipelineRequest(
            config=load_result.config,
            initial_state=load_result.initial_state,
            policy_bundle=load_result.policy_bundle,
            verbosity=effective_request.verbosity,
            counter_overrides=effective_counter_overrides,
        ),
        display_context=ctx,
    )
