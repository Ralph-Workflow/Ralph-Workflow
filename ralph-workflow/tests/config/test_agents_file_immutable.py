"""Tests verifying immutability of the agents configuration file.

Two contracts are proven here:

* the agents file is never rewritten implicitly -- a normal load, a
  ``--diagnose`` run and a pipeline run leave it byte-identical;
* an EXPLICIT rewrite never damages the file it replaces -- a symlinked
  agents path stays a symlink (the real file behind it is updated), and an
  existing file keeps its permission mode.
"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from rich.console import Console
from typer.testing import CliRunner

from ralph.cli.main import app
from ralph.config.agents_file_writer import write_agents_toml
from ralph.config.enums import Verbosity
from ralph.config.loader import load_config, reset_retired_agent_key_warnings
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.effects import CommitEffect, Effect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.run_loop import run as pipeline_run
from ralph.policy.loader import default_dir, load_policy
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def test_loader_leaves_agents_file_immutable_when_migrating_retired_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loading config with agent_can_commit in agents file leaves sha256 unchanged and removes key in memory."""
    reset_retired_agent_key_warnings()
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    agents_path = config_home / "ralph-workflow-agents.toml"
    content = (
        "# Custom agent configuration\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "can_commit = true\n"
        'display_name = "Claude Code"\n'
    )
    agents_path.write_text(content, encoding="utf-8")
    initial_sha256 = hashlib.sha256(agents_path.read_bytes()).hexdigest()
    initial_dir_contents = set(config_home.iterdir())

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    # Returns config with the key removed
    assert config.agents["claude"].cmd == "claude"
    assert config.agents["claude"].display_name == "Claude Code"
    assert not hasattr(config.agents["claude"], "can_commit")

    # Leaves the file's sha256 unchanged and byte-identical
    final_sha256 = hashlib.sha256(agents_path.read_bytes()).hexdigest()
    assert final_sha256 == initial_sha256
    assert agents_path.read_text(encoding="utf-8") == content

    # Produces no extra files
    final_dir_contents = set(config_home.iterdir())
    assert final_dir_contents == initial_dir_contents

    # Deduplicated warning emitted
    assert any("regenerate-config" in r and "agent_can_commit" in r for r in records)

    # Second load does not produce duplicate warning
    records.clear()
    sink_id2 = logger.add(records.append, level="WARNING", format="{message}")
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)
    assert not any("regenerate-config" in r for r in records)


def test_normal_load_config_leaves_agents_file_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Normal load_config leaves ralph-workflow-agents.toml sha256 byte-identical."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(home_dir))

    agents_path = config_home / "ralph-workflow-agents.toml"
    content = (
        "# Custom agent configuration\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        'display_name = "Claude Code"\n'
        "\n"
        "[agents.opencode]\n"
        'cmd = "opencode"\n'
        'display_name = "OpenCode"\n'
    )
    agents_path.write_text(content, encoding="utf-8")
    initial_bytes = agents_path.read_bytes()
    initial_sha256 = hashlib.sha256(initial_bytes).hexdigest()
    initial_dir_contents = set(config_home.iterdir())

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("[general]\nverbosity = 0\n", encoding="utf-8")

    config = load_config(config_path=local_path)

    assert config.agents["claude"].cmd == "claude"
    assert config.agents["opencode"].cmd == "opencode"

    final_bytes = agents_path.read_bytes()
    final_sha256 = hashlib.sha256(final_bytes).hexdigest()
    assert final_bytes == initial_bytes
    assert final_sha256 == initial_sha256
    assert set(config_home.iterdir()) == initial_dir_contents
    assert not (config_home / "ralph-workflow-agents.toml.bak").exists()
    assert not any(
        p.name.startswith(".ralph-workflow-agents.toml.tmp") for p in config_home.iterdir()
    )


def test_diagnose_leaves_agents_file_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Running ralph --diagnose leaves ralph-workflow-agents.toml sha256 byte-identical."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(home_dir))

    agents_path = config_home / "ralph-workflow-agents.toml"
    content = (
        "# Custom agent configuration\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        'display_name = "Claude Code"\n'
    )
    agents_path.write_text(content, encoding="utf-8")
    initial_bytes = agents_path.read_bytes()
    initial_sha256 = hashlib.sha256(initial_bytes).hexdigest()

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    runner_cli = CliRunner()
    result = runner_cli.invoke(app, ["--diagnose"])
    assert result.exit_code in (0, 1)

    final_bytes = agents_path.read_bytes()
    final_sha256 = hashlib.sha256(final_bytes).hexdigest()
    assert final_bytes == initial_bytes
    assert final_sha256 == initial_sha256
    assert not (config_home / "ralph-workflow-agents.toml.bak").exists()
    assert not any(
        p.name.startswith(".ralph-workflow-agents.toml.tmp") for p in config_home.iterdir()
    )


def test_pipeline_run_leaves_agents_file_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pipeline run with stubbed agent execution leaves ralph-workflow-agents.toml byte-identical."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(home_dir))

    agents_path = config_home / "ralph-workflow-agents.toml"
    content = (
        "# Custom agent configuration\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        'display_name = "Claude Code"\n'
    )
    agents_path.write_text(content, encoding="utf-8")
    initial_bytes = agents_path.read_bytes()
    initial_sha256 = hashlib.sha256(initial_bytes).hexdigest()
    initial_dir_contents = set(config_home.iterdir())

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "PROMPT.md").write_text("# Test Prompt\n", encoding="utf-8")
    agent_dir = repo_dir / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\nauto_integrate_enabled = false\n",
        encoding="utf-8",
    )

    scope = WorkspaceScope(repo_dir)
    config = load_config(
        config_path=agent_dir / "ralph-workflow.toml",
        workspace_scope=scope,
    )

    bundle = load_policy(default_dir())
    # Strip invocation gates so phases execute deterministically
    phases = dict(bundle.pipeline.phases)
    for name, phase_def in phases.items():
        if phase_def.invocation_gate is not None:
            phases[name] = phase_def.model_copy(update={"invocation_gate": None})
    bundle = bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": phases})}
    )

    invoker = MockAgentInvoker(MemoryWorkspace())

    def fake_execute_effect(
        effect: Effect, _config: UnifiedConfig, _scope: WorkspaceScope
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        raise AssertionError(f"Unexpected effect: {effect}")

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _p: bundle)
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *a, **kw: "noop")
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner, "phase_event_after_agent_run", lambda **kw: PipelineEvent.AGENT_SUCCESS
    )
    monkeypatch.setattr("ralph.pipeline.checkpoint.save", lambda *a, **kw: None)

    console = Console(record=True, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    monkeypatch.setattr(runner, "make_display_context", lambda **kw: ctx)

    rc = pipeline_run(
        config,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )

    assert rc in (0, 1)
    final_bytes = agents_path.read_bytes()
    final_sha256 = hashlib.sha256(final_bytes).hexdigest()
    assert final_bytes == initial_bytes
    assert final_sha256 == initial_sha256
    assert set(config_home.iterdir()) == initial_dir_contents
    assert not (config_home / "ralph-workflow-agents.toml.bak").exists()
    assert not any(
        p.name.startswith(".ralph-workflow-agents.toml.tmp") for p in config_home.iterdir()
    )


def test_write_through_symlinked_agents_path_preserves_symlink_and_updates_real_file(
    tmp_path: Path,
) -> None:
    """A write through a symlinked agents path keeps the symlink and updates the real file."""
    real_file = tmp_path / "real-agents.toml"
    link_path = tmp_path / "ralph-workflow-agents.toml"
    real_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")
    link_path.symlink_to(real_file)

    write_agents_toml(link_path, '[agents.claude]\ncmd = "claude-new"\n', trigger="symlink-proof")

    # The symlink is still a symlink pointing at the same real file.
    assert link_path.is_symlink()
    assert link_path.readlink() == real_file
    assert link_path.resolve() == real_file.resolve()

    # The real file behind the link carries the new content.
    assert real_file.read_text(encoding="utf-8") == '[agents.claude]\ncmd = "claude-new"\n'


def test_rewrite_preserves_existing_agents_file_mode(tmp_path: Path) -> None:
    """A rewrite keeps the mode of an existing agents file (0644 stays 0644)."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")
    target_path.chmod(0o644)
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644

    write_agents_toml(target_path, '[agents.claude]\ncmd = "claude-new"\n', trigger="mode-proof")

    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644
    assert target_path.read_text(encoding="utf-8") == '[agents.claude]\ncmd = "claude-new"\n'
