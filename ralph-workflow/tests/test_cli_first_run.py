"""Black-box CLI integration tests for first-run welcome banner and idempotency."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from ralph.banner import WELCOME_MESSAGE
from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL
from ralph.cli.main import app
from ralph.policy.validation import PolicyValidationError, validate_required_inputs

_MIN_PROMPT_SIZE_BYTES = 200

# Raw markup tokens that must never appear in rendered terminal output.
_RAW_MARKUP_TOKENS = (
    "[bold cyan]",
    "[/bold cyan]",
    "[yellow]",
    "[/yellow]",
    "[dim]",
    "[/dim]",
    "[green]",
    "[/green]",
    "[cyan]",
    "[/cyan]",
)


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set up a clean environment with temporary config and home directories."""
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "HOME": str(tmp_path / ".home"),
    }
    for key, val in env.items():
        monkeypatch.setenv(key, val)
        Path(val).mkdir(parents=True, exist_ok=True)
    return env


def test_cli_first_run_shows_welcome_banner(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """First invocation should show the 'Ralph Workflow first-run setup' banner and ASCII banner."""
    runner = CliRunner()

    # chdir to the temp path so .agent is created there
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--check-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "Ralph Workflow first-run setup" in result.output, (
        f"Expected 'Ralph Workflow first-run setup' in output, got: {result.output}"
    )
    assert WELCOME_MESSAGE in result.output, (
        f"Expected '{WELCOME_MESSAGE}' in output, got: {result.output}"
    )
    for token in _RAW_MARKUP_TOKENS:
        assert token not in result.output, (
            f"Raw markup token {token!r} found in CLI output: {result.output!r}"
        )


def test_cli_first_run_banner_not_shown_on_second_run(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Second invocation should NOT show the welcome banner (idempotency)."""
    runner = CliRunner()

    # chdir to the temp path
    monkeypatch.chdir(tmp_path)

    # First invocation - creates configs
    result1 = runner.invoke(app, ["--check-config"], catch_exceptions=False)
    assert result1.exit_code == 0

    # Second invocation - should skip creation, no banner
    result2 = runner.invoke(app, ["--check-config"], catch_exceptions=False)
    assert result2.exit_code == 0

    # Banner should only appear in first output
    assert "Ralph Workflow first-run setup" in result1.output
    assert "Ralph Workflow first-run setup" not in result2.output, (
        f"Welcome banner should not appear on second run. Output was: {result2.output}"
    )
    assert WELCOME_MESSAGE not in result2.output, (
        f"ASCII banner should not appear on second run. Output was: {result2.output}"
    )


def test_cli_regenerate_config_shows_banner(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regenerate-config should show the banner when files are regenerated."""
    runner = CliRunner()

    # chdir to the temp path
    monkeypatch.chdir(tmp_path)

    # First invocation creates configs
    result1 = runner.invoke(app, ["--check-config"], catch_exceptions=False)
    assert result1.exit_code == 0

    # Regenerate should show banner since files are overwritten
    result2 = runner.invoke(app, ["--regenerate-config"], catch_exceptions=False)
    assert result2.exit_code == 0, f"Expected exit 0, got {result2.exit_code}: {result2.output}"

    # The banner should appear (or a summary about configs being regenerated)
    output_lower = result2.output.lower()
    # Since regenerate produces "created_or_regenerated" results, welcome should fire
    # When files already exist and are regenerated, action is "regenerated" not "skipped"
    assert "ralph workflow first-run setup" in output_lower or "regenerated" in output_lower, (
        f"Expected banner or regenerate info in output, got: {result2.output}"
    )


def test_cli_init_shows_welcome_banner(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --init` should show the welcome banner when creating local configs."""
    runner = CliRunner()

    # chdir to the temp path
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "Ralph Workflow first-run setup" in result.output, (
        f"Expected 'Ralph Workflow first-run setup' in output, got: {result.output}"
    )
    for token in _RAW_MARKUP_TOKENS:
        assert token not in result.output, (
            f"Raw markup token {token!r} found in --init output: {result.output!r}"
        )


def test_cli_init_idempotent_no_banner_on_second_run(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Second `ralph --init` should not show the welcome banner."""
    runner = CliRunner()

    # chdir to the temp path
    monkeypatch.chdir(tmp_path)

    # First init
    result1 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result1.exit_code == 0

    # Second init - all configs already exist, should skip
    result2 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result2.exit_code == 0

    # Banner should not appear on second run
    assert "Ralph Workflow first-run setup" not in result2.output, (
        f"Welcome banner should not appear on second init. Output was: {result2.output}"
    )


def test_cli_init_creates_self_teaching_prompt_md(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --init should seed PROMPT.md with a concrete, self-teaching template."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"

    prompt_path = tmp_path / "PROMPT.md"
    assert prompt_path.exists(), "PROMPT.md was not created"

    content = prompt_path.read_text()
    assert "# Goal" in content, "PROMPT.md must contain '# Goal'"
    assert "## Acceptance criteria" in content, "PROMPT.md must contain '## Acceptance criteria'"
    assert "PROMPT.md" in content, "PROMPT.md must contain a self-referential mention of PROMPT.md"
    assert len(content.encode("utf-8")) > _MIN_PROMPT_SIZE_BYTES, (
        f"PROMPT.md must be at least {_MIN_PROMPT_SIZE_BYTES} bytes"
    )
    # Template must include an explanatory paragraph and end-of-template next-steps
    assert "ralph --diagnose" in content, (
        "PROMPT.md must contain 'ralph --diagnose' in the next-steps guidance"
    )
    assert "ralph`" in content or "`ralph`" in content or "run `ralph`" in content, (
        "PROMPT.md must contain a reference to running `ralph`"
    )


def test_cli_init_embeds_starter_sentinel_in_prompt_md(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --init embeds the sentinel; validate_required_inputs refuses the unedited file."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"

    prompt_path = tmp_path / "PROMPT.md"
    assert prompt_path.exists(), "PROMPT.md was not created"

    content = prompt_path.read_text()
    assert STARTER_PROMPT_SENTINEL in content, "Sentinel must be present in generated PROMPT.md"
    assert content.index(STARTER_PROMPT_SENTINEL) < content.index("# Goal"), (
        "Sentinel must appear before '# Goal' heading"
    )

    scope = MagicMock()
    scope.root = tmp_path
    with pytest.raises(PolicyValidationError):
        validate_required_inputs(scope)


def test_cli_first_run_panel_includes_what_is_ralph_pitch(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """First-run welcome panel must include the elevator-pitch sentence about the pipeline loop."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--check-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "planning" in result.output and "review" in result.output, (
        f"Expected pipeline loop description in first-run output, got: {result.output}"
    )
    for token in _RAW_MARKUP_TOKENS:
        assert token not in result.output, (
            f"Raw markup token {token!r} found in first-run output: {result.output!r}"
        )


def test_cli_init_fallback_next_steps_includes_docs_pointer(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Second `ralph --init` (fallback path) should include a docs pointer."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # First init creates everything
    result1 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result1.exit_code == 0

    # Second init hits the fallback path
    result2 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result2.exit_code == 0

    assert "Docs:" in result2.output, (
        f"Expected 'Docs:' docs pointer in fallback next-steps output, got: {result2.output}"
    )
