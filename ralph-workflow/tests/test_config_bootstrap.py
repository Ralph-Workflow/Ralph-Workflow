"""Black-box tests for ralph.config.bootstrap and related first-run behavior."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import ralph.config.loader as loader_module
import ralph.policy
from ralph.config.bootstrap import (
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_local_configs,
    regenerate_all,
    resolve_global_config_dir,
)
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    load_policy,
)
from ralph.workspace.scope import WorkspaceScope

_EXPECTED_LOCAL_CONFIG_COUNT = 4
_EXPECTED_REGENERATE_COUNT = 7


def test_ensure_global_config_creates_when_absent(tmp_path: Path) -> None:
    result = ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_global_config_idempotent(tmp_path: Path) -> None:
    ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"
    mtime_after_first = target.stat().st_mtime

    result2 = ensure_global_config(tmp_path)
    assert result2.action == "skipped"
    assert target.stat().st_mtime == mtime_after_first


def test_ensure_global_config_force_creates_backup(tmp_path: Path) -> None:
    target = tmp_path / "ralph-workflow.toml"
    target.write_text("# MINE", encoding="utf-8")

    result = ensure_global_config(tmp_path, force=True)

    backup = target.with_suffix(".toml.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "# MINE"
    assert target.read_text(encoding="utf-8").startswith("#")
    assert result.action == "regenerated"
    assert result.backup == backup


def test_ensure_global_mcp_config_creates(tmp_path: Path) -> None:
    result = ensure_global_mcp_config(tmp_path)
    target = tmp_path / "ralph-workflow-mcp.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_local_configs_creates_all_five(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    results = ensure_local_configs(agent_dir)

    expected_files = (
        "ralph-workflow.toml",
        "mcp.toml",
        "pipeline.toml",
        "artifacts.toml",
    )
    for fname in expected_files:
        assert (agent_dir / fname).exists(), f"{fname} should exist"
        assert isinstance(tomllib.loads((agent_dir / fname).read_text()), dict)

    assert len(results) == _EXPECTED_LOCAL_CONFIG_COUNT
    assert all(r.action == "created" for r in results)

    # Verify result list contains all policy files
    result_names = [r.path.name for r in results]
    for fname in expected_files:
        assert fname in result_names, f"{fname} should be in results"


def test_ensure_local_configs_includes_runtime_policy_files(tmp_path: Path) -> None:
    """Verify the default runtime policy TOMLs are in the result list."""
    agent_dir = tmp_path / ".agent"
    results = ensure_local_configs(agent_dir)

    policy_files = {"pipeline.toml", "artifacts.toml"}
    result_names = {r.path.name for r in results}
    assert policy_files.issubset(result_names), (
        f"Policy files {policy_files} not all in results {result_names}"
    )
    assert "agents.toml" not in result_names


def test_regenerate_all_force_creates_backups(tmp_path: Path) -> None:
    global_dir = tmp_path / "g"
    agent_dir = tmp_path / "a"
    global_dir.mkdir()
    agent_dir.mkdir()

    sentinel = "# SENTINEL"
    (global_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (global_dir / "ralph-workflow-mcp.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "mcp.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "agents.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text(sentinel, encoding="utf-8")

    results = regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    assert len(results) == _EXPECTED_REGENERATE_COUNT
    assert all(r.action == "regenerated" for r in results)

    for result in results:
        assert result.backup is not None
        assert result.backup.exists()
        assert result.backup.read_text(encoding="utf-8") == sentinel
        assert isinstance(tomllib.loads(result.path.read_text()), dict)


def test_resolve_global_config_dir_honors_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_global_config_dir() == tmp_path


def test_resolve_global_config_dir_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert resolve_global_config_dir() == Path.home() / ".config"


def test_ensure_global_config_round_trips_through_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_global_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    scope = WorkspaceScope(tmp_path)
    cfg = loader_module.load_config(workspace_scope=scope)
    assert cfg.general.verbosity is not None


def test_bundled_global_template_parses_as_valid_toml() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow.toml"
    content = template.read_text(encoding="utf-8")
    result = tomllib.loads(content)
    assert isinstance(result, dict)


def test_bundled_mcp_template_describes_broad_multimodal_support() -> None:
    """Default mcp.toml must describe the broad multimodal surface, not image-only."""
    template = Path(ralph.policy.__file__).parent / "defaults" / "mcp.toml"
    content = template.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "read_image" in content
    assert "compatibility" in content
    # Must not imply image-only support
    assert "Multimodal image reading support" not in content


def test_local_template_defines_active_runtime_drain_bindings() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    drains = data["agent_drains"]

    for drain_name, chain_name in (
        ("planning", "planning"),
        ("development", "development"),
        ("development_analysis", "analysis"),
        ("development_commit", "commit"),
    ):
        assert drains.get(drain_name) == chain_name, (
            f"Expected active local template drain binding {drain_name!r} -> {chain_name!r}"
        )


def test_local_template_defines_active_agent_chain_defaults() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    chains = data["agent_chains"]

    assert chains["planning"] == ["claude/opus"]
    assert chains["development"] == [
        "opencode/minimax/MiniMax-M2.7-highspeed",
        "codex",
        "claude/sonnet",
    ]
    assert chains["analysis"] == ["opencode/openai/gpt-5.4"]
    assert chains["commit"] == ["claude/haiku"]

    # Review-era chains must not appear in the active default local template.
    review_era_chains = {"review", "fix"}
    assert not review_era_chains.intersection(chains), (
        f"Review-era chains still active in local template: "
        f"{review_era_chains.intersection(chains)}"
    )


def test_local_template_does_not_expose_review_era_drain_bindings() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    drains = data["agent_drains"]

    review_era_drains = {"review", "review_analysis", "review_commit", "fix"}
    assert not review_era_drains.intersection(drains), (
        f"Review-era drains still active in local template: "
        f"{review_era_drains.intersection(drains)}"
    )


def test_local_template_mentions_ccs_alternative() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    content = template.read_text(encoding="utf-8")
    assert 'ccs/work' in content


def test_generated_local_template_validates_against_bundled_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(ralph.policy.__file__).parent / "defaults"
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        (defaults_dir / "ralph-workflow-local.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", agent_dir / "ralph-workflow.toml")
    config = loader_module.load_config(workspace_scope=WorkspaceScope(tmp_path))
    bundle = load_policy(agent_dir, config=config)

    for phase_name, phase_def in bundle.pipeline.phases.items():
        if phase_def.role == "terminal":
            continue
        assert phase_def.drain in bundle.agents.agent_drains, (
            f"Generated local template left phase {phase_name!r} drain {phase_def.drain!r} unbound"
        )


def test_generated_local_template_missing_required_drain_fails_policy_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(ralph.policy.__file__).parent / "defaults"
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    broken = (defaults_dir / "ralph-workflow-local.toml").read_text(encoding="utf-8").replace(
        'development_commit = "commit"\n', ''
    )
    (agent_dir / "ralph-workflow.toml").write_text(broken, encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", agent_dir / "ralph-workflow.toml")
    config = loader_module.load_config(workspace_scope=WorkspaceScope(tmp_path))
    with pytest.raises(LoaderPolicyValidationError, match="unbound drains"):
        load_policy(agent_dir, config=config)


def test_ensure_local_configs_bootstraps_a_valid_policy_bundle(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    ensure_local_configs(agent_dir)

    bundle = load_policy(agent_dir)

    assert bundle.pipeline.entry_phase == "planning"
    assert bundle.pipeline.phases["development"].parallelization is not None


def test_regenerate_all_bootstraps_a_valid_policy_bundle(tmp_path: Path) -> None:
    global_dir = tmp_path / "g"
    agent_dir = tmp_path / "a"
    global_dir.mkdir()
    agent_dir.mkdir()

    regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    bundle = load_policy(agent_dir)

    assert bundle.pipeline.terminal_phase == "complete"
    assert bundle.pipeline.phases["development"].parallelization is not None
