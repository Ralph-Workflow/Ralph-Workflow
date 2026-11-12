from __future__ import annotations

import io
import stat
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger
from rich.console import Console

from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.main import handle_check_config
from ralph.config import reset_fallback_warning_registry
from ralph.display.context import make_display_context
from ralph.policy.loader import (
    cached_default_agents_policy,
    cached_default_artifacts_policy,
    cached_default_pipeline_policy,
)
from ralph.project_policy.portfolio import default_portfolio, load_portfolio_toml
from ralph.workspace.context import workspace_context

pytestmark = pytest.mark.timeout_seconds(5.0)


@pytest.fixture(autouse=True)
def _isolate_home_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    fake_home = tmp_path_factory.mktemp("fake_home")
    fake_xdg = fake_home / ".config"
    fake_xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))


@pytest.fixture(autouse=True)
def _clean_fallback_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def _capture_warnings() -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")
    return records, sink_id


def _setup_base_workspace(root: Path) -> Path:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    cfg = agent_dir / "ralph-workflow.toml"
    cfg.write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "claude"\ncommit = "claude"\n',
        encoding="utf-8",
    )
    return root


# ==============================================================================
# Cell: workspace_context reload x policy (agents.toml)
# ==============================================================================


def test_workspace_reload_agents_toml_syntax_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    bad_bytes = b"agents = [\nunclosed syntax"
    agents_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx is not None
            assert ctx.root == root
            default_agents = cached_default_agents_policy()
            assert (
                ctx.policy_bundle.agents.agent_chains.keys() == default_agents.agent_chains.keys()
            )
    finally:
        logger.remove(sink_id)

    assert agents_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(agents_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0] or "ralph --regenerate-config" in matching[0]


def test_workspace_reload_agents_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    agents_file = root / ".agent" / "agents.toml"
    bad_content = "[agent_chains.planning]\nagents = []\n"
    agents_file.write_text(bad_content, encoding="utf-8")
    before_bytes = agents_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_agents = cached_default_agents_policy()
            assert (
                ctx.policy_bundle.agents.agent_chains.keys() == default_agents.agent_chains.keys()
            )
    finally:
        logger.remove(sink_id)

    assert agents_file.read_bytes() == before_bytes
    matching = [
        r["message"]
        for r in records
        if str(agents_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1


# ==============================================================================
# Cell: workspace_context reload x policy (pipeline.toml / policy.toml)
# ==============================================================================


def test_workspace_reload_pipeline_toml_syntax_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    bad_bytes = b"phases = [\ninvalid"
    pipeline_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_pipeline = cached_default_pipeline_policy()
            assert ctx.policy_bundle.pipeline.entry_block == default_pipeline.entry_block
    finally:
        logger.remove(sink_id)

    assert pipeline_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(pipeline_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


def test_workspace_reload_pipeline_toml_permission_denied_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    pipeline_file = root / ".agent" / "pipeline.toml"
    pipeline_file.write_text("entry_block = 'plan'\n", encoding="utf-8")
    before_bytes = pipeline_file.read_bytes()
    pipeline_file.chmod(0o000)

    try:
        records, sink_id = _capture_warnings()
        try:
            with workspace_context(root) as ctx:
                default_pipeline = cached_default_pipeline_policy()
                assert ctx.policy_bundle.pipeline.entry_block == default_pipeline.entry_block
        finally:
            logger.remove(sink_id)

        matching = [
            r["message"]
            for r in records
            if str(pipeline_file) in r["message"] and "using built-in default" in r["message"]
        ]
        assert len(matching) == 1
    finally:
        pipeline_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert pipeline_file.read_bytes() == before_bytes


# ==============================================================================
# Cell: workspace_context reload x policy (artifacts.toml)
# ==============================================================================


def test_workspace_reload_artifacts_toml_syntax_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    artifacts_file = root / ".agent" / "artifacts.toml"
    bad_bytes = b"artifacts = { invalid"
    artifacts_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            default_artifacts = cached_default_artifacts_policy()
            assert (
                ctx.policy_bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
            )
    finally:
        logger.remove(sink_id)

    assert artifacts_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(artifacts_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


# ==============================================================================
# Cell: workspace_context reload x MCP (mcp.toml)
# ==============================================================================


def test_workspace_reload_mcp_toml_syntax_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    mcp_file = root / ".agent" / "mcp.toml"
    bad_bytes = b"[mcp_servers\ninvalid syntax [[[ ]"
    mcp_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx is not None
            assert ctx.effective_mcp_plan is not None
    finally:
        logger.remove(sink_id)

    assert mcp_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(mcp_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


def test_workspace_reload_mcp_toml_bad_encoding_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    mcp_file = root / ".agent" / "mcp.toml"
    bad_bytes = b"\xff\xfe\x00\x01\x02not-utf-8"
    mcp_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root) as ctx:
            assert ctx is not None
    finally:
        logger.remove(sink_id)

    assert mcp_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(mcp_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1


# ==============================================================================
# Cell: workspace_context reload x Portfolio (policy-portfolio.toml / portfolio.toml)
# ==============================================================================


def test_workspace_reload_portfolio_toml_syntax_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    policy_dir = root / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    portfolio_file = policy_dir / "policy-portfolio.toml"
    bad_bytes = b"schema_version = 'v3'\n[controls\ninvalid = [[["
    portfolio_file.write_bytes(bad_bytes)

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root):
            portfolio = load_portfolio_toml(portfolio_file)
            expected = default_portfolio()
            assert portfolio.schema_version == expected.schema_version
    finally:
        logger.remove(sink_id)

    assert portfolio_file.read_bytes() == bad_bytes
    matching = [
        r["message"]
        for r in records
        if str(portfolio_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


def test_workspace_reload_portfolio_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    portfolio_file = root / ".agent" / "portfolio.toml"
    bad_content = "schema_version = 'invalid_schema_ver'\ndefault_budget_seconds = 60.0\n"
    portfolio_file.write_text(bad_content, encoding="utf-8")
    before_bytes = portfolio_file.read_bytes()

    records, sink_id = _capture_warnings()
    try:
        with workspace_context(root):
            portfolio = load_portfolio_toml(portfolio_file)
            expected = default_portfolio()
            assert portfolio.schema_version == expected.schema_version
    finally:
        logger.remove(sink_id)

    assert portfolio_file.read_bytes() == before_bytes
    matching = [
        r["message"]
        for r in records
        if str(portfolio_file) in r["message"] and "using built-in default" in r["message"]
    ]
    assert len(matching) == 1
    assert "ralph --check-config" in matching[0]


# ==============================================================================
# Cell: --check-config x Portfolio (policy-portfolio.toml)
# ==============================================================================


def test_check_config_reports_unreadable_portfolio_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    monkeypatch.chdir(root)

    policy_dir = root / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    portfolio_file = policy_dir / "policy-portfolio.toml"
    portfolio_file.write_text("invalid_portfolio = [[[", encoding="utf-8")

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    display_ctx = make_display_context(console=console)

    records, sink_id = _capture_warnings()
    try:
        exit_code = handle_check_config(
            config=None,
            cli_overrides={},
            check_config=True,
            console=console,
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert exit_code == 1
    output = console_buf.getvalue() + "\n" + "\n".join(r["message"] for r in records)
    assert str(portfolio_file) in output or portfolio_file.name in output
    assert any(
        str(portfolio_file) in r["message"] or portfolio_file.name in r["message"] for r in records
    )


# ==============================================================================
# Cell: diagnose x Portfolio (policy-portfolio.toml)
# ==============================================================================


def test_diagnose_reports_unreadable_portfolio_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _setup_base_workspace(tmp_path / "repo")
    monkeypatch.chdir(root)

    policy_dir = root / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    portfolio_file = policy_dir / "policy-portfolio.toml"
    portfolio_file.write_text("invalid_portfolio = [[[", encoding="utf-8")

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    display_ctx = make_display_context(console=console)

    records, sink_id = _capture_warnings()
    try:
        exit_code = diagnose_command(
            config_path=None,
            cli_overrides={},
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert exit_code != 0
    output = console_buf.getvalue() + "\n" + "\n".join(r["message"] for r in records)
    assert str(portfolio_file) in output or portfolio_file.name in output
    assert any(
        str(portfolio_file) in r["message"] or portfolio_file.name in r["message"] for r in records
    )
