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
    assert "Ralph Workflow first-run setup" not in result2.output, (
        f"Welcome banner should not appear on second init. Output was: {result2.output}"
    )


def test_cli_init_does_not_create_local_main_config_when_global_exists(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --init` should keep using the global main config by default."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--init"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert (tmp_path / ".config" / "ralph-workflow.toml").exists()
    assert not (tmp_path / ".agent" / "ralph-workflow.toml").exists()
    assert (tmp_path / ".agent" / "mcp.toml").exists()
    assert (tmp_path / ".agent" / "pipeline.toml").exists()
    assert (tmp_path / ".agent" / "artifacts.toml").exists()



def test_cli_generate_local_config_creates_local_main_override(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit local-config command should create `.agent/ralph-workflow.toml`."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    init_result = runner.invoke(app, ["--init"], catch_exceptions=False)
    assert init_result.exit_code == 0, (
        f"Expected init exit 0, got {init_result.exit_code}: {init_result.output}"
    )
    assert not (tmp_path / ".agent" / "ralph-workflow.toml").exists()

    result = runner.invoke(app, ["--generate-local-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert (tmp_path / ".agent" / "ralph-workflow.toml").exists()



def test_cli_init_in_linked_worktree_reuses_main_worktree_config_root(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --init` in a linked worktree should seed defaults in the main checkout only."""
    runner = CliRunner()
    main_repo = tmp_path / "main"
    linked_worktree = tmp_path / "feature-worktree"
    main_repo.mkdir()
    linked_worktree.mkdir()
    monkeypatch.chdir(linked_worktree)
    monkeypatch.setattr(
        "ralph.workspace.scope.find_repo_root",
        lambda _start=None: linked_worktree,
    )
    monkeypatch.setattr(
        "ralph.workspace.scope.find_main_worktree_root",
        lambda _start=None: main_repo,
    )

    result = runner.invoke(app, ["--init"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert (linked_worktree / "PROMPT.md").exists()
    assert (main_repo / ".agent" / "pipeline.toml").exists()
    assert (main_repo / ".agent" / "artifacts.toml").exists()
    assert (main_repo / ".agent" / "mcp.toml").exists()
    assert not (linked_worktree / ".agent" / "pipeline.toml").exists()
    assert not (linked_worktree / ".agent" / "artifacts.toml").exists()
    assert not (linked_worktree / ".agent" / "mcp.toml").exists()



def test_cli_generate_local_config_in_linked_worktree_targets_main_checkout(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`ralph --generate-local-config` in a linked worktree should write to the main checkout."""
    runner = CliRunner()
    main_repo = tmp_path / "main"
    linked_worktree = tmp_path / "feature-worktree"
    main_repo.mkdir()
    linked_worktree.mkdir()
    monkeypatch.chdir(linked_worktree)
    monkeypatch.setattr(
        "ralph.workspace.scope.find_repo_root",
        lambda _start=None: linked_worktree,
    )
    monkeypatch.setattr(
        "ralph.workspace.scope.find_main_worktree_root",
        lambda _start=None: main_repo,
    )

    result = runner.invoke(app, ["--generate-local-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert (main_repo / ".agent" / "ralph-workflow.toml").exists()
    assert not (linked_worktree / ".agent" / "ralph-workflow.toml").exists()


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


def test_cli_first_run_panel_includes_getting_started_pointer(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """First-run welcome panel must point new users to getting-started.md."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--check-config"], catch_exceptions=False)

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    assert "getting-started" in result.output, (
        f"Expected 'getting-started' reference in first-run welcome panel, got: {result.output}"
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


def test_cli_init_fallback_next_steps_includes_getting_started(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Second `ralph --init` (fallback path) should reference getting-started.md."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # First init creates everything
    result1 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result1.exit_code == 0

    # Second init hits the fallback path
    result2 = runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    assert result2.exit_code == 0

    assert "getting-started" in result2.output, (
        f"Expected getting-started reference in fallback next-steps, got: {result2.output}"
    )


def test_cli_run_in_fresh_dir_shows_init_hint(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bare `ralph` in a directory with no PROMPT.md and no .agent shows a friendly init hint."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # Ensure completely fresh directory (no PROMPT.md, no .agent)
    assert not (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / ".agent").exists()

    result = runner.invoke(app, [], catch_exceptions=False)

    assert result.exit_code == 2, (  # noqa: PLR2004
        f"Expected exit code 2 (preflight), got {result.exit_code}: {result.output}"
    )
    assert "not initialized" in result.output.lower(), (
        f"Expected 'not initialized' in output, got: {result.output}"
    )
    assert "ralph --init" in result.output, (
        f"Expected 'ralph --init' in output, got: {result.output}"
    )
    assert "getting-started" in result.output, (
        f"Expected 'getting-started' in output, got: {result.output}"
    )


def test_cli_run_with_only_prompt_shows_init_hint(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bare `ralph` with only PROMPT.md but no .agent still surfaces `ralph --init` guidance.

    Specifically verifies the validation-error path (PROMPT.md exists but is not configured):
    the output must explain the problem, point to `ralph --init`, and NOT show the
    fresh-state 'not initialized' panel (which requires both PROMPT.md and .agent to be absent).
    """
    runner = CliRunner()

    # Initialize global configs first so the first-run welcome does not contaminate the output.
    init_dir = tmp_path / "global_init"
    init_dir.mkdir()
    monkeypatch.chdir(init_dir)
    runner.invoke(app, ["--check-config"], catch_exceptions=False)

    # Switch to a fresh workspace with only an empty PROMPT.md (no .agent).
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROMPT.md").write_text("")
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, [], catch_exceptions=False)

    # Exit 2 = preflight validation failure
    assert result.exit_code == 2, (  # noqa: PLR2004
        f"Expected exit code 2 (preflight), got {result.exit_code}: {result.output}"
    )
    # Validation error message from validate_required_inputs references ralph --init
    assert "ralph --init" in result.output, (
        f"Expected 'ralph --init' guidance in output, got: {result.output}"
    )
    # Must mention PROMPT.md to explain what is wrong
    assert "PROMPT.md" in result.output, (
        f"Expected 'PROMPT.md' to be mentioned in output, got: {result.output}"
    )
    # Must NOT show the "not initialized" fresh-state panel — PROMPT.md exists so
    # we are in the validation-error path, not the completely-uninitialized path.
    assert "not initialized" not in result.output.lower(), (
        "The 'not initialized' fresh-state panel must NOT appear when PROMPT.md exists; "
        f"got: {result.output}"
    )
