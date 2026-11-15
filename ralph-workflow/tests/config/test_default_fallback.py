from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from rich.console import Console

from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.main import handle_check_config
from ralph.config import reset_fallback_warning_registry
from ralph.config.loader import ConfigTomlError, load_config
from ralph.config.mcp_loader import load_mcp_config
from ralph.display.context import make_display_context
from ralph.policy.loader import (
    cached_default_agents_policy,
    cached_default_artifacts_policy,
    cached_default_pipeline_policy,
    load_policy_for_workspace_scope,
)
from ralph.project_policy.markers import PORTFOLIO_PATH
from ralph.project_policy.portfolio import default_portfolio, load_portfolio_toml
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.timeout_seconds(5.0)


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "fake_home"
    fake_xdg = fake_home / ".config"
    fake_xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))


@pytest.fixture(autouse=True)
def _reset_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    return records, sink_id


def _setup_workspace(root: Path) -> Path:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )
    (root / "PROMPT.md").write_text("Test prompt\n", encoding="utf-8")
    return root


def test_warn_dedup_key_contract(tmp_path: Path) -> None:
    f = tmp_path / "test.toml"
    f.write_text("invalid [", encoding="utf-8")
    fp = hashlib.sha256(f.read_bytes()).hexdigest()
    expected_key = (f.resolve(), fp)
    assert isinstance(expected_key[0], Path)
    assert expected_key[0].is_absolute()
    assert isinstance(expected_key[1], str)
    assert len(expected_key[1]) == 64


def test_missing_files_stay_silent(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        cfg = load_config(workspace_scope=scope)
        assert cfg.general.developer_iters == 5
        bundle = load_policy_for_workspace_scope(scope, config=cfg)
        assert bundle is not None
        with workspace_context(root) as ctx:
            assert ctx is not None
    finally:
        logger.remove(sink_id)
    fallback_records = [r for r in records if "using built-in default" in r]
    assert len(fallback_records) == 0


def test_valid_files_override_defaults_silently(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_text(
        "[loop_counters.development_analysis_iteration]\ndefault_max = 99\n",
        encoding="utf-8",
    )
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        bundle = load_policy_for_workspace_scope(scope)
        assert bundle.pipeline.loop_counters["development_analysis_iteration"].default_max == 99
    finally:
        logger.remove(sink_id)
    fallback_records = [r for r in records if "using built-in default" in r]
    assert len(fallback_records) == 0


def test_fallback_never_modifies_disk_file(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    bad_bytes = b"syntax_error = [[[\n"
    agents_file.write_bytes(bad_bytes)
    with workspace_context(root) as ctx:
        assert ctx is not None
    assert agents_file.read_bytes() == bad_bytes


def test_partial_file_never_merged_piecemeal(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_text(
        "[loop_counters.development_analysis_iteration]\ndefault_max = 99\n[broken\n=== syntax\n",
        encoding="utf-8",
    )
    scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
    bundle = load_policy_for_workspace_scope(scope)
    default_pipeline = cached_default_pipeline_policy()
    assert (
        bundle.pipeline.loop_counters["development_analysis_iteration"].default_max
        == default_pipeline.loop_counters["development_analysis_iteration"].default_max
    )


def test_agents_fallback_startup(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    bad_bytes = b"agents = [\nunclosed"
    agents_file.write_bytes(bad_bytes)
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        bundle = load_policy_for_workspace_scope(scope)
        default_agents = cached_default_agents_policy()
        assert bundle.agents.agent_chains.keys() == default_agents.agent_chains.keys()
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(agents_file) in r and "using built-in default" in r]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0] or "ralph --regenerate-config" in matching[0]


def test_agents_fallback_workspace_context_reload(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"[invalid_agents\n=== broken")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_agents = cached_default_agents_policy()
            assert (
                ctx.policy_bundle.agents.agent_chains.keys() == default_agents.agent_chains.keys()
            )
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(agents_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_agents_check_config(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"invalid agents syntax [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "agents.toml" in buf.getvalue()


def test_agents_diagnose(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"invalid agents syntax [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "agents.toml" in buf.getvalue()


def test_pipeline_fallback_startup(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_bytes(b"pipeline_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        bundle = load_policy_for_workspace_scope(scope)
        default_pipeline = cached_default_pipeline_policy()
        assert bundle.pipeline.entry_block == default_pipeline.entry_block
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(pipeline_file) in r and "using built-in default" in r]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


def test_pipeline_fallback_workspace_context_reload(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_bytes(b"pipeline_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_pipeline = cached_default_pipeline_policy()
            assert ctx.policy_bundle.pipeline.entry_block == default_pipeline.entry_block
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(pipeline_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_pipeline_check_config(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_bytes(b"pipeline_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "pipeline.toml" in buf.getvalue()


def test_pipeline_diagnose(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_bytes(b"pipeline_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "pipeline.toml" in buf.getvalue()


def test_artifacts_fallback_startup(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    artifacts_file = root / ".agent" / "artifacts.toml"
    artifacts_file.write_bytes(b"artifacts_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        bundle = load_policy_for_workspace_scope(scope)
        default_artifacts = cached_default_artifacts_policy()
        assert bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(artifacts_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_artifacts_fallback_workspace_context_reload(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    artifacts_file = root / ".agent" / "artifacts.toml"
    artifacts_file.write_bytes(b"artifacts_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_artifacts = cached_default_artifacts_policy()
            assert (
                ctx.policy_bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
            )
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(artifacts_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_artifacts_check_config(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    artifacts_file = root / ".agent" / "artifacts.toml"
    artifacts_file.write_bytes(b"artifacts_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "artifacts.toml" in buf.getvalue()


def test_artifacts_diagnose(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    artifacts_file = root / ".agent" / "artifacts.toml"
    artifacts_file.write_bytes(b"artifacts_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "artifacts.toml" in buf.getvalue()


def test_mcp_fallback_startup(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    mcp_file = root / ".agent" / "mcp.toml"
    mcp_file.write_bytes(b"mcp_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        scope = WorkspaceScope(root=root, local_config_path=root / ".agent" / "ralph-workflow.toml")
        mcp_cfg = load_mcp_config(workspace_scope=scope)
        assert mcp_cfg is not None
        assert mcp_cfg.mcp_servers == {}
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(mcp_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_mcp_fallback_workspace_context_reload(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    mcp_file = root / ".agent" / "mcp.toml"
    mcp_file.write_bytes(b"mcp_bad = [[[")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx.effective_mcp_plan is not None
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(mcp_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_mcp_check_config(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    mcp_file = root / ".agent" / "mcp.toml"
    mcp_file.write_bytes(b"mcp_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "mcp.toml" in buf.getvalue()


def test_mcp_diagnose(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    mcp_file = root / ".agent" / "mcp.toml"
    mcp_file.write_bytes(b"mcp_bad = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "mcp.toml" in buf.getvalue()


def test_portfolio_fallback_startup(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_bytes(b"broken portfolio = [[[")
    records, sink_id = _capture_warnings()
    try:
        port = load_portfolio_toml(portfolio_file)
        expected = default_portfolio()
        assert port.schema_version == expected.schema_version
        assert port.default_budget_seconds == expected.default_budget_seconds
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(portfolio_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_portfolio_fallback_workspace_context_reload(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    portfolio_file = root / PORTFOLIO_PATH
    portfolio_file.parent.mkdir(parents=True, exist_ok=True)
    portfolio_file.write_bytes(b"broken portfolio = [[[")
    records, sink_id = _capture_warnings()
    try:
        port = load_portfolio_toml(portfolio_file)
        assert port.schema_version == default_portfolio().schema_version
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(portfolio_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_portfolio_check_config(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    portfolio_file = root / PORTFOLIO_PATH
    portfolio_file.parent.mkdir(parents=True, exist_ok=True)
    portfolio_file.write_bytes(b"broken portfolio = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "policy-portfolio.toml" in buf.getvalue()


def test_portfolio_diagnose(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _setup_workspace(tmp_git_repo)
    monkeypatch.chdir(root)
    portfolio_file = root / PORTFOLIO_PATH
    portfolio_file.parent.mkdir(parents=True, exist_ok=True)
    portfolio_file.write_bytes(b"broken portfolio = [[[")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "policy-portfolio.toml" in buf.getvalue()


def test_ralph_workflow_toml_startup_fails_loudly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_bytes(b"invalid toml = [[[")
    scope = WorkspaceScope(root=root, local_config_path=cfg)
    with pytest.raises(ConfigTomlError):
        load_config(workspace_scope=scope)


def test_ralph_workflow_toml_workspace_context_fails_loudly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_bytes(b"invalid toml = [[[")
    with pytest.raises(WorkspaceContextError) as exc_info, workspace_context(root):
        pass
    assert exc_info.value.file == cfg


def test_ralph_workflow_toml_check_config_reports_error(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_workspace(tmp_git_repo)
    agent_dir = root / ".agent"
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_bytes(b"invalid toml = [[[")
    monkeypatch.chdir(root)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    exit_code = handle_check_config(None, {}, True, console=console)
    assert exit_code == 1
    assert "ralph-workflow.toml" in buf.getvalue()


def test_ralph_workflow_toml_diagnose_reports_error(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_workspace(tmp_git_repo)
    agent_dir = root / ".agent"
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_bytes(b"invalid toml = [[[")
    monkeypatch.chdir(root)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=1000)
    display_ctx = make_display_context(console=console)
    diagnose_command(None, {}, display_context=display_ctx)
    assert "ralph-workflow.toml" in buf.getvalue()


def test_warn_collapsed_across_reloads(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"invalid = [[[")
    records, sink_id = _capture_warnings()
    try:
        for _ in range(5):
            with workspace_context(root) as ctx:
                assert ctx is not None
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(agents_file) in r and "using built-in default" in r]
    assert len(matching) == 1


def test_warn_emitted_again_on_content_change(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"invalid 1 = [[[")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx is not None
        agents_file.write_bytes(b"invalid 2 = [[[")
        with workspace_context(root) as ctx:
            assert ctx is not None
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(agents_file) in r and "using built-in default" in r]
    assert len(matching) == 2


def test_warn_emitted_again_after_registry_reset(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    agents_file.write_bytes(b"invalid = [[[")
    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx is not None
        reset_fallback_warning_registry()
        with workspace_context(root) as ctx:
            assert ctx is not None
    finally:
        logger.remove(sink_id)
    matching = [r for r in records if str(agents_file) in r and "using built-in default" in r]
    assert len(matching) == 2


def test_repair_then_retry_strict_ralph_workflow_toml(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_bytes(b"invalid = [[[")
    (root / "PROMPT.md").write_text("Test prompt\n", encoding="utf-8")

    with pytest.raises(WorkspaceContextError) as exc_info, workspace_context(root):
        pass
    assert exc_info.value.file == cfg

    cfg.write_text(
        '[general]\ndeveloper_iters = 42\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )

    with workspace_context(root) as ctx:
        assert ctx.config.general.developer_iters == 42


def test_repair_then_retry_optional_file(tmp_path: Path) -> None:
    root = _setup_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_bytes(b"invalid = [[[")

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_pipeline = cached_default_pipeline_policy()
            assert ctx.policy_bundle.pipeline.entry_block == default_pipeline.entry_block
    finally:
        logger.remove(sink_id)
    assert any(str(pipeline_file) in r for r in records)

    pipeline_file.write_text(
        "[loop_counters.development_analysis_iteration]\ndefault_max = 77\n",
        encoding="utf-8",
    )

    records_after, sink_after = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert (
                ctx.policy_bundle.pipeline.loop_counters[
                    "development_analysis_iteration"
                ].default_max
                == 77
            )
    finally:
        logger.remove(sink_after)
    fallback_records = [r for r in records_after if "using built-in default" in r]
    assert len(fallback_records) == 0
