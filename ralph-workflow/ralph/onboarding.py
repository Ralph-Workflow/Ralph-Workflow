"""Shared onboarding copy for CLI, validation, and docs-facing messaging."""

from __future__ import annotations

from typing import Final

from ralph.skills._agent_paths import sibling_agent_skill_roots

GETTING_STARTED_DOC: Final[str] = "docs/sphinx/getting-started.md"
PROMPT_FILE: Final[str] = "PROMPT.md"
INIT_COMMAND: Final[str] = "ralph --init"
INIT_LOCAL_CONFIG_COMMAND: Final[str] = "ralph --init-local-config"
DIAGNOSE_COMMAND: Final[str] = "ralph --diagnose"
RUN_COMMAND: Final[str] = "ralph"
PROJECT_CANONICAL_SKILLS_PATH: Final[str] = "./.opencode/skills/"
PROJECT_SIBLING_SKILL_PATHS: Final[tuple[str, ...]] = (
    "./.claude/skills/",
    "./.codex/skills/",
    "./.gemini/antigravity-cli/skills/",
)
CODEBERG_REPO: Final[str] = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
ERROR_REPORTING_DISCLOSURE: Final[str] = (
    "Error reporting: Ralph Workflow sends anonymous crash data and performance metrics. "
    "No personal data is collected. See ~/.config/ralph-workflow-user.ini for details."
)
CODEBERG_STAR_CTA: Final[str] = (
    f"⭐ Star {CODEBERG_REPO} so we know you're using it — "
    "stars drive development priority. Run `ralph star` to star from your terminal."
)
RUN_COMPLETION_STAR_CTA: Final[str] = (
    f"⭐ If Ralph Workflow saved you time, star the repo: {CODEBERG_REPO}"
)
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
        "Initialize Ralph Workflow in the current directory (creates user-global config and "
        "PROMPT.md). Use `--init feature-spec`, `guardrail` (or `bug-fix`), `refactor`, "
        "`test-coverage`, or `docs` to choose a prompt shape. "
        f"Use `{INIT_LOCAL_CONFIG_COMMAND}` only when you want an {explanation}. "
        "Also seeds project-scope skills and the batteries-included `.gitignore` when missing."
    )


def init_local_config_help_text() -> str:
    """Return top-level help text for the optional local override command."""
    explanation = init_local_config_override_explanation()
    return f"Create .agent/ config files as an {explanation} for this repo."


def fresh_workspace_next_steps() -> tuple[str, ...]:
    """Return the minimal next steps for a completely fresh workspace."""
    return (
        "From a human-operated shell outside any Ralph-managed agent session, "
        f"run {INIT_COMMAND} to scaffold {PROMPT_FILE} and user-global config",
        f"Edit {PROMPT_FILE} with your task",
        f"From that same human-operated shell, run {RUN_COMMAND} to start the pipeline",
    )


def welcome_panel_next_steps() -> tuple[str, ...]:
    """Return the richer onboarding steps shown after initialization succeeds."""
    explanation = init_local_config_override_explanation()

    siblings = ", ".join(sibling.agent for sibling in sibling_agent_skill_roots())
    project_siblings = ", ".join(PROJECT_SIBLING_SKILL_PATHS)
    return (
        f"Edit {PROMPT_FILE} with your implementation task",
        "Skills and a batteries-included .gitignore are auto-seeded on every `ralph` run when "
        "missing (project scope: .opencode/skills/ canonical + symlinks to .claude/skills/, "
        ".codex/skills/, .gemini/antigravity-cli/skills/). Run `ralph --force-init-skills` to "
        "repair or overwrite a conflict.",
        "Install AI agents if missing (e.g., `claude`, `opencode`, `nanocoder`, `agy`, `cursor`)",
        f"Skills were installed to ~/.claude/skills/ and symlinked to {siblings}",
        f"Project-local skills were seeded to {PROJECT_CANONICAL_SKILLS_PATH}and symlinked to "
        f"{project_siblings} so every supported agent finds the same baseline. "
        f"Edit the SKILL.md in that canonical directory to customize; sibling symlinks are "
        f"preserved across `ralph` re-runs.",
        "The default .gitignore was seeded with patterns for Python, Node, Rust, Go, "
        "Ruby, PHP, Java/Kotlin, .NET, Dart/Flutter, Elixir, Scala, Terraform, "
        "and common IDE/OS files",
        f"(Optional) Run {INIT_LOCAL_CONFIG_COMMAND} when this repo needs an {explanation}",
        "(Recommended) From a human-operated shell outside any Ralph-managed agent "
        f"session, run {DIAGNOSE_COMMAND} to verify agents, MCP servers, and config "
        "before the first real run",
        f"From that same human-operated shell, run {RUN_COMMAND} to start the pipeline",
        "Run `ralph --regenerate-config` to reset configs",
        CODEBERG_STAR_CTA,
    )


def fallback_next_steps() -> tuple[str, ...]:
    """Return rerun guidance after init when files already exist."""
    explanation = init_local_config_override_explanation()
    project_siblings = ", ".join(PROJECT_SIBLING_SKILL_PATHS)
    return (
        f"Edit {PROMPT_FILE} with your implementation task",
        f"(Optional) Read {GETTING_STARTED_DOC} for a step-by-step first-run walkthrough",
        "Re-running init is idempotent; skills were re-checked and the default "
        ".gitignore was updated to cover common project structures (Python, Node, "
        "Rust, Go, Ruby, PHP, Java/Kotlin, .NET, Dart/Flutter, Elixir, Scala, "
        "Terraform, IDE/OS). A normal `ralph` run also auto-seeds the project-scope "
        "skills and .gitignore when missing, without requiring `ralph --init`.",
        f"Project-local skills under {PROJECT_CANONICAL_SKILLS_PATH}and the sibling symlinks "
        f"{project_siblings} were re-checked; re-running `ralph` is idempotent and will "
        f"not overwrite SKILL.md files you have edited.",
        f"(Optional) Run {INIT_LOCAL_CONFIG_COMMAND} when this repo needs an {explanation}",
        "(Optional) Configure MCP servers in `.agent/mcp.toml` or "
        "`~/.config/ralph-workflow-mcp.toml`",
        "(Optional) Review `.agent/pipeline.toml` and `.agent/artifacts.toml` "
        "if you need advanced workflow overrides",
        "(Recommended) From a human-operated shell outside any Ralph-managed agent "
        f"session, run {DIAGNOSE_COMMAND} to verify agents, MCP servers, and config "
        "before the first real run",
        f"From that same human-operated shell, run {RUN_COMMAND} to start the pipeline",
    )


def resolve_starter_template(label: str | None) -> str:
    """Return the requested starter PROMPT.md template.

    ``bug-fix`` is an alias for the guardrail template.  The bare init path
    keeps the established general-purpose template.
    """
    if not label:
        return starter_prompt_template()
    templates = {
        "feature-spec": (
            "# Goal\n\nAdd <feature> to <surface>. Keep the rest of the flow unchanged.\n\n"
            "## Acceptance criteria\n\n- <user action> now produces <expected result>\n"
            "- Existing behavior for <adjacent flow> stays unchanged\n"
            "- Tests cover the new behavior\n"
            "- Documentation or help text is updated if user-visible behavior changed\n"
        ),
        "guardrail": (
            "# Goal\n\nReject or block <invalid input / unsafe action> before <bad outcome> happens. "
            "Keep the normal success path unchanged.\n\n## Acceptance criteria\n\n"
            "- <invalid input> fails with a clear error or message\n"
            "- <bad side effect> does not happen for invalid input\n"
            "- Existing valid behavior stays unchanged\n"
            "- Tests cover the new validation or guardrail\n"
        ),
        "refactor": (
            "# Goal\n\nRefactor <module / component / command> to improve "
            "<maintainability / duplication / structure> without changing external behavior.\n\n"
            "## Acceptance criteria\n\n- Behavior stays the same for existing supported inputs\n"
            "- The targeted duplication or structural problem is reduced\n"
            "- Existing tests still pass\n"
            "- New or updated tests cover the area if needed to lock behavior in place\n"
        ),
        "test-coverage": (
            "# Goal\n\nAdd or improve automated tests for <feature / module / workflow>. "
            "Do not change production behavior unless a small testability fix is required.\n\n"
            "## Acceptance criteria\n\n- Tests cover the key success path for <feature>\n"
            "- Tests cover at least one important failure or edge case\n"
            "- Production changes stay minimal and scoped to testability if needed\n"
            "- The relevant test command passes\n"
        ),
        "docs": (
            "# Goal\n\nImprove <doc / README / onboarding page> so a new user can complete "
            "<specific outcome> without guessing.\n\n## Acceptance criteria\n\n"
            "- The doc clearly explains <specific concept or setup path>\n"
            "- Steps are ordered and runnable\n"
            "- Ambiguous wording or missing prerequisites are removed\n"
            "- The updated doc matches current behavior in the codebase\n"
        ),
    }
    selected = templates.get("guardrail" if label == "bug-fix" else label)
    if selected is None:
        raise ValueError(
            "Unknown PROMPT.md template "
            f"{label!r}. Valid templates: feature-spec, guardrail/bug-fix, refactor, "
            "test-coverage, docs."
        )
    return f"{STARTER_PROMPT_SENTINEL}\n\n{selected}"


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
        f"task input. Run `{INIT_COMMAND}` to scaffold PROMPT.md and user-global config, "
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
