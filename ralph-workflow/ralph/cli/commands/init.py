"""Init command for Ralph Workflow CLI.

This module implements the initialization command that sets up
Ralph Workflow in a repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

import ralph.policy
from ralph.config.bootstrap import (
    BootstrapResult,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_local_configs,
)
from ralph.config.welcome import emit_first_run_welcome
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from ralph.agents.registry import AgentRegistry

STARTER_PROMPT_SENTINEL = (
    "<!-- ralph:starter-prompt: edit this file before running `ralph` -->"
)


def _resolve_console(display_context: DisplayContext | None) -> DisplayContext:
    return display_context if display_context is not None else make_display_context()


def init_command(
    template: str | None = None,
    config_path: Path | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Initialize Ralph Workflow in the current working directory.

    Args:
        template: Optional template name (e.g. 'default').
              All labels currently produce the same starter content.
        config_path: Optional path for config file.
        display_context: Optional display context for consistent rendering.
    """
    ctx = _resolve_console(display_context)
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
    agent_dir = target / ".agent"
    agent_dir.mkdir(exist_ok=True)

    prompt_path = target / "PROMPT.md"
    if not prompt_path.exists():
        prompt_path.write_text(
            STARTER_PROMPT_SENTINEL
            + "\n\n"
            "PROMPT.md is the goal and acceptance-criteria document that Ralph Workflow reads "
            "as its task input. Replace the example content below with YOUR task description, "
            "then remove the sentinel comment at the top before running `ralph`.\n\n"
            "# Goal\n\n"
            "Add a /health endpoint to the example API that returns HTTP 200 with a JSON body"
            ' `{"status": "ok"}`.\n'
            "This endpoint should be unauthenticated and return a Content-Type of"
            " application/json.\n"
            "It is used by load balancers and uptime monitors to verify the service is"
            " running.\n\n"
            "## Context\n\n"
            "- Main API entry point: `src/api/app.py`\n"
            "- Existing route examples: `src/api/routes/`\n"
            "- Dependencies and external services: see `README.md`\n\n"
            "## Acceptance criteria\n\n"
            "- GET /health returns HTTP 200\n"
            "- Response body is valid JSON with `status` == `ok`\n"
            "- A new test in `tests/` covers the new endpoint\n\n"
            "## Notes\n\n"
            "- Keep the prompt scoped — one user-visible outcome per run works best.\n"
            "- Describe constraints (language, framework, test style) in Context above.\n\n"
            "---\n\n"
            "**Next steps**\n\n"
            "1. Edit the sections above to describe YOUR task and remove the sentinel comment.\n"
            "2. Run `ralph --diagnose` to verify agents, MCP servers, and config.\n"
            "3. Run `ralph` to start the planning → development → review → fix pipeline.\n",
            encoding="utf-8",
        )
        console.print(_status_text("Created", str(prompt_path), "theme.status.success"))

    bundled_defaults = Path(ralph.policy.__file__).parent / "defaults"

    if config_path is not None and not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bundled_defaults / "ralph-workflow.toml"), str(config_path))
        console.print(_status_text("Created", str(config_path), "theme.status.success"))
    elif config_path is None:
        local_results = ensure_local_configs(agent_dir)
        global_results: list[BootstrapResult] = [
            ensure_global_config(),
            ensure_global_mcp_config(),
        ]
        all_results = local_results + global_results

        # Show welcome banner if anything was created/regenerated
        created_or_regenerated = [r for r in all_results if r.action in {"created", "regenerated"}]
        if created_or_regenerated:
            registry = _try_load_registry()
            emit_first_run_welcome(
                console,
                all_results,
                agent_registry=registry,
                display_context=ctx,
            )
        else:
            # All skipped - show fallback next steps
            _print_fallback_next_steps(target, ctx)


def _try_load_registry() -> AgentRegistry | None:
    """Attempt to load the agent registry; returns None on failure."""
    from ralph.agents.registry import AgentRegistry  # noqa: PLC0415
    from ralph.config.loader import load_config  # noqa: PLC0415

    try:
        cfg = load_config(None, {})
        return AgentRegistry.from_config(cfg)
    except Exception:
        return None


def _print_fallback_next_steps(target: Path, display_context: DisplayContext | None = None) -> None:
    """Print next steps when all configs were skipped (re-running init)."""
    ctx = _resolve_console(display_context)
    console = ctx.console
    console.print(_status_text("Ralph Workflow initialized in", str(target), "theme.cat.meta"))
    console.print(
        "\nRalph Workflow orchestrates AI coding agents through a"
        " [theme.phase.planning]planning → development → review → fix[/theme.phase.planning]"
        " loop driven by PROMPT.md."
    )
    console.print(
        Text("Docs: ", style="theme.text.muted")
    )
    console.print(
        "[theme.text.muted]New to Ralph Workflow?[/theme.text.muted] Start with"
        " [theme.cat.meta]docs/sphinx/getting-started.md[/theme.cat.meta]"
        " for a step-by-step walkthrough."
    )
    console.print(Text("\nNext steps:", style="theme.text.muted"))
    console.print(
        "  1. Edit [theme.cat.meta]PROMPT.md[/theme.cat.meta] with your implementation task"
    )
    console.print(
        "  2. (Optional) Read"
        " [theme.cat.meta]docs/sphinx/getting-started.md[/theme.cat.meta]"
        " for a step-by-step first-run walkthrough"
    )
    console.print(
        "  3. (Optional) Override defaults in"
        " [theme.cat.meta].agent/ralph-workflow.toml[/theme.cat.meta]"
        " or [theme.cat.meta]~/.config/ralph-workflow.toml[/theme.cat.meta]"
    )
    console.print(
        "  4. (Optional) Configure MCP servers in"
        " [theme.cat.meta].agent/mcp.toml[/theme.cat.meta]"
        " or [theme.cat.meta]~/.config/ralph-workflow-mcp.toml[/theme.cat.meta]"
    )
    console.print(
        "  5. (Optional) Review [theme.cat.meta].agent/pipeline.toml[/theme.cat.meta] and"
        " [theme.cat.meta].agent/artifacts.toml[/theme.cat.meta]"
        " if you need advanced workflow overrides"
    )
    console.print(
        "  6. (Optional) Run [theme.cat.meta]ralph --diagnose[/theme.cat.meta]"
        " to verify agents, MCP servers, and config"
    )
    console.print("  7. Run [theme.cat.meta]ralph[/theme.cat.meta] to start the pipeline")
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
