from __future__ import annotations

from ralph.onboarding import (
    GETTING_STARTED_DOC,
    INIT_COMMAND,
    PROMPT_FILE,
    RUN_COMMAND,
    starter_prompt_validation_hint,
)


def build_next_steps(
    *,
    validation_ok: bool,
    agent_missing: bool,
    prompt_exists: bool,
    prompt_has_sentinel: bool,
) -> list[str]:
    steps: list[str] = []

    if not prompt_exists:
        steps.append(f"Run `{INIT_COMMAND}` to scaffold {PROMPT_FILE} and project config files.")
    elif prompt_has_sentinel:
        steps.append(starter_prompt_validation_hint())

    if agent_missing:
        steps.append(
            "Install at least one supported agent: "
            "Claude Code (https://docs.claude.com/claude-code), "
            "Codex CLI (https://github.com/openai/codex), "
            "OpenCode (https://opencode.ai), "
            "or Google Anti Gravity (https://github.com/google-antigravity/antigravity-cli)."
        )

    if not validation_ok:
        steps.append(
            "Pre-flight validation failed: see the Pre-flight Validation table above. "
            "Fix policy errors with `ralph --regenerate-config` if config files were edited."
        )

    if not steps:
        steps.append(f"Run `{RUN_COMMAND}` to start the pipeline.")

    return steps


def _print_next_steps_panel(steps: list[str], *, display: object) -> None:
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    content_parts: list[str] = []
    for i, step in enumerate(steps):
        if i > 0:
            content_parts.append("")
        content_parts.append(f"  \u2022 {step}")
    content_parts.append("")
    content_parts.append(
        f"New to Ralph Workflow? {GETTING_STARTED_DOC} \u2014 step-by-step walkthrough."
    )
    display.emit_info_panel(title="Next steps", content="\n".join(content_parts))
