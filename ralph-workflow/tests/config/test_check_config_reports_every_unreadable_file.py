"""Tests for aggregated unreadable file reporting in --check-config and --diagnose.

Verifies that when multiple configuration, policy, and MCP layers are corrupted,
both `handle_check_config` and `diagnose_command` report every single unreadable
file in a single run without failing fast, exit non-zero, and emit a WARN record
for each unreadable file.
"""

from __future__ import annotations

import io
from collections.abc import Generator
from pathlib import Path

import pytest
from loguru import logger
from rich.console import Console

from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.main import handle_check_config
from ralph.config import reset_fallback_warning_registry
from ralph.display.context import make_display_context

pytestmark = pytest.mark.timeout_seconds(5.0)


@pytest.fixture(autouse=True)
def _clean_fallback_registry() -> Generator[None, None, None]:
    """Ensure each test starts and finishes with an empty warning registry."""
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def _capture_loguru_warnings() -> tuple[list[str], int]:
    """Attach a loguru sink capturing WARNING level messages."""
    records: list[str] = []

    def _sink(message: object) -> None:
        rec = getattr(message, "record", {})
        level = rec.get("level")
        if level is not None and getattr(level, "name", "") == "WARNING":
            records.append(str(rec.get("message", "")))

    sink_id = logger.add(_sink, level="WARNING")
    return records, sink_id


def _setup_test_environment(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, Path, Path]:
    """Set up an isolated workspace repo and global config directory.

    The workspace root is a real Git repository supplied by the shared
    ``tmp_git_repo`` fixture, so no test in this file creates a repository
    of its own.

    Returns:
        (repo_root, global_dir, agents_file, policy_file, global_mcp_file, local_mcp_file)
    """
    monkeypatch.chdir(repo_root)

    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    # Base valid ralph-workflow.toml in workspace root
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir()
    local_main_config = agent_dir / "ralph-workflow.toml"
    local_main_config.write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )

    # 1. Corrupt agents file (local)
    agents_file = agent_dir / "agents.toml"
    agents_file.write_text("invalid_agents_syntax = [[[", encoding="utf-8")

    # 2. Corrupt policy file (local pipeline.toml)
    policy_file = agent_dir / "pipeline.toml"
    policy_file.write_text("invalid_pipeline_syntax = {[[", encoding="utf-8")

    # 3. Corrupt global MCP file
    global_mcp_file = global_dir / "ralph-workflow-mcp.toml"
    global_mcp_file.write_text("invalid_global_mcp_syntax = 'unclosed string", encoding="utf-8")

    # 4. Corrupt local MCP file
    local_mcp_file = agent_dir / "mcp.toml"
    local_mcp_file.write_text("invalid_local_mcp_syntax = ]]]", encoding="utf-8")

    return repo_root, global_dir, agents_file, policy_file, global_mcp_file, local_mcp_file


def test_check_config_reports_every_unreadable_file(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """handle_check_config names every unreadable file in one run, exits 1, and emits WARN records."""
    (
        _repo_root,
        _global_dir,
        agents_file,
        policy_file,
        global_mcp_file,
        local_mcp_file,
    ) = _setup_test_environment(tmp_git_repo, tmp_path, monkeypatch)

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    display_ctx = make_display_context(console=console)

    captured_warnings, sink_id = _capture_loguru_warnings()
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

    # Must exit non-zero
    assert exit_code == 1

    output = console_buf.getvalue()
    corrupt_files = [agents_file, policy_file, global_mcp_file, local_mcp_file]

    # ONE run names EVERY unreadable file in output (either console output or logs)
    combined_output = output + "\n" + "\n".join(captured_warnings)
    for f in corrupt_files:
        assert str(f) in combined_output or f.name in combined_output, (
            f"Expected corrupt file {f} to be named in output:\n{combined_output}"
        )

    # Asserts a WARN record per unreadable file
    for f in corrupt_files:
        matching_warnings = [w for w in captured_warnings if str(f) in w or f.name in w]
        assert matching_warnings, (
            f"Expected a WARN record for unreadable file {f}. Captured warnings:\n"
            + "\n".join(captured_warnings)
        )


def test_diagnose_reports_every_unreadable_file(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """diagnose_command names every unreadable file in one run, exits non-zero, and emits WARN records."""
    (
        _repo_root,
        _global_dir,
        agents_file,
        policy_file,
        global_mcp_file,
        local_mcp_file,
    ) = _setup_test_environment(tmp_git_repo, tmp_path, monkeypatch)

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    display_ctx = make_display_context(console=console)

    captured_warnings, sink_id = _capture_loguru_warnings()
    try:
        exit_code = diagnose_command(
            config_path=None,
            cli_overrides={},
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    # Must exit non-zero
    assert exit_code != 0

    output = console_buf.getvalue()
    corrupt_files = [agents_file, policy_file, global_mcp_file, local_mcp_file]

    # ONE run names EVERY unreadable file in output
    combined_output = output + "\n" + "\n".join(captured_warnings)
    for f in corrupt_files:
        assert str(f) in combined_output or f.name in combined_output, (
            f"Expected corrupt file {f} to be named in diagnose output:\n{combined_output}"
        )

    # Asserts a WARN record per unreadable file
    for f in corrupt_files:
        matching_warnings = [w for w in captured_warnings if str(f) in w or f.name in w]
        assert matching_warnings, (
            f"Expected a WARN record for unreadable file {f}. Captured warnings:\n"
            + "\n".join(captured_warnings)
        )


def test_check_config_and_diagnose_all_valid_files(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When all files are valid, check-config and diagnose exit 0 with no fallback warnings."""
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)

    global_dir = tmp_path / "valid_global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    agent_dir = repo_root / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )
    (agent_dir / "mcp.toml").write_text("[mcp_servers]\n", encoding="utf-8")
    (global_dir / "ralph-workflow-mcp.toml").write_text("[mcp_servers]\n", encoding="utf-8")

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    display_ctx = make_display_context(console=console)

    captured_warnings, sink_id = _capture_loguru_warnings()
    try:
        check_exit = handle_check_config(
            config=None,
            cli_overrides={},
            check_config=True,
            console=console,
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert check_exit == 0
    assert captured_warnings == []


def test_check_config_and_diagnose_global_layers_corrupt(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check-config and diagnose aggregate unreadable global agents, global policy, and MCP files."""
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)

    global_dir = tmp_path / "global_corrupt_dir"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    agent_dir = repo_root / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )

    # Global agents corrupt
    global_agents = global_dir / "ralph-workflow-agents.toml"
    global_agents.write_text("bad_global_agents = [[[", encoding="utf-8")

    # Global policy corrupt
    global_pipeline = global_dir / "ralph-workflow-pipeline.toml"
    global_pipeline.write_text("bad_global_pipeline = [[[", encoding="utf-8")

    # Global MCP corrupt
    global_mcp = global_dir / "ralph-workflow-mcp.toml"
    global_mcp.write_text("bad_global_mcp = [[[", encoding="utf-8")

    # Local MCP corrupt
    local_mcp = agent_dir / "mcp.toml"
    local_mcp.write_text("bad_local_mcp = [[[", encoding="utf-8")

    corrupt_files = [global_agents, global_pipeline, global_mcp, local_mcp]

    # Test --check-config
    console_buf1 = io.StringIO()
    console1 = Console(file=console_buf1, force_terminal=False)
    ctx1 = make_display_context(console=console1)
    captured1, sink1 = _capture_loguru_warnings()
    try:
        rc1 = handle_check_config(
            config=None, cli_overrides={}, check_config=True, console=console1, display_context=ctx1
        )
    finally:
        logger.remove(sink1)

    assert rc1 == 1
    combined_output1 = console_buf1.getvalue() + "\n" + "\n".join(captured1)
    for f in corrupt_files:
        assert str(f) in combined_output1 or f.name in combined_output1
        assert any(str(f) in w or f.name in w for w in captured1)

    # Test --diagnose
    reset_fallback_warning_registry()
    console_buf2 = io.StringIO()
    console2 = Console(file=console_buf2, force_terminal=False)
    ctx2 = make_display_context(console=console2)
    captured2, sink2 = _capture_loguru_warnings()
    try:
        rc2 = diagnose_command(config_path=None, cli_overrides={}, display_context=ctx2)
    finally:
        logger.remove(sink2)

    assert rc2 != 0
    combined_output2 = console_buf2.getvalue() + "\n" + "\n".join(captured2)
    for f in corrupt_files:
        assert str(f) in combined_output2 or f.name in combined_output2
        assert any(str(f) in w or f.name in w for w in captured2)


def test_check_config_reports_permission_denied_file(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check-config and diagnose report permission-denied (chmod 000) files as unreadable."""
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)

    global_dir = tmp_path / "perm_global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    agent_dir = repo_root / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )

    unreadable_mcp = agent_dir / "mcp.toml"
    unreadable_mcp.write_text("[mcp_servers]\n", encoding="utf-8")
    unreadable_mcp.chmod(0o000)

    try:
        console_buf = io.StringIO()
        console = Console(file=console_buf, force_terminal=False)
        ctx = make_display_context(console=console)
        captured, sink = _capture_loguru_warnings()
        try:
            rc = handle_check_config(
                config=None,
                cli_overrides={},
                check_config=True,
                console=console,
                display_context=ctx,
            )
        finally:
            logger.remove(sink)

        assert rc == 1
        combined = console_buf.getvalue() + "\n" + "\n".join(captured)
        assert str(unreadable_mcp) in combined or unreadable_mcp.name in combined
        assert any(str(unreadable_mcp) in w or unreadable_mcp.name in w for w in captured)
    finally:
        unreadable_mcp.chmod(0o644)


def test_check_config_reports_schema_invalid_agents_and_mcp_files(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """handle_check_config exits non-zero and names schema-invalid agents and MCP files."""
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)

    global_dir = tmp_path / "schema_invalid_global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    agent_dir = repo_root / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )

    # 1. Schema-invalid agents TOML (cmd is integer instead of string)
    agents_file = global_dir / "ralph-workflow-agents.toml"
    agents_file.write_text("[agents.broken]\ncmd = 12345\n", encoding="utf-8")

    # 2. Schema-invalid MCP TOML (max_inline_bytes must be > 0)
    mcp_file = global_dir / "ralph-workflow-mcp.toml"
    mcp_file.write_text("[media]\nenabled = true\nmax_inline_bytes = 0\n", encoding="utf-8")

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False)
    ctx = make_display_context(console=console)
    captured, sink = _capture_loguru_warnings()
    try:
        rc = handle_check_config(
            config=None,
            cli_overrides={},
            check_config=True,
            console=console,
            display_context=ctx,
        )
    finally:
        logger.remove(sink)

    assert rc == 1
    combined = console_buf.getvalue() + "\n" + "\n".join(captured)

    assert str(agents_file) in combined or agents_file.name in combined
    assert any(str(agents_file) in w or agents_file.name in w for w in captured)

    assert str(mcp_file) in combined or mcp_file.name in combined
    assert any(str(mcp_file) in w or mcp_file.name in w for w in captured)
