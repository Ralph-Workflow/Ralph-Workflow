"""Run pipeline command for Ralph Workflow CLI.

This module implements the main pipeline execution command.
"""

from __future__ import annotations

import pathlib  # noqa: TC003
from inspect import signature
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

from loguru import logger
from rich.panel import Panel
from rich.text import Text

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity  # noqa: TC001
from ralph.config.loader import load_config
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState  # noqa: TC001
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
    validate_policy_completeness,
    validate_recovery_config,
)
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.policy.models import PolicyBundle

from ralph.display.context import make_display_context


class _RunnerFunc(Protocol):
    def __call__(
        self,
        config: UnifiedConfig,
        initial_state: PipelineState | None,
        **kwargs: object,
    ) -> int: ...


# Late import to avoid circular dependency
try:
    from ralph.pipeline.runner import run as _imported_run_func
except ImportError:
    _run_func: _RunnerFunc | None = None
else:
    _run_func = cast("_RunnerFunc", _imported_run_func)

ConfigOverrides = dict[str, object]


# Exit codes
_EXIT_SUCCESS = 0
_EXIT_CONFIG_ERROR = 1
_EXIT_INTERRUPT = 130
_EXIT_PREFLIGHT = 2
load_policy = _dir_load_policy


class _LoadResult(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    initial_state: PipelineState | None
    policy_bundle: PolicyBundle | None


def _load_configuration(
    config_path: pathlib.Path | None,
    cli_overrides: ConfigOverrides,
    resume: bool,
    *,
    display_context: DisplayContext,
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
            console.print(
                Text("No checkpoint found to resume from", style="theme.status.warning")
            )

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
        "planning → development → review → fix loop "
        "driven by your PROMPT.md.\n\n"
    )
    content.append("Next steps:\n", style="theme.banner.title")
    content.append("  1. Run ")
    content.append("ralph --init", style="theme.cat.meta")
    content.append(" to scaffold PROMPT.md and .agent/ configs\n")
    content.append("  2. Edit ")
    content.append("PROMPT.md", style="theme.cat.meta")
    content.append(" with your task\n")
    content.append("  3. Run ")
    content.append("ralph", style="theme.cat.meta")
    content.append(" to start the pipeline\n\n")
    content.append("Docs: ", style="theme.text.muted")
    content.append("docs/sphinx/getting-started.md", style="theme.text.muted")
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
    from ralph.policy.validation import validate_drain_contracts  # noqa: PLC0415

    validate_drain_contracts(policy_bundle)


def _run_policy_preflight_checks(
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    initial_state: PipelineState | None,
    counter_overrides: dict[str, int],
    *,
    display_context: DisplayContext,
) -> int:
    """Run policy-backed preflight checks against the already loaded bundle."""
    console = display_context.console
    try:
        agent_registry = AgentRegistry.from_config(config)
        validate_agent_chains_satisfiable(policy_bundle, agent_registry)
    except PolicyValidationError as e:
        console.print(_preflight_error_text(e.message), soft_wrap=True)
        return _EXIT_PREFLIGHT

    try:
        validate_recovery_config(policy_bundle)
    except PolicyValidationError as e:
        console.print(_preflight_error_text(e.message), soft_wrap=True)
        return _EXIT_PREFLIGHT

    if counter_overrides:
        try:
            validate_policy_completeness(policy_bundle, cli_counter_overrides=counter_overrides)
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    if initial_state is not None:
        try:
            validate_checkpoint_against_policy(initial_state, policy_bundle)
        except CheckpointPolicyMismatchError as e:
            console.print(_checkpoint_mismatch_text(str(e)), soft_wrap=True)
            return _EXIT_PREFLIGHT
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    return _EXIT_SUCCESS


def _run_preflight_checks(  # noqa: PLR0913
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope | None,
    policy_bundle: object,
    initial_state: PipelineState | None,
    counter_overrides: dict[str, int],
    *,
    display_context: DisplayContext,
) -> int:
    """Run all preflight validation checks.

    Returns:
        _EXIT_SUCCESS if all checks pass, _EXIT_PREFLIGHT if any check fails.
    """
    from ralph.policy.validation import validate_required_inputs  # noqa: PLC0415

    console = display_context.console
    # validate_required_inputs requires workspace_scope
    if workspace_scope is not None:
        # Fresh-state detection: workspace has neither PROMPT.md nor .agent
        prompt_path = workspace_scope.root / "PROMPT.md"
        agent_dir = workspace_scope.root / ".agent"
        if not prompt_path.exists() and not agent_dir.exists():
            _print_not_initialized_panel(display_context=display_context)
            return _EXIT_PREFLIGHT

        try:
            validate_required_inputs(workspace_scope)
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT

    # Only run policy-based validations if we have a loaded policy bundle.
    if policy_bundle is not None:
        loaded_policy_bundle = cast("PolicyBundle", policy_bundle)
        try:
            _validate_loaded_policy_bundle(loaded_policy_bundle)
        except PolicyValidationError as e:
            console.print(_preflight_error_text(e.message), soft_wrap=True)
            return _EXIT_PREFLIGHT
        return _run_policy_preflight_checks(
            config,
            loaded_policy_bundle,
            initial_state,
            counter_overrides,
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
    fallback_phase = (
        policy_bundle.pipeline.entry_phase if policy_bundle is not None else "unknown"
    )
    phase = initial_state.phase if initial_state else fallback_phase
    console.print(_detail_text("Phase", phase))
    console.print(_detail_text("Iterations", str(config.general.developer_iters)))
    console.print(_detail_text("Review passes", str(config.general.reviewer_reviews)))


def _execute_pipeline(  # noqa: PLR0913
    config: UnifiedConfig,
    initial_state: PipelineState | None,
    policy_bundle: object,
    verbosity: Verbosity | None,
    counter_overrides: dict[str, int],
    *,
    display_context: DisplayContext,
) -> int:
    """Execute the pipeline.

    Returns:
        Exit code from pipeline runner.
    """
    console = display_context.console
    if _run_func is None:
        logger.error("Pipeline runner is unavailable")
        console.print(Text("Pipeline runner is unavailable", style="theme.status.error"))
        return _EXIT_CONFIG_ERROR

    try:
        kwargs: dict[str, object] = {}
        runner_params = signature(_run_func).parameters
        if verbosity is not None and "verbosity" in runner_params:
            kwargs["verbosity"] = verbosity
        if policy_bundle is not None and "policy_bundle" in runner_params:
            kwargs["policy_bundle"] = policy_bundle
        if "display_context" in runner_params:
            kwargs["display_context"] = display_context
        if counter_overrides and "counter_overrides" in runner_params:
            kwargs["counter_overrides"] = counter_overrides
        return _run_func(config, initial_state, **kwargs)
    except KeyboardInterrupt:
        console.print(Text("\nInterrupted by user", style="theme.status.warning"))
        if initial_state is not None:
            _save_interrupt_checkpoint(initial_state)
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
def run_pipeline(  # noqa: PLR0913
    config_path: pathlib.Path | None = None,
    cli_overrides: ConfigOverrides | None = None,
    dry_run: bool = False,
    resume: bool = False,
    verbosity: Verbosity | None = None,
    *,
    display_context: DisplayContext | None = None,
    counter_overrides: dict[str, int] | None = None,
) -> int:
    """Run the Ralph Workflow pipeline (backward compatibility wrapper).

    Args:
        config_path: Path to configuration file.
        cli_overrides: CLI flag overrides for config.
        dry_run: If True, run without invoking agents.
        resume: If True, resume from checkpoint.
        verbosity: Optional explicit verbosity passed through to the runner.
        display_context: Display context for consistent rendering. If None, a default
            context is created using make_display_context().
        counter_overrides: Optional budget counter overrides from --counter flags.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    ctx = display_context if display_context is not None else make_display_context()
    effective_counter_overrides = counter_overrides or {}
    # Phase 1: Load configuration
    load_result = _load_configuration(
        config_path, cli_overrides or {}, resume, display_context=ctx
    )
    if isinstance(load_result, int):
        return load_result

    # Phase 2: Preflight validation (before any pipeline activity)
    preflight_result = _run_preflight_checks(
        load_result.config,
        load_result.workspace_scope,
        load_result.policy_bundle,
        load_result.initial_state,
        effective_counter_overrides,
        display_context=ctx,
    )
    if preflight_result != _EXIT_SUCCESS:
        return preflight_result

    # Phase 3: Handle dry-run
    if dry_run:
        _print_dry_run(
            load_result.initial_state,
            load_result.config,
            load_result.policy_bundle,
            display_context=ctx,
        )
        return _EXIT_SUCCESS

    # Phase 4: Execute pipeline
    return _execute_pipeline(
        load_result.config,
        load_result.initial_state,
        load_result.policy_bundle,
        verbosity,
        effective_counter_overrides,
        display_context=ctx,
    )
