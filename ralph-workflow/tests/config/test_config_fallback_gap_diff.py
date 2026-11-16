from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger
from rich.console import Console

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.main import handle_check_config
from ralph.config import reset_fallback_warning_registry
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import RebaseStop
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy.portfolio import default_portfolio, load_portfolio_toml
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(10.0)


@pytest.fixture(autouse=True)
def _clean_fallback() -> None:
    reset_fallback_warning_registry()


def _seed_minimal_repo(repo: Path) -> None:
    agent_dir = repo / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")
    (repo / "PROMPT.md").write_text("prompt\n", encoding="utf-8")


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    return records, sink_id


def _setup_corrupt_global_agents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    _seed_minimal_repo(repo)

    global_dir = tmp_path / "global_config"
    global_dir.mkdir(parents=True)
    bad_agents = global_dir / "ralph-workflow-agents.toml"
    bad_agents.write_text("[agents\ninvalid toml = = =\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    monkeypatch.chdir(repo)
    return repo, bad_agents


def _assert_warn_contents(warn: str, bad_file: Path) -> None:
    assert str(bad_file) in warn
    assert "line 1" in warn.lower()
    assert "column" in warn.lower()
    assert "using built-in default" in warn
    assert "ralph --check-config" in warn


def test_warn_format_on_startup_and_workspace_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, bad_agents = _setup_corrupt_global_agents(monkeypatch, tmp_path)
    original_bytes = bad_agents.read_bytes()
    scope = WorkspaceScope(repo)

    reset_fallback_warning_registry()
    records, sink_id = _capture_warnings()
    try:
        cfg = load_config(workspace_scope=scope)
    finally:
        logger.remove(sink_id)

    assert cfg.agents == {}
    matching = [r for r in records if str(bad_agents) in r and "using built-in default" in r]
    assert len(matching) == 1
    _assert_warn_contents(matching[0], bad_agents)
    assert bad_agents.read_bytes() == original_bytes

    reset_fallback_warning_registry()
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(repo) as ctx:
            assert ctx.config.agents == {}
    finally:
        logger.remove(sink_id)

    matching = [r for r in records if str(bad_agents) in r and "using built-in default" in r]
    assert len(matching) == 1
    _assert_warn_contents(matching[0], bad_agents)


def test_warn_format_on_check_config_and_diagnose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _repo, bad_agents = _setup_corrupt_global_agents(monkeypatch, tmp_path)

    reset_fallback_warning_registry()
    console = Console(file=io.StringIO(), force_terminal=False)
    display_ctx = make_display_context(console=console)
    records, sink_id = _capture_warnings()
    try:
        rc = handle_check_config(
            config=None,
            cli_overrides={},
            check_config=True,
            console=console,
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert rc == 1
    matching = [r for r in records if str(bad_agents) in r and "using built-in default" in r]
    assert len(matching) == 1
    _assert_warn_contents(matching[0], bad_agents)

    reset_fallback_warning_registry()
    console = Console(file=io.StringIO(), force_terminal=False)
    display_ctx = make_display_context(console=console)
    records, sink_id = _capture_warnings()
    try:
        rc = diagnose_command(
            config_path=None,
            cli_overrides={},
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert rc != 0
    matching = [r for r in records if str(bad_agents) in r and "using built-in default" in r]
    assert len(matching) == 1
    _assert_warn_contents(matching[0], bad_agents)


def test_warn_deduplication_and_invalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _seed_minimal_repo(repo)

    local_pipeline = repo / ".agent" / "pipeline.toml"
    local_pipeline.write_text("[phases\ninvalid syntax = 1\n", encoding="utf-8")

    reset_fallback_warning_registry()
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(repo):
            pass
        first_count = sum(
            1 for r in records if str(local_pipeline) in r and "using built-in default" in r
        )
        assert first_count == 1

        with workspace_context(repo):
            pass
        second_count = sum(
            1 for r in records if str(local_pipeline) in r and "using built-in default" in r
        )
        assert second_count == 1

        local_pipeline.write_text("[phases\ninvalid syntax = 2\n", encoding="utf-8")
        with workspace_context(repo):
            pass
        third_count = sum(
            1 for r in records if str(local_pipeline) in r and "using built-in default" in r
        )
        assert third_count == 2

        reset_fallback_warning_registry()
        with workspace_context(repo):
            pass
        fourth_count = sum(
            1 for r in records if str(local_pipeline) in r and "using built-in default" in r
        )
        assert fourth_count == 3
    finally:
        logger.remove(sink_id)


def test_all_project_local_layers_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _seed_minimal_repo(repo)

    agent_dir = repo / ".agent"
    local_files = {
        "agents": (agent_dir / "agents.toml", "[agents\ninvalid = = =\n"),
        "pipeline": (agent_dir / "pipeline.toml", "[phases\ninvalid = = =\n"),
        "artifacts": (agent_dir / "artifacts.toml", "[artifacts\ninvalid = = =\n"),
        "mcp": (agent_dir / "mcp.toml", "[mcp_servers\ninvalid = = =\n"),
        "portfolio": (agent_dir / "portfolio.toml", "schema_version = [[[bad\n"),
    }

    for path, content in local_files.values():
        path.write_text(content, encoding="utf-8")

    payloads = {name: path.read_bytes() for name, (path, _) in local_files.items()}

    reset_fallback_warning_registry()
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(repo) as ctx:
            assert ctx is not None
            assert ctx.policy_bundle is not None
            portfolio = load_portfolio_toml(local_files["portfolio"][0])
            assert portfolio.schema_version == default_portfolio().schema_version
    finally:
        logger.remove(sink_id)

    for name, (path, _) in local_files.items():
        assert path.read_bytes() == payloads[name]
        matching = [r for r in records if str(path) in r and "using built-in default" in r]
        assert len(matching) == 1, f"Missing fallback warning for local {name}: {records}"
        assert "ralph --check-config" in matching[0]


def test_auto_integrate_resolver_proceeds_on_corrupt_agents_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _seed_minimal_repo(repo)

    global_dir = tmp_path / "global_config"
    global_dir.mkdir(parents=True)
    corrupt_agents = global_dir / "ralph-workflow-agents.toml"
    corrupt_agents.write_text("[agents\nbad syntax = = =\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    monkeypatch.chdir(repo)

    policy = load_policy(default_dir())
    cfg = load_config(workspace_scope=WorkspaceScope(repo))
    reg = AgentRegistry.from_config(cfg)

    mock_display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy,
        registry=reg,
        display=mock_display,
        config=cfg,
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(repo),
    )

    launched: list[dict[str, object]] = []

    def _fake_run_pipeline(**kwargs: object) -> ResolvedOutcome:
        launched.append(kwargs)
        return ResolvedOutcome()

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_outcome",
        _fake_run_pipeline,
    )

    stop = RebaseStop(
        sha="1234567",
        subject="feat: conflict",
        conflicted_files=("file.txt",),
        stop_index=1,
        stop_cap=3,
    )

    outcome = resolver(repo, "main", stop)
    assert isinstance(outcome, ResolvedOutcome)
    assert len(launched) == 1
    from ralph.config.models import UnifiedConfig

    launched_cfg = launched[0]["config"]
    assert isinstance(launched_cfg, UnifiedConfig)
    assert launched_cfg.agents == {}


def test_workspace_context_attribute_validation_with_corrupt_agents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    agent_dir = repo / ".agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = "invalid_non_int"\n', encoding="utf-8"
    )
    (agent_dir / "agents.toml").write_text("[agents\ninvalid syntax = = =\n", encoding="utf-8")
    (repo / "PROMPT.md").write_text("prompt", encoding="utf-8")

    with pytest.raises(WorkspaceContextError) as exc_info, workspace_context(repo):
        pass

    err = exc_info.value
    assert err.file == agent_dir / "ralph-workflow.toml"
    assert "developer_iters" in err.reason


def test_session_guard_redirects_import_time_constants() -> None:
    from ralph.config import loader as config_loader

    global_cfg = str(config_loader.GLOBAL_CONFIG_PATH)
    global_agents = str(config_loader.GLOBAL_AGENTS_CONFIG_PATH)

    assert (
        not global_cfg.startswith("/home/mistlight/.config/")
        or "autouse-home" in global_cfg
        or "session-home" in global_cfg
    )
    assert (
        not global_agents.startswith("/home/mistlight/.config/")
        or "autouse-home" in global_agents
        or "session-home" in global_agents
    )
