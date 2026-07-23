"""Init command for Ralph Workflow CLI.

This module implements the initialization command that sets up
Ralph Workflow in a repository.

AUTO-SKILL-INSTALL CONTRACT
=============================
`ralph --init` ALWAYS invokes the baseline skill installer on every run,
including the re-run path where every bootstrap result is `skipped`.
This guarantees that the bundled skill bundle is materialized at
`~/.claude/skills/` and symlinked into every registered sibling agent
root, regardless of whether other config files needed creation. The
installer failures (e.g. `sibling-conflict-*`) are surfaced to the user
on both the first-run and re-run paths.
"""

from __future__ import annotations

import shutil
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer

import ralph.policy
from ralph.config.agent_detection import enable_detected_agents
from ralph.config.bootstrap import (
    BootstrapResult,
    auto_seed_default_git_exclude,
    auto_seed_default_gitignore,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
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
        ) -> UnifiedConfig: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> AgentRegistry: ...


from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.skills._capability_state import CapabilityState
from ralph.skills.manager import SkillManager

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
    """Initialize Ralph Workflow in the current working directory.

    Args:
        template: Optional prompt-template name.
        config_path: Optional path for config file.
        display_context: Display context for consistent rendering. If None, a default
            context is created using make_display_context().
    """
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
        prompt_path.write_text(prompt, encoding="utf-8")
        display.emit_status(f"Created: {prompt_path}")
    elif template:
        # PROMPT.md already exists. An explicit `--init <label>` is NEVER
        # silently dropped: the operator's intent was to choose a starter
        # shape, so we still validate the label and tell them why their
        # file wasn't overwritten. Unknown labels raise as today so a
        # typo'd `--init feature-specs` (note the plural) still exits 1.
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
    auto_seed_default_git_exclude(target)

    bundled_defaults = Path(ralph.policy.__file__).parent / "defaults"

    if config_path is not None and not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bundled_defaults / "ralph-workflow.toml"), str(config_path))
        display.emit_status(f"Created: {config_path}")
        newly_enabled = enable_detected_agents(config_path)
        _, failures = _ensure_baseline_capabilities(display_context=ctx)
        if newly_enabled:
            display.emit_status("Auto-enabled agents (found on PATH): " + ", ".join(newly_enabled))
        if failures:
            display.emit_skill_failure_warning(failures)
    elif config_path is not None:
        newly_enabled = enable_detected_agents(config_path)
        _, failures = _ensure_baseline_capabilities(display_context=ctx)
        if newly_enabled:
            display.emit_status("Auto-enabled agents (found on PATH): " + ", ".join(newly_enabled))
        if failures:
            display.emit_skill_failure_warning(failures)
    else:
        global_results: list[BootstrapResult] = [
            ensure_global_config(),
            ensure_global_mcp_config(),
            *ensure_global_policy_configs(),
        ]
        all_results = global_results
        newly_enabled = enable_detected_agents()

        _, failures = _ensure_baseline_capabilities(display_context=ctx)

        created_or_regenerated = [r for r in all_results if r.action in {"created", "regenerated"}]
        if created_or_regenerated:
            registry = _try_load_registry()
            emit_first_run_welcome(
                all_results,
                agent_registry=registry,
                newly_enabled=newly_enabled,
                display_context=ctx,
            )
            if failures:
                display.emit_skill_failure_warning(failures)
        else:
            _print_fallback_next_steps(
                target,
                newly_enabled=newly_enabled,
                failures=failures,
                display_context=ctx,
            )


def _try_load_registry() -> AgentRegistry | None:
    """Attempt to load the agent registry; returns None on failure."""
    try:
        cfg = _load_config_loader()(None, {})
        registry_type = _load_agent_registry_factory()
        return registry_type.from_config(cfg)
    except Exception:
        return None


def _ensure_baseline_capabilities(
    *, display_context: DisplayContext
) -> tuple[CapabilityState, list[str]]:
    """Install baseline skills, print the capability summary, and return (state, failures).

    Returns (CapabilityState, list[str]) where the second element is the list of
    failure codes returned by install_baseline_skills (empty list on success or
    on a swallowed exception). The init_command caller threads the failures
    list into the welcome-banner and fallback code paths so a NEEDS_REPAIR is
    visible on every ralph --init invocation, not just first run.
    """
    from contextlib import suppress

    from ralph.skills._installer import (
        _project_skills_need_install,
        install_project_baseline_skills,
    )

    ctx = display_context
    display = resolve_active_display(None, ctx)
    target_root = Path.cwd()
    try:
        manager = SkillManager()
        cap_state, failures = manager.ensure_baseline_capabilities(workspace_root=target_root)
        with suppress(Exception):
            if _project_skills_need_install(target_root):
                # PA-004: discard the CapabilityEntry since
                # ensure_baseline_capabilities already re-stamped the
                # state with whichever user-global entry is worst.
                _, project_failures = install_project_baseline_skills(target_root)
                failures.extend(project_failures)
        display.emit_capability_summary(cap_state, workspace_root=target_root)
        return cap_state, failures
    except Exception:
        return CapabilityState(), []


def _print_fallback_next_steps(
    target: Path,
    *,
    newly_enabled: list[str] | None = None,
    failures: list[str] | None = None,
    display_context: DisplayContext,
) -> None:
    """Print next steps when all configs were skipped (re-running init)."""
    display = resolve_active_display(None, display_context)
    display.emit_status(f"Ralph Workflow initialized in: {target}")
    if newly_enabled:
        display.emit_status("Auto-enabled agents (found on PATH): " + ", ".join(newly_enabled))
    display.emit_status(
        "\nRalph Workflow orchestrates AI coding agents through a"
        " planning → development loop driven by PROMPT.md."
    )
    display.emit_status(f"\nDocs: {getting_started_pointer_sentence()}")
    display.emit_fallback_next_steps(list(fallback_next_steps()))
    if failures:
        display.emit_skill_failure_warning(failures)
    display.emit_status("\nTo reset configs later: ralph --regenerate-config")
