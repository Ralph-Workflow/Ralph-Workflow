
from __future__ import annotations

import os
import secrets
import shutil
import uuid
from contextlib import ExitStack, suppress
from importlib import import_module
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, Unpack, cast

from loguru import logger
from rich.text import Text

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands._execute_pipeline_request import _ExecutePipelineRequest
from ralph.cli.commands._load_result import _LoadResult
from ralph.cli.commands._policy_preflight_request import _PolicyPreflightRequest
from ralph.cli.commands._preflight_request import _PreflightRequest
from ralph.cli.commands._run_func_state import _RUN_FUNC_UNSET, _RunFuncState
from ralph.cli.commands._run_start_setup import (
    SetupDependencies,
    run_start_retention_sweep,
    sync_shipped_skills,
)
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, resolve_active_display
from ralph.mcp.protocol.env import RALPH_PARALLEL_WORKER_MANIFEST_ENV
from ralph.mcp.server._process_secrecy import protect_broker_secret
from ralph.onboarding import GETTING_STARTED_DOC, fresh_workspace_next_steps
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.parallel.worker_runtime import run_parallel_worker_from_manifest
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
    validate_chain_agents_on_path,
    validate_checkpoint_against_policy,
    validate_drain_contracts,
    validate_policy_completeness,
    validate_recovery_config,
    validate_required_inputs,
)
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.project_policy.policy_mode import PolicyMode
from ralph.skills._installer import (
    _project_skills_need_install,
    install_project_baseline_skills,
)
from ralph.skills._process_view import SkillsProcessView, has_machine_global_skills
from ralph.skills._state_store import default_state_path
from ralph.skills.manager import SkillManager
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.cli.commands._legacy_run_pipeline_kwargs import _LegacyRunPipelineKwargs
    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import ProPipelineHooks
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.workspace.protocol import Workspace

if TYPE_CHECKING:

    class _RunnerFunc(Protocol):
        def __call__(
            self,
            config: UnifiedConfig,
            initial_state: PipelineState | None,
            **kwargs: object,
        ) -> int: ...

    class _RunnerModule(Protocol):

        run: _RunnerFunc


_state = _RunFuncState()


def _get_run_func() -> _RunnerFunc | None:
    if _state.run_func is not _RUN_FUNC_UNSET:
        return cast(
            "_RunnerFunc | None", _state.run_func
        )

    try:
        module = cast(
            "_RunnerModule", import_module("ralph.pipeline.runner")
        )
    except ImportError:
        _state.run_func = None
        return None

    _state.run_func = module.run
    return module.run


ConfigOverrides = dict[str, object]


_EXIT_SUCCESS = 0
_EXIT_CONFIG_ERROR = 1
_EXIT_INTERRUPT = 130
_EXIT_PREFLIGHT = 2
load_policy = _dir_load_policy

_BROKER_SECRET_ENV = "RALPH_BROKER_SECRET"


def _ensure_broker_secret() -> None:
    os.environ[_BROKER_SECRET_ENV] = secrets.token_hex(32)


_GENERATED_AGENT_STATE_DIRS: tuple[str, ...] = (
    "artifacts",
    "tmp",
    "prompt_history",
    "workers",
)


def _validate_custom_mcp_servers(workspace_root: Path) -> int:
    module = import_module("ralph.pipeline.runner")
    return cast(
        "int", module.validate_custom_mcp_servers(workspace_root)
    )


validate_custom_mcp_servers = _validate_custom_mcp_servers


_GENERATED_AGENT_STATE_FILES: tuple[str, ...] = (
    "PRODUCT_CRITERIA.md",
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


class RunPipelineRequest(NamedTuple):

    config_path: Path | None = None
    cli_overrides: ConfigOverrides | None = None
    dry_run: bool = False
    resume: bool = False
    verbosity: Verbosity | None = None
    counter_overrides: dict[str, int] | None = None
    parallel_worker_manifest: Path | None = None
    pro_hooks: ProPipelineHooks | None = None
    model_identity: MultimodalModelIdentity | None = None
    policy_mode: PolicyMode = PolicyMode.NORMAL


def _prompt_changed_since_last_materialization(workspace_root: Path) -> bool:
    prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    product_criteria_path = workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    if not prompt_path.exists() or not product_criteria_path.exists():
        return False
    try:
        return prompt_path.read_text(encoding="utf-8") != product_criteria_path.read_text(
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


def _record_config_telemetry(config: UnifiedConfig, workspace_root: Path | None) -> None:
    try:
        from ralph.project_policy.schema_state import policy_schema_state
        from ralph.telemetry._sentry import (
            is_telemetry_active,
            set_agent_config_context,
            set_policy_schema_context,
        )

        if not is_telemetry_active():
            return
        set_agent_config_context(config.agents)
        if workspace_root is not None:
            set_policy_schema_context(policy_schema_state(workspace_root))
    except Exception:
        return


def _load_configuration(
    config_path: Path | None,
    cli_overrides: ConfigOverrides,
    resume: bool,
    *,
    display_context: DisplayContext,
) -> _LoadResult | int:
    display = resolve_active_display(None, display_context)
    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(
            config_path,
            cli_overrides,
            workspace_scope=workspace_scope,
            unknown_field_warning=display.emit_warning,
        )
    except Exception as e:
        logger.error("Failed to load configuration: {}", e)
        return _EXIT_CONFIG_ERROR

    _record_config_telemetry(
        config,
        workspace_scope.root if workspace_scope is not None else None,
    )
    initial_state: PipelineState | None = None
    policy_bundle: PolicyBundle | None = None

    if workspace_scope is not None and _invalidate_pipeline_state_if_prompt_changed(
        workspace_scope.root
    ):
        display.emit_warning(
            "PROMPT.md changed since the last materialized run context; "
            "cleared saved pipeline state and caches."
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
            display.emit_warning(f"Preflight error: {e}")
            return _EXIT_PREFLIGHT

    if resume:
        initial_state = ckpt.load()
        if initial_state is None:
            display.emit_warning("No checkpoint found to resume from")

    canonical_run_id = uuid.uuid4().hex

    return _LoadResult(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=initial_state,
        policy_bundle=policy_bundle,
        run_id=canonical_run_id,
    )


def _print_not_initialized_panel(*, display_context: DisplayContext) -> None:
    display = resolve_active_display(None, display_context)
    content_lines: list[str] = [
        "Ralph Workflow orchestrates AI coding agents through a "
        "planning → development loop driven by your PROMPT.md.",
        "",
    ]
    for index, line in enumerate(fresh_workspace_next_steps(), start=1):
        content_lines.append(f"  {index}. {line}")
    content_lines.append("")
    content_lines.append(f"Docs: {GETTING_STARTED_DOC} — step-by-step walkthrough for new users")
    display.emit_info_panel(
        title="Ralph Workflow is not initialized here yet",
        content="\n".join(content_lines),
    )


def _validate_loaded_policy_bundle(policy_bundle: PolicyBundle) -> None:
    validate_drain_contracts(policy_bundle)


def _warn_missing_chain_fallback(message: str) -> None:
    logger.warning(message)


def _run_policy_preflight_checks(
    request: _PolicyPreflightRequest,
    *,
    display_context: DisplayContext,
) -> int:
    display = resolve_active_display(None, display_context)
    try:
        agent_registry = AgentRegistry.from_config(request.config)
        validate_agent_chains_satisfiable(request.policy_bundle, agent_registry)
        validate_chain_agents_on_path(
            request.policy_bundle.agents, warn=_warn_missing_chain_fallback
        )
    except PolicyValidationError as e:
        display.emit_warning(_preflight_error_text(e.message).plain)
        return _EXIT_PREFLIGHT

    try:
        validate_recovery_config(request.policy_bundle)
    except PolicyValidationError as e:
        display.emit_warning(_preflight_error_text(e.message).plain)
        return _EXIT_PREFLIGHT

    if request.counter_overrides:
        try:
            validate_policy_completeness(
                request.policy_bundle,
                cli_counter_overrides=request.counter_overrides,
            )
        except PolicyValidationError as e:
            display.emit_warning(_preflight_error_text(e.message).plain)
            return _EXIT_PREFLIGHT

    if request.initial_state is not None:
        try:
            validate_checkpoint_against_policy(request.initial_state, request.policy_bundle)
        except CheckpointPolicyMismatchError as e:
            display.emit_warning(_checkpoint_mismatch_text(str(e)).plain)
            return _EXIT_PREFLIGHT
        except PolicyValidationError as e:
            display.emit_warning(_preflight_error_text(e.message).plain)
            return _EXIT_PREFLIGHT

    return _EXIT_SUCCESS


def _run_preflight_checks(
    request: _PreflightRequest,
    *,
    display_context: DisplayContext,
) -> int:
    display = resolve_active_display(None, display_context)
    if request.workspace_scope is not None:
        prompt_path = resolve_effective_prompt_path(request.workspace_scope.root, os.environ)
        agent_dir = request.workspace_scope.root / ".agent"
        if not prompt_path.exists() and not agent_dir.exists():
            _print_not_initialized_panel(display_context=display_context)
            return _EXIT_PREFLIGHT

        try:
            validate_required_inputs(request.workspace_scope)
        except PolicyValidationError as e:
            display.emit_warning(_preflight_error_text(e.message).plain)
            return _EXIT_PREFLIGHT

        if validate_custom_mcp_servers(request.workspace_scope.root) != _EXIT_SUCCESS:
            display.emit_warning(
                _preflight_error_text("Custom MCP validation failed — see logs").plain
            )
            return _EXIT_PREFLIGHT

    if request.policy_bundle is not None:
        loaded_policy_bundle = cast(
            "PolicyBundle", request.policy_bundle
        )
        try:
            validate_loaded_policy_bundle(loaded_policy_bundle)
        except PolicyValidationError as e:
            display.emit_warning(_preflight_error_text(e.message).plain)
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


def print_dry_run(
    initial_state: PipelineState | None,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle | None,
    *,
    display_context: DisplayContext,
) -> None:
    display = resolve_active_display(None, display_context)
    fallback_phase = policy_bundle.pipeline.entry_phase if policy_bundle is not None else "unknown"
    phase = initial_state.phase if initial_state else fallback_phase
    display.emit_dry_run_summary(
        phase=phase,
        iterations=config.general.developer_iters,
    )


def _build_runner_kwargs(
    request: _ExecutePipelineRequest,
    *,
    display_context: DisplayContext,
    display: ParallelDisplay | None,
    display_is_active: bool,
    run_func: _RunnerFunc,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    runner_params = signature(run_func).parameters
    if request.verbosity is not None and "verbosity" in runner_params:
        kwargs["verbosity"] = request.verbosity
    if request.policy_bundle is not None and "policy_bundle" in runner_params:
        kwargs["policy_bundle"] = request.policy_bundle
    if "display_context" in runner_params:
        kwargs["display_context"] = display_context
    if display is not None and "display" in runner_params:
        kwargs["display"] = display
    if "display_is_active" in runner_params:
        kwargs["display_is_active"] = display_is_active
    if request.counter_overrides and "counter_overrides" in runner_params:
        kwargs["counter_overrides"] = request.counter_overrides
    if request.config_path is not None and "config_path" in runner_params:
        kwargs["config_path"] = request.config_path
    if request.cli_overrides is not None and "cli_overrides" in runner_params:
        kwargs["cli_overrides"] = request.cli_overrides
    if "pipeline_deps" in runner_params:
        kwargs["pipeline_deps"] = DefaultPipelineFactory().build(
            request.config,
            display_context,
            model_identity=request.model_identity,
            policy_bundle=request.policy_bundle,
            pro_hooks=request.pro_hooks,
        )
    if "pro_hooks" in runner_params:
        kwargs["pro_hooks"] = request.pro_hooks
    return kwargs


def _execute_pipeline(
    request: _ExecutePipelineRequest,
    *,
    display_context: DisplayContext,
    display: ParallelDisplay | None = None,
    display_is_active: bool = False,
) -> int:
    active_display = resolve_active_display(display, display_context)
    run_func = _get_run_func()
    if run_func is None:
        logger.error("Pipeline runner is unavailable")
        active_display.emit_warning("Pipeline runner is unavailable")
        return _EXIT_CONFIG_ERROR

    from ralph.mcp.explore.dirty_paths import _dirty_scheduler

    try:
        kwargs = _build_runner_kwargs(
            request,
            display_context=display_context,
            display=active_display,
            display_is_active=display_is_active,
            run_func=run_func,
        )
        result = run_func(request.config, request.initial_state, **kwargs)
        _dirty_scheduler.on_workflow_complete()
        return result
    except KeyboardInterrupt:
        active_display.emit_warning("\nInterrupted by user")
        _dirty_scheduler.on_workflow_cancel()
        try:
            from ralph.interrupt import handle_keyboard_interrupt_at_cli

            handle_keyboard_interrupt_at_cli(exit_code=_EXIT_INTERRUPT)
        except Exception:
            logger.warning("Interrupt dispatcher failed during CLI catch", exc_info=True)
        if request.initial_state is not None:
            _save_interrupt_checkpoint(request.initial_state)
        return _EXIT_INTERRUPT
    except CheckpointPolicyMismatchError as e:
        _dirty_scheduler.on_workflow_restart()
        active_display.emit_warning(_checkpoint_mismatch_text(str(e)).plain)
        return _EXIT_PREFLIGHT
    except PolicyValidationError as e:
        _dirty_scheduler.on_workflow_restart()
        active_display.emit_warning(_pipeline_config_error_text(e.message).plain)
        return _EXIT_PREFLIGHT
    except Exception as e:
        _dirty_scheduler.on_workflow_fail()
        logger.exception("Pipeline execution failed: {}")
        active_display.emit_warning(f"Pipeline failed: {e}")
        return _EXIT_CONFIG_ERROR


def _save_interrupt_checkpoint(initial_state: PipelineState) -> None:
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


def _maybe_enter_process_view(stack: ExitStack) -> Path | None:
    if has_machine_global_skills():
        return None
    return stack.enter_context(SkillsProcessView())


def _warn_if_capabilities_degraded(display_context: DisplayContext, workspace_root: Path) -> None:
    state_path = default_state_path()
    if not state_path.exists():
        return
    health = SkillManager().check_baseline_health()
    mandatory_keys = ("web_search", "visit_url", "skills")
    if any(not health.get(k) for k in mandatory_keys):
        display = resolve_active_display(None, display_context)
        display.emit_info_panel(
            title="Baseline Capability Warning",
            content=(
                "One or more baseline capabilities may need attention.\n"
                "Run `ralph --init` to repair or update."
            ),
        )


def _print_project_skill_conflict_hint(failures: list[str]) -> None:
    if not failures:
        return
    display = resolve_active_display(None, make_display_context())
    display.emit_skill_failure_warning(failures)


def _print_user_global_update_hint() -> None:
    display = resolve_active_display(None, make_display_context())
    display.emit_warning(
        "Baseline skills have an update available. "
        "Run `ralph --force-init-skills` to apply, "
        "or `ralph --diagnose` for details."
    )


def _sync_shipped_skills_on_pipeline_run(
    workspace_root: Path | None = None,
    *,
    keep_run_id: str | None = None,
    retention_max_age_seconds: float | None = None,
) -> None:
    sync_shipped_skills(
        workspace_root,
        keep_run_id=keep_run_id,
        retention_max_age_seconds=retention_max_age_seconds,
        dependencies=SetupDependencies(
            emit_warning=_emit_setup_warning,
            print_user_global_update_hint=_print_user_global_update_hint,
            print_project_skill_conflict_hint=_print_project_skill_conflict_hint,
            run_retention_sweep=_run_retention_sweep_for_setup,
            skill_manager_factory=SkillManager,
            project_skills_need_install=_project_skills_need_install,
            install_project_skills=install_project_baseline_skills,
        ),
    )


def _run_retention_sweep_for_setup(
    root: Path,
    *,
    keep_run_id: str | None,
    retention_max_age_seconds: float | None,
) -> None:
    run_start_retention_sweep(
        root,
        keep_run_id=keep_run_id,
        retention_max_age_seconds=retention_max_age_seconds,
        emit_warning=_emit_setup_warning,
    )


def _emit_setup_warning(message: str) -> None:
    logger.warning(message)
    with suppress(Exception):
        display = resolve_active_display(None, make_display_context())
    with suppress(Exception):
        display.emit_warning(message)


sync_shipped_skills_on_pipeline_run = _sync_shipped_skills_on_pipeline_run


def _run_project_policy_readiness(
    *,
    load_result: _LoadResult,
    display_context: DisplayContext,
    display: ParallelDisplay | None = None,
    mode: PolicyMode = PolicyMode.NORMAL,
    workspace_factory: Callable[[], Workspace] | None = None,
    emit_factory: Callable[[str], None] | None = None,
    invoke_remediation_agent_factory: Callable[[Workspace], InvokePolicyAgent] | None = None,
) -> int:
    try:
        from ralph.project_policy.cli_integration import (
            run_project_policy_readiness as _orchestrator,
        )

        return _orchestrator(
            load_result=load_result,
            display_context=display_context,
            display=display,
            mode=mode,
            workspace_factory=workspace_factory,
            emit_factory=emit_factory,
            invoke_remediation_agent_factory=invoke_remediation_agent_factory,
        )
    except Exception:
        logger.opt(exception=True).warning(
            "project-policy preflight could not run; continuing the development run"
        )
        return _EXIT_PREFLIGHT if mode.exits_after() else _EXIT_SUCCESS


def _detail_text(label: str, detail: str) -> Text:
    text = Text()
    text.append(f"  {label}: ")
    text.append(detail)
    return text


def run_pipeline(
    request: RunPipelineRequest | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    **kwargs: Unpack[_LegacyRunPipelineKwargs],
) -> int:
    _ensure_broker_secret()
    protect_broker_secret()
    ctx = display_context if display_context is not None else make_display_context()
    if request is None:
        manifest_from_kwargs = kwargs.get("parallel_worker_manifest")
        request = RunPipelineRequest(
            config_path=kwargs.get("config_path"),
            cli_overrides=kwargs.get("cli_overrides"),
            dry_run=kwargs.get("dry_run", False),
            resume=kwargs.get("resume", False),
            verbosity=kwargs.get("verbosity"),
            counter_overrides=kwargs.get("counter_overrides"),
            parallel_worker_manifest=(
                Path(manifest_from_kwargs)
                if isinstance(manifest_from_kwargs, str)
                else manifest_from_kwargs
            ),
            pro_hooks=pro_hooks,
            model_identity=model_identity,
        )
    effective_request = request._replace(
        pro_hooks=pro_hooks if pro_hooks is not None else request.pro_hooks,
        model_identity=model_identity if model_identity is not None else request.model_identity,
    )
    effective_counter_overrides = effective_request.counter_overrides or {}
    effective_parallel_worker_manifest = effective_request.parallel_worker_manifest
    if effective_parallel_worker_manifest is None:
        manifest_from_env = ctx.env.get(str(RALPH_PARALLEL_WORKER_MANIFEST_ENV))
        if manifest_from_env:
            effective_parallel_worker_manifest = Path(manifest_from_env)

    if effective_parallel_worker_manifest is not None:
        return run_parallel_worker_from_manifest(
            manifest_path=effective_parallel_worker_manifest,
            display_context=ctx,
            model_identity=effective_request.model_identity,
            pro_hooks=effective_request.pro_hooks,
        )

    load_result = _load_configuration(
        effective_request.config_path,
        effective_request.cli_overrides or {},
        effective_request.resume,
        display_context=ctx,
    )
    if isinstance(load_result, int):
        return load_result
    preflight_result = _run_preflight_checks(
        _PreflightRequest(
            config=load_result.config,
            workspace_scope=load_result.workspace_scope,
            policy_bundle=load_result.policy_bundle,
            initial_state=load_result.initial_state,
            counter_overrides=effective_counter_overrides,
            parallel_worker_manifest=effective_request.parallel_worker_manifest,
        ),
        display_context=ctx,
    )

    if preflight_result != _EXIT_SUCCESS:
        return preflight_result

    if load_result.workspace_scope is not None:
        _sync_shipped_skills_on_pipeline_run(
            workspace_root=load_result.workspace_scope.root,
            keep_run_id=load_result.run_id,
            retention_max_age_seconds=load_result.config.general.retention_max_age_days * 86400.0,
        )
        _warn_if_capabilities_degraded(ctx, load_result.workspace_scope.root)

    active_display: ParallelDisplay | None = None
    if load_result.workspace_scope is not None:
        active_display = resolve_active_display(None, ctx)
        active_display.start()
        policy_readiness_result = _run_project_policy_readiness(
            load_result=load_result,
            display_context=ctx,
            display=active_display,
            mode=effective_request.policy_mode,
        )
        if effective_request.policy_mode.exits_after():
            active_display.stop()
            return policy_readiness_result
        if policy_readiness_result != _EXIT_SUCCESS:
            logger.warning(
                "project-policy preflight returned {}; continuing the run anyway",
                policy_readiness_result,
            )

    try:
        if effective_request.dry_run:
            if active_display is not None:
                active_display.stop()
            print_dry_run(
                load_result.initial_state,
                load_result.config,
                load_result.policy_bundle,
                display_context=ctx,
            )
            return _EXIT_SUCCESS
        with ExitStack() as _stack:
            _maybe_enter_process_view(_stack)
            return _execute_pipeline(
                _ExecutePipelineRequest(
                    config=load_result.config,
                    initial_state=load_result.initial_state,
                    policy_bundle=load_result.policy_bundle,
                    verbosity=effective_request.verbosity,
                    counter_overrides=effective_counter_overrides,
                    config_path=effective_request.config_path,
                    cli_overrides=effective_request.cli_overrides,
                    parallel_worker_manifest=effective_request.parallel_worker_manifest,
                    pro_hooks=effective_request.pro_hooks,
                    model_identity=effective_request.model_identity,
                ),
                display_context=ctx,
                display=active_display,
                display_is_active=active_display is not None,
            )
    finally:
        _unregister_active_run(load_result)


def _unregister_active_run(load_result: _LoadResult) -> None:
    if load_result.workspace_scope is None:
        return
    try:
        from ralph.workspace.agent_dir_retention import unregister_active_run

        unregister_active_run(load_result.workspace_scope.root, load_result.run_id)
    except Exception as exc:
        logger.debug("Active-run unregistration failed (non-fatal): {}", exc)


validate_loaded_policy_bundle = _validate_loaded_policy_bundle
state = _state
invalidate_pipeline_state_if_prompt_changed = _invalidate_pipeline_state_if_prompt_changed
