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

from rich.text import Text

import ralph.policy
from ralph.config.bootstrap import (
    BootstrapResult,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
    ensure_local_support_configs,
)
from ralph.config.welcome import emit_first_run_welcome
from ralph.onboarding import (
    STARTER_PROMPT_SENTINEL as _STARTER_PROMPT_SENTINEL,
)
from ralph.onboarding import (
    fallback_next_steps,
    getting_started_pointer_sentence,
    starter_prompt_template,
)

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Protocol

    from rich.console import Console

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
from ralph.skills._baseline_catalog import STATIC_BUILTIN_CAPABILITIES
from ralph.skills._capability_state import CapabilityState
from ralph.skills._capability_status import CapabilityStatus
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
    """Initialize Ralph Workflow in the current working directory.

    Args:
        template: Optional template name (e.g. 'default').
              All labels currently produce the same starter content.
        config_path: Optional path for config file.
        display_context: Display context for consistent rendering. If None, a default
            context is created using make_display_context().
    """
    ctx = display_context if display_context is not None else make_display_context()
    console = ctx.console
    if template:
        console.print(
            Text(
                f"Warning: --init label {template!r} is deprecated and ignored; "
                "use `ralph --init` without a label.",
                style="theme.status.warning",
            )
        )

    target = Path.cwd()
    scope = resolve_workspace_scope(target)
    agent_dir = scope.local_config_path.parent

    prompt_path = target / "PROMPT.md"
    if not prompt_path.exists():
        prompt_path.write_text(starter_prompt_template(), encoding="utf-8")
        console.print(_status_text("Created", str(prompt_path), "theme.status.success"))

    bundled_defaults = Path(ralph.policy.__file__).parent / "defaults"

    if config_path is not None and not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bundled_defaults / "ralph-workflow.toml"), str(config_path))
        console.print(_status_text("Created", str(config_path), "theme.status.success"))
        # Even on the legacy config_path-copy branch, run the always-on
        # auto-skill-install so a one-shot init copy still triggers the
        # skill fan-out. Failures are swallowed and surfaced via the
        # welcome-banner / fallback paths.
        _, failures = _ensure_baseline_capabilities(display_context=ctx)
        _print_skill_failure_warning(console, failures)
    elif config_path is None:
        global_results: list[BootstrapResult] = [
            ensure_global_config(),
            ensure_global_mcp_config(),
            *ensure_global_policy_configs(),
        ]
        local_results = ensure_local_support_configs(agent_dir)
        all_results = global_results + local_results

        # Always run the capability refresh + summary table on every init invocation.
        # The failures list is threaded into the welcome-banner / fallback path so
        # a NEEDS_REPAIR is visible regardless of which code path fires.
        _, failures = _ensure_baseline_capabilities(display_context=ctx)

        created_or_regenerated = [r for r in all_results if r.action in {"created", "regenerated"}]
        if created_or_regenerated:
            registry = _try_load_registry()
            emit_first_run_welcome(
                console,
                all_results,
                agent_registry=registry,
                display_context=ctx,
            )
            _print_skill_failure_warning(console, failures)
        else:
            # All skipped - show fallback next steps (with skill-failure warning if any)
            _print_fallback_next_steps(target, failures=failures, display_context=ctx)


def _print_skill_failure_warning(console: Console, failures: list[str]) -> None:
    """Print a one-line skill-install failure warning when the list is non-empty.

    Surfaced on BOTH the first-run welcome banner path AND the re-run
    fallback path so a NEEDS_REPAIR status from install_baseline_skills is
    visible to the user regardless of which init code path fires.
    """
    if not failures:
        return
    console.print(
        Text(
            f"Skills auto-install reported: {', '.join(failures)}. "
            "Run `ralph --diagnose` for details.",
            style="theme.status.warning",
        )
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
    ctx = display_context
    console = ctx.console
    try:
        manager = SkillManager()
        cap_state, failures = manager.ensure_baseline_capabilities(workspace_root=Path.cwd())
        _print_capability_summary(console, cap_state)
        return cap_state, failures
    except Exception:
        return CapabilityState(), []


def _collect_skill_root_rows() -> list[tuple[str, str, Text]]:
    """Build the (agent, skill_root, status_text) rows for the per-agent coverage table."""
    from ralph.skills._agent_paths import agent_skill_roots
    from ralph.skills._content import BASELINE_SKILL_NAMES

    rows: list[tuple[str, str, Text]] = []
    for entry in agent_skill_roots():
        resolved = entry.resolve()
        all_present = all((resolved / name / "SKILL.md").exists() for name in BASELINE_SKILL_NAMES)
        if all_present:
            status_text = Text("OK", style="theme.status.success")
        else:
            status_text = Text("Skipped", style="theme.status.pending")
        label = f"{entry.agent} (canonical)" if entry.is_canonical else entry.agent
        rows.append((label, str(resolved), status_text))
    return rows


def _print_capability_summary(console: Console, state: CapabilityState) -> None:
    """Print the baseline capabilities summary table."""
    from rich.table import Table

    table = Table(title="Baseline Capabilities", show_header=True)
    table.add_column("Capability", style="theme.cat.meta")
    table.add_column("Type")
    table.add_column("Status")
    # Static built-in capabilities — always available
    for cap in STATIC_BUILTIN_CAPABILITIES:
        table.add_row(
            cap.name.replace("_", " ").title(),
            "Built-in",
            Text("OK — always available", style="theme.status.success"),
        )
    # Health-tracked dependency-backed helpers
    managed_rows = [
        ("Web search (DuckDuckGo)", state.web_search),
        ("Page retrieval (visit_url)", state.visit_url),
        ("Docs MCP (localhost:6280)", state.docs_mcp),
        ("Skill bundles", state.skills),
    ]
    for label, entry in managed_rows:
        if entry.status == CapabilityStatus.INSTALLED_HEALTHY:
            status_text = Text("OK", style="theme.status.success")
        elif entry.update_available:
            status_text = Text(
                "Update available — run `ralph --init` to update",
                style="theme.status.warning",
            )
        else:
            status_text = Text(
                f"{entry.status.value} — run `ralph --init` or check config",
                style="theme.status.warning",
            )
        table.add_row(label, "Managed", status_text)
    console.print(table)

    if state.skills.status != CapabilityStatus.NOT_INSTALLED:
        console.print(Text("Skill root coverage", style="theme.cat.meta"))
        skill_table = Table(show_header=True)
        skill_table.add_column("Agent", style="theme.cat.meta")
        skill_table.add_column("Skill root", style="theme.text.muted")
        skill_table.add_column("Status")
        for agent_label, skill_root, status_text in _collect_skill_root_rows():
            skill_table.add_row(agent_label, skill_root, status_text)
        console.print(skill_table)


def _print_fallback_next_steps(
    target: Path, *, failures: list[str] | None = None, display_context: DisplayContext
) -> None:
    """Print next steps when all configs were skipped (re-running init)."""
    ctx = display_context
    console = ctx.console
    console.print(_status_text("Ralph Workflow initialized in", str(target), "theme.cat.meta"))
    console.print(
        "\nRalph Workflow orchestrates AI coding agents through a"
        " [theme.phase.planning]planning → development loop[/theme.phase.planning]"
        " driven by PROMPT.md."
    )
    console.print(Text("Docs: ", style="theme.text.muted"))
    console.print(Text(getting_started_pointer_sentence(), style="theme.text.muted"))
    console.print(Text("\nNext steps:", style="theme.text.muted"))
    for index, line in enumerate(fallback_next_steps(), start=1):
        console.print(f"  {index}. {line}")
    if failures:
        _print_skill_failure_warning(console, failures)
    console.print(
        "\n[theme.text.muted]To reset configs later:"
        " [theme.cat.meta]ralph --regenerate-config[/theme.cat.meta][/theme.text.muted]"
    )


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text
