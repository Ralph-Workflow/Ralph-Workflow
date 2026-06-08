"""Black-box tests for the auto-symlinked copy in ralph.onboarding next steps."""

from __future__ import annotations

from ralph.onboarding import fallback_next_steps, welcome_panel_next_steps


def test_welcome_panel_next_steps_contains_auto_symlinked() -> None:
    output = "\n".join(welcome_panel_next_steps())
    assert "auto-symlinked" in output, (
        f"Expected 'auto-symlinked' in welcome_panel_next_steps, got: {output!r}"
    )


def test_fallback_next_steps_contains_auto_symlinked() -> None:
    output = "\n".join(fallback_next_steps())
    assert "auto-symlinked" in output, (
        f"Expected 'auto-symlinked' in fallback_next_steps, got: {output!r}"
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
