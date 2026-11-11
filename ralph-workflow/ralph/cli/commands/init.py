
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from loguru import logger

from ralph.agents.agent_install_links import install_url_for
from ralph.config.agent_detection import (
    _commit_deterministic_config_write,
    autowire_chains_to_detected_agent,
    detect_installed_agents,
    enable_detected_agents,
)
from ralph.config.bootstrap import (
    BootstrapResult,
    auto_seed_default_git_exclude,
    auto_seed_default_gitignore,
    ensure_global_agents_config,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
    resolve_global_config_dir,
)
from ralph.config.welcome import emit_first_run_welcome
from ralph.onboarding import (
    STARTER_PROMPT_SENTINEL as _STARTER_PROMPT_SENTINEL,
)
from ralph.onboarding import (
    fallback_next_steps,
    getting_started_pointer_sentence,
    resolve_starter_template,
)
from ralph.pipeline._auto_integrate_reclaim import target_worktree_lookup
from ralph.project_policy.preflight import seed_project_policy_starters
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Protocol

    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.skills._capability_state import CapabilityState

    class _LoadConfigFn(Protocol):
        def __call__(
            self,
            config_path: Path | None = None,
            cli_overrides: dict[str, object] | None = None,
            *,
            workspace_scope: object | None = None,
        ) -> UnifiedConfig: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> AgentRegistry: ...


from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, resolve_active_display
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.skills._capability_state import CapabilityState
from ralph.skills.manager import SkillManager
from ralph.workspace.scope import resolve_workspace_scope

STARTER_PROMPT_SENTINEL = _STARTER_PROMPT_SENTINEL


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_config_loader() -> _LoadConfigFn:
    return cast(
        "_LoadConfigFn",
        _module_attr(import_module("ralph.config.loader"), "load_config"),
    )


def _load_agent_registry_factory() -> _AgentRegistryFactory:
    return cast(
        "_AgentRegistryFactory",
        _module_attr(import_module("ralph.agents.registry"), "AgentRegistry"),
    )


def init_command(
    template: str | None = None,
    config_path: Path | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> None:
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    target = Path.cwd()

    prompt_path = target / "PROMPT.md"
    if not prompt_path.exists():
        try:
            prompt = resolve_starter_template(template)
        except ValueError as exc:
            display.emit_warning(str(exc))
            raise typer.Exit(code=1) from exc
        _commit_deterministic_config_write(
            prompt_path,
            subject="chore(config): update agent configuration",
            write_fn=lambda: write_text_if_changed(
                DEFAULT_FILE_BACKEND, prompt_path, prompt, encoding="utf-8"
            ),
        )
        display.emit_status(f"Created: {prompt_path}")
    elif template:
        try:
            resolve_starter_template(template)
        except ValueError as exc:
            display.emit_warning(str(exc))
            raise typer.Exit(code=1) from exc
        display.emit_warning(
            f'PROMPT.md already exists; the "{template}" starter template was NOT applied. '
            f"Edit PROMPT.md directly, or delete it and run `ralph --init {template}`."
        )

    auto_seed_default_gitignore(target)
    if (target / ".git").exists():
        auto_seed_default_git_exclude(target)
    _emit_target_worktree_warning(display, target)

    ws = FsWorkspace(target)
    seed_project_policy_starters(ws)

    if config_path is not None and config_path.exists():
        newly_enabled = enable_detected_agents(config_path)
        rewired = autowire_chains_to_detected_agent(config_path)
        _, failures = _ensure_baseline_capabilities(display_context=ctx)
        _emit_agent_setup_status(display, newly_enabled, rewired)
        if failures:
            display.emit_skill_failure_warning(failures)
    else:
        if config_path is not None:
            display.emit_warning(
                f"Config file does not exist: {config_path}. `ralph --init` does not create "
                "project-local configuration; run `ralph --init-local-config` to create "
                "the optional local override set."
            )
        global_results: list[BootstrapResult] = [
            ensure_global_config(),
            ensure_global_agents_config(create_if_missing=True),
            ensure_global_mcp_config(),
            *ensure_global_policy_configs(),
        ]
        all_results = global_results
        newly_enabled = enable_detected_agents()
        global_main_config = resolve_global_config_dir() / "ralph-workflow.toml"
        rewired = (
            autowire_chains_to_detected_agent(global_main_config)
            if global_main_config.exists()
            else None
        )

        _, failures = _ensure_baseline_capabilities(display_context=ctx)

        created_or_regenerated = [r for r in all_results if r.action in {"created", "regenerated"}]
        if created_or_regenerated:
            registry = _try_load_registry()
            emit_first_run_welcome(
                all_results,
                agent_registry=registry,
                newly_enabled=newly_enabled,
                rewired=rewired if isinstance(rewired, list) else None,
                autowire_outcome=rewired if isinstance(rewired, str) else None,
                display_context=ctx,
            )
            if failures:
                display.emit_skill_failure_warning(failures)
        else:
            _print_fallback_next_steps(
                target,
                newly_enabled=newly_enabled,
                rewired=rewired if isinstance(rewired, list) else None,
                autowire_outcome=rewired if isinstance(rewired, str) else None,
                failures=failures,
                display_context=ctx,
            )


def _emit_target_worktree_warning(display: ParallelDisplay, target: Path) -> None:
    configured_target = "main"
    try:
        config = _load_config_loader()(None, {}, workspace_scope=resolve_workspace_scope())
        configured_target = config.general.auto_integrate_target
    except Exception:
        pass
    _verdict, owner = target_worktree_lookup(target, configured_target)
    if owner is not None:
        display.emit_warning(
            f"Auto-integration owns {owner}: Ralph Workflow may snapshot and reset uncommitted content there."
        )


def _try_load_registry() -> AgentRegistry | None:
    try:
        cfg = _load_config_loader()(None, {}, workspace_scope=resolve_workspace_scope())
        registry_type = _load_agent_registry_factory()
        return registry_type.from_config(cfg)
    except Exception as exc:
        logger.warning(
            "Could not load configured agents after setup: {}. "
            "Run `ralph --diagnose` to see the configuration problem and fix it.",
            exc,
        )
        return None


def _ensure_baseline_capabilities(
    *, display_context: DisplayContext
) -> tuple[CapabilityState, list[str]]:
    from contextlib import suppress

    from ralph.skills._installer import (
        _project_skills_need_install,
    )

    ctx = display_context
    display = resolve_active_display(None, ctx)
    target_root = Path.cwd()
    try:
        manager = SkillManager()
        cap_state, failures = manager.ensure_baseline_capabilities(workspace_root=target_root)
        with suppress(Exception):
            if _project_skills_need_install(target_root):
                from ralph.git.operations import (
                    create_commit,
                )
                from ralph.git.scoped_auto_commit import ScopedCommitStatus
                from ralph.skills._auto_commit import commit_skill_writes
                from ralph.skills._installer import (
                    install_project_baseline_skills_with_diff,
                )

                outcome = install_project_baseline_skills_with_diff(target_root)
                failures.extend(outcome.failures)
                try:
                    result = commit_skill_writes(
                        target_root,
                        written_paths=outcome.written_paths,
                        pre_contents=outcome.pre_contents,
                        create_commit_fn=create_commit,
                        intentional_transitions=outcome.intentional_transitions,
                    )
                    if result.status is ScopedCommitStatus.FAILED:
                        display.emit_warning(
                            f"Skill auto-commit failed during --init (non-fatal): {result.error}. "
                            "Skill content is materialized; commit manually or re-run to retry."
                        )
                except Exception as exc:
                    display.emit_warning(
                        f"Skill auto-commit failed during --init (non-fatal): {exc}. "
                        "Skill content is materialized; commit manually or re-run to retry."
                    )
        display.emit_capability_summary(cap_state, workspace_root=target_root)
        return cap_state, failures
    except Exception:
        return CapabilityState(), []


def _emit_agent_setup_status(
    display: ParallelDisplay, newly_enabled: list[str], rewired: list[str] | str | None
) -> None:
    if newly_enabled:
        display.emit_status("Auto-enabled agents (found on PATH): " + ", ".join(newly_enabled))
    if isinstance(rewired, list):
        display.emit_status(
            "Detected agent set for [agent_chains]; edit section 1 to change models."
        )
    elif rewired == "kept-default-agent":
        display.emit_status("kept claude (found on PATH)")
    elif rewired == "chains-customized":
        display.emit_status("chains already customized — left unchanged")


def _print_fallback_next_steps(
    target: Path,
    *,
    newly_enabled: list[str] | None = None,
    rewired: list[str] | None = None,
    autowire_outcome: str | None = None,
    failures: list[str] | None = None,
    display_context: DisplayContext,
) -> None:
    display = resolve_active_display(None, display_context)
    display.emit_status(f"Ralph Workflow initialized in: {target}")
    _emit_agent_setup_status(display, newly_enabled or [], rewired or autowire_outcome)
    display.emit_status(
        "\nRalph Workflow orchestrates AI coding agents through a"
        " planning → development loop driven by PROMPT.md."
    )
    display.emit_status(f"\nDocs: {getting_started_pointer_sentence()}")
    display.emit_fallback_next_steps(list(fallback_next_steps()))
    detected = detect_installed_agents()
    if not detected:
        install_options = ", ".join(
            f"{name}: {url}"
            for name in ("claude", "codex", "opencode", "nanocoder", "agy", "pi", "cursor")
            if (url := install_url_for(name)) is not None
        )
        display.emit_warning(
            "No agent CLIs found on PATH. Install one of: "
            f"{install_options}. Then re-run `ralph --init`."
        )
    if failures:
        display.emit_skill_failure_warning(failures)
    display.emit_status("\nTo reset configs later: ralph --regenerate-config")
