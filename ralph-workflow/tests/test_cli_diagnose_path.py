from __future__ import annotations

import gc
import shutil as _shutil
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import diagnose as diagnose_module
from ralph.cli.commands.diagnose import build_next_steps, check_agents
from ralph.cli.main import app
from ralph.config import bootstrap as bootstrap_module
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

pytestmark = [pytest.mark.timeout_seconds(5)]


class BootstrapResultStub:
    def __init__(
        self,
        *,
        action: str = "skipped",
        path: Path | None = None,
        backup: Path | None = None,
    ) -> None:
        self.action = action
        self.path = path if path is not None else Path("/dev/null")
        self.backup = backup


KNOWN_DEFAULT_AGENTS = ("claude", "opencode")


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "HOME": str(tmp_path / ".home"),
    }
    for key, val in env.items():
        monkeypatch.setenv(key, val)
        Path(val).mkdir(parents=True, exist_ok=True)
    return env


def _stub_init_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    skipped = BootstrapResultStub(action="skipped")

    monkeypatch.setattr(bootstrap_module, "ensure_global_config", lambda *a, **kw: skipped)
    monkeypatch.setattr(bootstrap_module, "ensure_global_mcp_config", lambda *a, **kw: skipped)
    monkeypatch.setattr(
        bootstrap_module, "ensure_global_policy_configs", lambda *a, **kw: [skipped]
    )
    monkeypatch.setattr(
        bootstrap_module,
        "ensure_local_support_configs",
        lambda *_args, **_kw: [skipped],
    )
    monkeypatch.setattr(bootstrap_module, "ensure_local_main_config", lambda *_args, **_kw: skipped)
    monkeypatch.setattr(
        bootstrap_module,
        "_ensure_default_gitignore",
        lambda *_args, **_kw: None,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_ensure_default_git_exclude",
        lambda *_args, **_kw: None,
    )


@pytest.mark.timeout_seconds(3)
def test_diagnose_renders_agent_path_column(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del clean_env
    _stub_init_bootstrap(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    gc.collect(0)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    lines = output.splitlines()

    path_tokens = ("on PATH", "missing")
    found = any(
        any(agent in line for agent in KNOWN_DEFAULT_AGENTS)
        and any(token in line for token in path_tokens)
        for line in lines
    )
    assert found, (
        "Expected at least one output line to contain both an agent name "
        f"({', '.join(KNOWN_DEFAULT_AGENTS)}) and a PATH status "
        f"({', '.join(path_tokens)}).\nFull output:\n{output}"
    )


def test_diagnose_alias_with_different_display_name_shows_correct_path_status(
    clean_env: dict[str, str],
) -> None:
    del clean_env
    custom_agent = AgentConfig(
        cmd="python",
        output_flag="--json",
        json_parser=JsonParserType.GENERIC,
        display_name="My Python Agent",
    )
    cfg = UnifiedConfig()
    registry = AgentRegistry.from_config(cfg)
    registry.register("my-alias", custom_agent)

    with patch(
        "shutil.which",
        side_effect=lambda cmd: "/usr/bin/python" if cmd == "python" else None,
    ):
        results = check_agent_availability(registry)

    result_map = dict(results)
    assert "my-alias" in result_map, (
        f"Expected 'my-alias' (registry key) in availability results, got: {list(result_map)}"
    )
    assert result_map["my-alias"] == "available", (
        f"Expected 'my-alias' to be 'available' (python on PATH), got: {result_map['my-alias']}"
    )
    assert "My Python Agent" not in result_map, (
        "display_name 'My Python Agent' must not be used as key in availability results"
    )


def test_diagnose_alias_path_status_rendered_in_cli(
    clean_env: dict[str, str],
) -> None:
    del clean_env
    custom_agent = AgentConfig(
        cmd="python",
        output_flag="--json",
        json_parser=JsonParserType.GENERIC,
        display_name="My Python Agent",
    )

    cfg = UnifiedConfig()
    base_registry = AgentRegistry.from_config(cfg)
    base_registry.register("my-alias", custom_agent)

    buf = StringIO()
    buf_console = Console(file=buf, force_terminal=False, theme=RALPH_THEME)
    ctx = make_display_context(console=buf_console, env={})

    with (
        patch("ralph.cli.commands.diagnose.load_config", return_value=cfg),
        patch(
            "ralph.cli.commands.diagnose.AgentRegistry.from_config",
            return_value=base_registry,
        ),
    ):
        check_agents(None, display_context=ctx)

    output = buf.getvalue()
    lines = output.splitlines()

    alias_lines = [line for line in lines if "my-alias" in line]
    assert alias_lines, f"Expected 'my-alias' row in diagnose output.\nFull output:\n{output}"

    alias_line = alias_lines[0]
    assert "on PATH" in alias_line or "missing" in alias_line, (
        f"Expected PATH status on 'my-alias' row, got: {alias_line!r}"
    )

    python_on_path = _shutil.which("python") is not None
    if python_on_path:
        assert "on PATH" in alias_line, (
            f"python is on PATH but 'my-alias' row shows wrong status: {alias_line!r}\n"
            f"Full output:\n{output}"
        )


def test_diagnose_preflight_keeps_literal_config_sections() -> None:
    output = StringIO()
    ctx = make_display_context(
        console=Console(file=output, force_terminal=False, theme=RALPH_THEME, width=200), env={}
    )
    display = diagnose_module.resolve_active_display(None, ctx)
    diagnose_module._emit_simple_table(
        display,
        "Pre-flight Validation",
        [
            (
                "Policy validation",
                diagnose_module._status_text(
                    "Failed",
                    "FIX: install a CLI or edit [agent_chains] in ralph-workflow.toml and "
                    "[agents.<name>] in ralph-workflow-agents.toml.",
                    "theme.status.error",
                ),
            )
        ],
    )
    rendered = output.getvalue()
    assert "[agent_chains]" in rendered
    assert "[agents.<name>]" in rendered


def test_diagnose_missing_agent_shows_install_link_and_config_section(
    clean_env: dict[str, str],
) -> None:
    del clean_env
    agent = AgentConfig(cmd="codex")
    registry = AgentRegistry.from_config(UnifiedConfig())
    registry.register("codex", agent)
    output = StringIO()
    ctx = make_display_context(
        console=Console(file=output, force_terminal=False, theme=RALPH_THEME, width=200), env={}
    )

    with (
        patch("ralph.cli.commands.diagnose.load_config", return_value=UnifiedConfig()),
        patch("ralph.cli.commands.diagnose.AgentRegistry.from_config", return_value=registry),
        patch(
            "ralph.cli.commands.diagnose.check_agent_availability",
            return_value=[("codex", "missing")],
        ),
    ):
        assert check_agents(None, display_context=ctx) is True

    rendered = output.getvalue()
    assert "https://github.com/openai/codex" in rendered
    assert "edit [agents.codex]" in rendered
    assert "ralph-workflow-agents.toml" in rendered


def test_build_next_steps_no_prompt_recommends_init() -> None:
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=False,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "ralph --init" in combined, (
        f"Expected 'ralph --init' in next-steps when prompt_exists=False, got: {steps}"
    )


def test_build_next_steps_sentinel_recommends_edit() -> None:
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=True,
    )
    combined = " ".join(steps)
    sentinel_mentioned = (
        "starter-prompt" in combined.lower()
        or "sentinel" in combined.lower()
        or "marker" in combined.lower()
    )
    assert sentinel_mentioned, (
        "Expected sentinel/marker reference in next-steps "
        f"when prompt_has_sentinel=True, got: {steps}"
    )
    assert "PROMPT.md" in combined, (
        f"Expected 'PROMPT.md' in next-steps when prompt_has_sentinel=True, got: {steps}"
    )


def test_build_next_steps_agent_missing_recommends_install() -> None:
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=True,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "claude" in combined.lower() or "opencode" in combined.lower(), (
        f"Expected agent name in next-steps when agent_missing=True, got: {steps}"
    )
    assert "install" in combined.lower(), (
        f"Expected 'install' in next-steps when agent_missing=True, got: {steps}"
    )


def test_build_next_steps_agent_missing_includes_agy_url() -> None:
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=True,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "https://github.com/google-antigravity/antigravity-cli" in combined


def test_build_next_steps_validation_failed_recommends_regenerate() -> None:
    steps = build_next_steps(
        validation_ok=False,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "regenerate-config" in combined or "validation failed" in combined.lower(), (
        f"Expected regenerate-config hint when validation_ok=False, got: {steps}"
    )


def test_build_next_steps_all_ok_recommends_run() -> None:
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "ralph" in combined, (
        f"Expected 'ralph' run recommendation in next-steps when all ok, got: {steps}"
    )


@pytest.mark.timeout_seconds(5)
def test_diagnose_flags_unknown_config_field(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del clean_env
    _stub_init_bootstrap(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "[general]\nverbosity = 2\nwrokflow = { checkpoint_enabled = true }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    assert "Config loaded" in output, (
        f"Expected 'Config loaded' status row in --diagnose output, got: {output}"
    )
    assert "general.wrokflow" in output, (
        f"Expected the typo'd 'general.wrokflow' field to be surfaced in "
        f"--diagnose output, got: {output}"
    )
    assert "workflow" in output, (
        f"Expected the canonical 'workflow' suggestion in --diagnose output, got: {output}"
    )


@pytest.mark.timeout_seconds(5)
def test_diagnose_flags_unknown_field_in_propagated_config(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del clean_env
    _stub_init_bootstrap(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    propagated_path = tmp_path / "parent" / ".agent" / "ralph-workflow.toml"
    propagated_path.parent.mkdir(parents=True)
    propagated_path.write_text(
        "[general]\nwrokflow = { checkpoint_enabled = true }\n",
        encoding="utf-8",
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text("", encoding="utf-8")
    from ralph.workspace.scope import WorkspaceScope

    monkeypatch.setattr(
        "ralph.cli.commands.diagnose.resolve_workspace_scope",
        lambda: WorkspaceScope(
            root=tmp_path,
            local_config_path=local_path,
            propagated_config_paths=(propagated_path,),
        ),
    )

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    assert "general.wrokflow" in output, (
        f"Expected the propagated-ancestor typo 'general.wrokflow' to be "
        f"surfaced in --diagnose output, got: {output}"
    )


@pytest.mark.timeout_seconds(5)
def test_diagnose_next_steps_panel_rendered_in_cli(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del clean_env
    _stub_init_bootstrap(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    gc.collect(0)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    assert "Next steps" in result.output, (
        f"Expected 'Next steps' panel in --diagnose output, got: {result.output}"
    )
    assert "getting-started" in result.output, (
        f"Expected 'getting-started' pointer in --diagnose Next steps panel, got: {result.output}"
    )


@pytest.mark.timeout_seconds(5)
def test_diagnose_next_steps_points_to_getting_started_when_no_prompt(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del clean_env
    _stub_init_bootstrap(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["--check-config"], catch_exceptions=False)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    assert "ralph --init" in result.output, (
        f"Expected 'ralph --init' in Next steps when PROMPT.md absent, got: {result.output}"
    )
