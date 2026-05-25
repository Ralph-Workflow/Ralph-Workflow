"""Shared onboarding copy for CLI, validation, and docs-facing messaging."""

from __future__ import annotations

from typing import Final

GETTING_STARTED_DOC: Final[str] = "docs/sphinx/getting-started.md"
PROMPT_FILE: Final[str] = "PROMPT.md"
INIT_COMMAND: Final[str] = "ralph --init"
INIT_LOCAL_CONFIG_COMMAND: Final[str] = "ralph --init-local-config"
DIAGNOSE_COMMAND: Final[str] = "ralph --diagnose"
RUN_COMMAND: Final[str] = "ralph"
STARTER_PROMPT_SENTINEL: Final[str] = (
    "<!-- ralph:starter-prompt: edit this file before running `ralph` -->"
)


def getting_started_pointer_sentence() -> str:
    """Return the canonical getting-started docs pointer sentence."""
    return f"New to Ralph Workflow? Read {GETTING_STARTED_DOC} for a step-by-step walkthrough."


def init_local_config_override_explanation() -> str:
    """Return the canonical explanation for the local override command."""
    return "optional full project-local override copy of the user-global config set"


def init_help_text() -> str:
    """Return top-level help text for the canonical init command."""
    explanation = init_local_config_override_explanation()
    return (
        "Initialize Ralph Workflow in the current directory (scaffolds PROMPT.md plus "
        "project-local MCP/pipeline/artifact files copied from the user-global config set). "
        f"Use `{INIT_LOCAL_CONFIG_COMMAND}` only when you want an {explanation}. "
        "Labels are deprecated and ignored; use `--init` without a label."
    )


def init_local_config_help_text() -> str:
    """Return top-level help text for the optional local override command."""
    explanation = init_local_config_override_explanation()
    return f"Create .agent/ config files as an {explanation} for this repo."


def fresh_workspace_next_steps() -> tuple[str, ...]:
    """Return the minimal next steps for a completely fresh workspace."""
    return (
        f"Run {INIT_COMMAND} to scaffold {PROMPT_FILE} and .agent/ configs",
        f"Edit {PROMPT_FILE} with your task",
        f"Run {RUN_COMMAND} to start the pipeline",
    )


def welcome_panel_next_steps() -> tuple[str, ...]:
    """Return the richer onboarding steps shown after initialization succeeds."""
    explanation = init_local_config_override_explanation()
    return (
        f"Edit {PROMPT_FILE} with your implementation task",
        "Install AI agents if missing (e.g., `claude`, `opencode`, `agy`)",
        f"(Optional) Run {INIT_LOCAL_CONFIG_COMMAND} when this repo needs an {explanation}",
        f"(Recommended) Run {DIAGNOSE_COMMAND} to verify agents, MCP servers, and config "
        "before the first real run",
        f"Run {RUN_COMMAND} to start the pipeline",
        "Run `ralph --regenerate-config` to reset configs",
    )


def fallback_next_steps() -> tuple[str, ...]:
    """Return rerun guidance after init when files already exist."""
    explanation = init_local_config_override_explanation()
    return (
        f"Edit {PROMPT_FILE} with your implementation task",
        f"(Optional) Read {GETTING_STARTED_DOC} for a step-by-step first-run walkthrough",
        f"(Optional) Run {INIT_LOCAL_CONFIG_COMMAND} when this repo needs an {explanation}",
        "(Optional) Configure MCP servers in `.agent/mcp.toml` or "
        "`~/.config/ralph-workflow-mcp.toml`",
        "(Optional) Review `.agent/pipeline.toml` and `.agent/artifacts.toml` "
        "if you need advanced workflow overrides",
        f"(Recommended) Run {DIAGNOSE_COMMAND} to verify agents, MCP servers, and config "
        "before the first real run",
        f"Run {RUN_COMMAND} to start the pipeline",
    )


def starter_prompt_template() -> str:
    """Return the canonical starter PROMPT.md template."""
    return (
        STARTER_PROMPT_SENTINEL
        + "\n\n"
        + "PROMPT.md is the goal and acceptance-criteria document that Ralph Workflow reads "
        + "as its task input. Replace the example content below with YOUR task description, "
        + "then remove the sentinel comment at the top before running `ralph`.\n\n"
        + "# Goal\n\n"
        + "Add a /health endpoint to the example API that returns HTTP 200 with a JSON body"
        + ' `{"status": "ok"}`.\n'
        + "This endpoint should be unauthenticated and return a Content-Type of"
        + " application/json.\n"
        + "It is used by load balancers and uptime monitors to verify the service is"
        + " running.\n\n"
        + "## Context\n\n"
        + "- Main API entry point: `src/api/app.py`\n"
        + "- Existing route examples: `src/api/routes/`\n"
        + "- Dependencies and external services: see `README.md`\n\n"
        + "## Acceptance criteria\n\n"
        + "- GET /health returns HTTP 200\n"
        + "- Response body is valid JSON with `status` == `ok`\n"
        + "- A new test in `tests/` covers the new endpoint\n\n"
        + "## Notes\n\n"
        + "- Keep the prompt scoped — one user-visible outcome per run works best.\n"
        + "- Describe constraints (language, framework, test style) in Context above.\n\n"
        + "---\n\n"
        + "**Next steps**\n\n"
        + "1. Edit the sections above to describe YOUR task and remove the sentinel comment.\n"
        + f"2. Run `{DIAGNOSE_COMMAND}` to verify agents, MCP servers, and config "
        + "before the first real run.\n"
        + f"3. Run `{RUN_COMMAND}` to start the planning → development pipeline.\n"
    )


def missing_prompt_validation_hint() -> str:
    """Return canonical validation guidance when PROMPT.md is missing."""
    return (
        "PROMPT.md is the goal/acceptance-criteria document Ralph Workflow reads as its "
        f"task input. Run `{INIT_COMMAND}` to scaffold PROMPT.md and project config files, "
        "then edit PROMPT.md with the task you want Ralph Workflow to run. "
        f"{getting_started_pointer_sentence()}"
    )


def starter_prompt_validation_hint() -> str:
    """Return canonical validation guidance when the starter sentinel is still present."""
    return (
        "Edit PROMPT.md to describe YOUR task and remove the `<!-- ralph:starter-prompt "
        "... -->` marker once you have replaced the example content. "
        f"{DIAGNOSE_COMMAND} is a recommended verification step after initialization. "
        f"Then re-run `{RUN_COMMAND}`. New to Ralph Workflow? See {GETTING_STARTED_DOC} "
        "for a walkthrough, or docs/sphinx/concepts.md for what a good PROMPT.md should "
        "contain."
    )
