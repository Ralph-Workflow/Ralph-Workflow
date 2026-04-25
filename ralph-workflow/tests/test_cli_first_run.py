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
    """First invocation should show the 'Ralph first-run setup' banner and ASCII banner."""
    runner = CliRunner()

    # chdir to the temp path so .agent is created there
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--check-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "Ralph first-run setup" in result.output, (
        f"Expected 'Ralph first-run setup' in output, got: {result.output}"
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
    assert "Ralph first-run setup" in result1.output
    assert "Ralph first-run setup" not in result2.output, (
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
    assert "ralph first-run setup" in output_lower or "regenerated" in output_lower, (
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
    assert "Ralph first-run setup" in result.output, (
        f"Expected 'Ralph first-run setup' in output, got: {result.output}"
    )
    for token in _RAW_MARKUP_TOKENS:
        assert token not in result.output, (
            f"Raw markup token {token!r} found in --init output: {result.output!r}"
        )


def test_cli_init_without_label_has_no_deprecation_warning(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --init` with no label should stay warning-free."""
    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "deprecated" not in result.output.lower(), result.output


def test_cli_init_with_default_label_emits_deprecation_warning(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --init default` should warn that labels are deprecated."""
    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "deprecated" in result.output.lower(), result.output


def test_cli_init_with_arbitrary_label_emits_deprecation_warning(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Arbitrary `--init` labels should warn that the label is ignored."""
    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init", "starter-template"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "deprecated" in result.output.lower(), result.output
    assert "starter-template" in result.output, result.output
    assert "ignored" in result.output.lower(), result.output


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
    assert "Ralph first-run setup" not in result2.output, (
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
