"""Black-box tests for the bundled-skill + gitignore copy in ralph.onboarding next steps."""

from __future__ import annotations

from ralph.onboarding import (
    PROJECT_CANONICAL_SKILLS_PATH,
    PROJECT_SIBLING_SKILL_PATHS,
    fallback_next_steps,
    welcome_panel_next_steps,
)


def test_welcome_panel_next_steps_mentions_skill_install_path() -> None:
    output = "\n".join(welcome_panel_next_steps())
    assert "~/.claude/skills/" in output, (
        f"Expected canonical skill install path in welcome_panel_next_steps, got: {output!r}"
    )


def test_fallback_next_steps_mentions_skill_recheck_and_gitignore() -> None:
    output = "\n".join(fallback_next_steps())
    assert "idempotent" in output, (
        f"Expected idempotency mention in fallback_next_steps, got: {output!r}"
    )
    assert ".gitignore" in output, (
        f"Expected .gitignore mention in fallback_next_steps, got: {output!r}"
    )


def test_welcome_panel_next_steps_still_mentions_agy_and_nanocoder() -> None:
    """Regression guard: the new bullet insertion must not drop the
    claude/opencode/nanocoder/agy install-line copy that test_config_welcome
    asserts on.
    """
    output = "\n".join(welcome_panel_next_steps())
    for token in ("claude", "opencode", "nanocoder", "agy"):
        assert token in output, (
            f"Expected {token!r} in welcome_panel_next_steps, got: {output!r}"
        )


def test_welcome_panel_next_steps_mentions_project_canonical() -> None:
    output = "\n".join(welcome_panel_next_steps())
    assert PROJECT_CANONICAL_SKILLS_PATH in output, (
        f"Expected {PROJECT_CANONICAL_SKILLS_PATH!r} in welcome_panel_next_steps, got: {output!r}"
    )


def test_welcome_panel_next_steps_mentions_three_project_siblings() -> None:
    output = "\n".join(welcome_panel_next_steps())
    for path in PROJECT_SIBLING_SKILL_PATHS:
        assert path in output, (
            f"Expected {path!r} in welcome_panel_next_steps, got: {output!r}"
        )


def test_welcome_panel_next_steps_lists_opencode_skills_exactly_once() -> None:
    output = "\n".join(welcome_panel_next_steps())
    assert output.count(PROJECT_CANONICAL_SKILLS_PATH) == 1, (
        f"Expected exactly one occurrence of {PROJECT_CANONICAL_SKILLS_PATH!r}, "
        f"got {output.count(PROJECT_CANONICAL_SKILLS_PATH)}"
    )
