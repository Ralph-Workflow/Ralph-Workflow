from __future__ import annotations

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
from ralph.config.mcp_models import McpConfig
from ralph.config.web_service_configs import WebVisitConfig
from ralph.display.context import make_display_context
from ralph.policy.loader import (
    cached_default_agents_policy,
    cached_default_artifacts_policy,
    cached_default_pipeline_policy,
    load_policy,
    load_policy_for_config_dir,
)
from ralph.project_policy.portfolio import default_portfolio, load_portfolio_toml
from ralph.workspace.context import workspace_context
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ralph.policy.models import PolicyBundle

pytestmark = pytest.mark.timeout_seconds(5.0)

_BAD_ENCODING_BYTES = b"\xff\xfe\x00\x01\x02not-utf-8"
_READABLE_BYTES = b"key = 'value'\n"

_LOCAL_POLICY_CASES: tuple[tuple[str, str], ...] = (
    ("agents.toml", "agents"),
    ("pipeline.toml", "pipeline"),
    ("artifacts.toml", "artifacts"),
)

_GLOBAL_POLICY_CASES: tuple[tuple[str, str], ...] = (
    ("ralph-workflow-pipeline.toml", "pipeline"),
    ("ralph-workflow-artifacts.toml", "artifacts"),
)

_GLOBAL_RELOAD_CASES: tuple[tuple[str, str], ...] = (
    ("ralph-workflow-agents.toml", "agents"),
    ("ralph-workflow-pipeline.toml", "pipeline"),
    ("ralph-workflow-artifacts.toml", "artifacts"),
    ("ralph-workflow-mcp.toml", "mcp"),
)

_PARSE_ERROR_CASES: tuple[tuple[str, str, str], ...] = (
    ("agents", "agents.toml", "agents"),
    ("pipeline", "pipeline.toml", "pipeline"),
    ("artifacts", "artifacts.toml", "artifacts"),
    ("mcp", "mcp.toml", "mcp"),
    ("portfolio", "policy-portfolio.toml", "portfolio"),
)

_VALID_PORTFOLIO = (
    'schema_version = "v3"\ndefault_budget_seconds = 100.0\nkernel_controls = []\n\n'
    '[[lanes]]\nid = "default"\nowner = "project maintainers"\ntrigger = "every change"\n'
)

_PARTIALLY_VALID_PORTFOLIO = (
    'schema_version = "v3"\ndefault_budget_seconds = 100.0\nkernel_controls = []\n\n'
    '[[lanes]]\nid = "default"\nowner = "project maintainers"\ntrigger = "every change"\n\n'
    '[[lanes]]\nid = "broken"\nowner = 12345\ntrigger = "every change"\n'
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))


@pytest.fixture(autouse=True)
def _clean_fallback_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    return records, sink_id


def _capture_load[T](load: Callable[[], T]) -> tuple[T, list[str]]:
    records, sink_id = _capture_warnings()
    try:
        result = load()
    finally:
        logger.remove(sink_id)
    return result, records


def _fallback_warnings(records: list[str], path: Path) -> list[str]:
    target = str(path)
    resolved = str(path.resolve())
    return [
        record
        for record in records
        if (target in record or resolved in record) and "using built-in default" in record
    ]


def _assert_fallback_warning(
    records: list[str], path: Path, kind: str, error_marker: str | None = None
) -> None:
    matching = _fallback_warnings(records, path)
    assert len(matching) == 1, f"expected one fallback warning for {path}: {records}"
    warning = matching[0]
    assert kind in warning.lower()
    assert "ralph --check-config" in warning
    if error_marker is not None:
        assert error_marker in warning


def _write_unreadable_payload(path: Path, failure_mode: str) -> bytes:
    if failure_mode == "bad_encoding":
        path.write_bytes(_BAD_ENCODING_BYTES)
        return _BAD_ENCODING_BYTES
    path.write_bytes(_READABLE_BYTES)
    return _READABLE_BYTES


def _deny_file_reads(monkeypatch: pytest.MonkeyPatch, path: Path) -> Callable[[Path], bytes]:
    original_read_bytes = Path.read_bytes

    def _failing_read_bytes(self: Path) -> bytes:
        if self == path:
            raise PermissionError(f"[Errno 13] Permission denied: {self!s}")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _failing_read_bytes)
    return original_read_bytes


def _assert_shipped_default(bundle: PolicyBundle, kind: str) -> None:
    if kind == "agents":
        default = cached_default_agents_policy()
        assert bundle.agents.agent_chains.keys() == default.agent_chains.keys()
        assert bundle.agents.agent_drains.keys() == default.agent_drains.keys()
    elif kind == "pipeline":
        default = cached_default_pipeline_policy()
        assert bundle.pipeline.entry_block == default.entry_block
        assert bundle.pipeline.phases.keys() == default.phases.keys()
    else:
        default = cached_default_artifacts_policy()
        assert bundle.artifacts.artifacts.keys() == default.artifacts.keys()


def _load_named_kind(kind: str, path: Path) -> object:
    if kind == "mcp":
        return load_mcp_config(config_path=path)
    if kind == "portfolio":
        return load_portfolio_toml(path)
    return load_policy(path.parent)


@pytest.mark.parametrize(("filename", "kind"), _LOCAL_POLICY_CASES)
@pytest.mark.parametrize("failure_mode", ["bad_encoding", "permission_denied"])
def test_unreadable_local_policy_file_falls_back_to_the_shipped_default(
    filename: str,
    kind: str,
    failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_file = workspace / filename

    payload = _write_unreadable_payload(config_file, failure_mode)
    read_bytes = (
        _deny_file_reads(monkeypatch, config_file)
        if failure_mode == "permission_denied"
        else Path.read_bytes
    )
    bundle, records = _capture_load(lambda: load_policy(workspace))

    assert read_bytes(config_file) == payload
    _assert_shipped_default(bundle, kind)
    _assert_fallback_warning(
        records,
        config_file,
        kind,
        error_marker="Permission denied" if failure_mode == "permission_denied" else None,
    )


@pytest.mark.parametrize(("filename", "kind"), _GLOBAL_POLICY_CASES)
@pytest.mark.parametrize("failure_mode", ["bad_encoding", "permission_denied"])
def test_unreadable_global_policy_file_falls_back_to_the_shipped_default(
    filename: str,
    kind: str,
    failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_file = tmp_path / "home" / ".config" / filename
    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)

    payload = _write_unreadable_payload(global_file, failure_mode)
    read_bytes = (
        _deny_file_reads(monkeypatch, global_file)
        if failure_mode == "permission_denied"
        else Path.read_bytes
    )
    bundle, records = _capture_load(lambda: load_policy_for_config_dir(local_dir))

    assert read_bytes(global_file) == payload
    _assert_shipped_default(bundle, kind)
    _assert_fallback_warning(
        records,
        global_file,
        kind,
        error_marker="Permission denied" if failure_mode == "permission_denied" else None,
    )


@pytest.mark.parametrize("failure_mode", ["bad_encoding", "permission_denied"])
def test_unreadable_portfolio_file_falls_back_to_the_shipped_default(
    failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"

    payload = _write_unreadable_payload(portfolio_file, failure_mode)
    read_bytes = (
        _deny_file_reads(monkeypatch, portfolio_file)
        if failure_mode == "permission_denied"
        else Path.read_bytes
    )
    portfolio, records = _capture_load(lambda: load_portfolio_toml(portfolio_file))

    assert read_bytes(portfolio_file) == payload
    assert portfolio.schema_version == default_portfolio().schema_version
    assert portfolio.default_budget_seconds == default_portfolio().default_budget_seconds
    _assert_fallback_warning(
        records,
        portfolio_file,
        "portfolio",
        error_marker="Permission denied" if failure_mode == "permission_denied" else None,
    )


@pytest.mark.parametrize(("kind", "filename", "warn_kind"), _PARSE_ERROR_CASES)
def test_fallback_warning_reports_the_parse_error_location(
    kind: str,
    filename: str,
    warn_kind: str,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_file = workspace / filename
    config_file.write_text("broken = [[[\n", encoding="utf-8")

    _, records = _capture_load(lambda: _load_named_kind(kind, config_file))

    matching = _fallback_warnings(records, config_file)
    assert len(matching) == 1, f"expected one fallback warning for {config_file}: {records}"
    warning = matching[0]
    assert warn_kind in warning.lower()
    assert "line" in warning.lower()
    assert "column" in warning.lower()
    assert "ralph --check-config" in warning


@pytest.mark.parametrize(("filename", "kind"), _GLOBAL_RELOAD_CASES)
def test_workspace_reload_global_config_file_falls_back_with_a_warning(
    filename: str,
    kind: str,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".agent").mkdir(parents=True)
    (workspace / ".agent" / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")
    (workspace / "PROMPT.md").write_text("# prompt\n", encoding="utf-8")
    global_file = tmp_path / "home" / ".config" / filename
    global_file.write_text("broken = [[[\n", encoding="utf-8")
    payload = global_file.read_bytes()

    def _enter_workspace() -> None:
        with workspace_context(workspace):
            pass

    _, records = _capture_load(_enter_workspace)

    assert global_file.read_bytes() == payload
    matching = _fallback_warnings(records, global_file)
    assert len(matching) == 1, f"expected one fallback warning for {global_file}: {records}"
    assert kind in matching[0].lower()
    assert "ralph --check-config" in matching[0]


def test_partially_valid_global_agents_file_is_replaced_by_the_shipped_default(
    tmp_path: Path,
) -> None:
    agents_file = tmp_path / "home" / ".config" / "ralph-workflow-agents.toml"
    agents_file.write_text(
        '[agents.good]\ncmd = "good"\n\n[agents.broken]\ncmd = 12345\n',
        encoding="utf-8",
    )
    local_config = tmp_path / "ralph-workflow.toml"
    local_config.write_text("", encoding="utf-8")

    config, records = _capture_load(lambda: load_config(config_path=local_config))

    assert config.agents == {}
    _assert_fallback_warning(records, agents_file, "agents")


def test_partially_valid_local_agents_policy_is_replaced_by_the_shipped_default(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents_file = workspace / "agents.toml"
    agents_file.write_text(
        '[agent_chains.custom_phase]\nagents = ["claude"]\nmax_retries = 99\n\n'
        '[agent_drains.custom_phase]\ndrain = "custom_phase"\nmax_retries = 2\n',
        encoding="utf-8",
    )

    bundle, records = _capture_load(lambda: load_policy(workspace))

    assert "custom_phase" not in bundle.agents.agent_chains
    assert bundle.agents.agent_chains.keys() == cached_default_agents_policy().agent_chains.keys()
    _assert_fallback_warning(records, agents_file, "agents")


def test_partially_valid_mcp_file_is_replaced_by_the_shipped_default(tmp_path: Path) -> None:
    mcp_file = tmp_path / "mcp.toml"
    mcp_file.write_text(
        "[web_visit]\nmax_bytes = 123456\n\n"
        '[mcp_servers.broken]\nname = "broken"\ntransport = "stdio"\n',
        encoding="utf-8",
    )

    config, records = _capture_load(lambda: load_mcp_config(config_path=mcp_file))

    assert config.web_visit.max_bytes == WebVisitConfig().max_bytes
    assert config.mcp_servers == {}
    _assert_fallback_warning(records, mcp_file, "mcp")


def test_partially_valid_pipeline_file_is_replaced_by_the_shipped_default(tmp_path: Path) -> None:
    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    pipeline_file = local_dir / "pipeline.toml"
    pipeline_file.write_text(
        "[development_timebox]\nduration_seconds = 7200\nwarning_seconds = 6000\n\n"
        "entry_block = 42\n",
        encoding="utf-8",
    )

    bundle, records = _capture_load(lambda: load_policy_for_config_dir(local_dir))

    default = cached_default_pipeline_policy()
    assert default.development_timebox is not None
    assert bundle.pipeline.development_timebox is not None
    assert (
        bundle.pipeline.development_timebox.duration_seconds
        == default.development_timebox.duration_seconds
    )
    _assert_fallback_warning(records, pipeline_file, "pipeline")


def test_partially_valid_artifacts_file_is_replaced_by_the_shipped_default(tmp_path: Path) -> None:
    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    artifacts_file = local_dir / "artifacts.toml"
    artifacts_file.write_text(
        '[artifacts.custom_artifact]\ndrain = "development"\nartifact_type = "custom_artifact"\n'
        'markdown_summary_path = "CUSTOM.md"\n\n'
        '[artifacts.plan]\nartifact_required = "not-a-bool"\n',
        encoding="utf-8",
    )

    bundle, records = _capture_load(lambda: load_policy_for_config_dir(local_dir))

    assert "custom_artifact" not in bundle.artifacts.artifacts
    assert bundle.artifacts.artifacts.keys() == cached_default_artifacts_policy().artifacts.keys()
    _assert_fallback_warning(records, artifacts_file, "artifacts")


def test_partially_valid_portfolio_file_is_replaced_by_the_shipped_default(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text(_PARTIALLY_VALID_PORTFOLIO, encoding="utf-8")

    portfolio, records = _capture_load(lambda: load_portfolio_toml(portfolio_file))

    assert portfolio.default_budget_seconds == default_portfolio().default_budget_seconds
    _assert_fallback_warning(records, portfolio_file, "portfolio")


def test_portfolio_fallback_warns_once_per_unchanged_file_and_rewarns_on_change(
    tmp_path: Path,
) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text('schema_version = "v3"\nlanes = "not-a-list"\n', encoding="utf-8")

    _, first = _capture_load(lambda: load_portfolio_toml(portfolio_file))
    assert len(_fallback_warnings(first, portfolio_file)) == 1

    _, second = _capture_load(lambda: load_portfolio_toml(portfolio_file))
    assert len(_fallback_warnings(second, portfolio_file)) == 0

    portfolio_file.write_text(
        'schema_version = "v3"\nlanes = "still-not-a-list"\n', encoding="utf-8"
    )
    _, third = _capture_load(lambda: load_portfolio_toml(portfolio_file))
    assert len(_fallback_warnings(third, portfolio_file)) == 1

    reset_fallback_warning_registry()
    _, fourth = _capture_load(lambda: load_portfolio_toml(portfolio_file))
    assert len(_fallback_warnings(fourth, portfolio_file)) == 1


def test_missing_config_files_load_the_shipped_default_without_any_warning(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_config = tmp_path / "ralph-workflow.toml"
    local_config.write_text("", encoding="utf-8")
    portfolio_file = tmp_path / "policy-portfolio.toml"
    config_home = tmp_path / "home" / ".config"

    bundle, policy_records = _capture_load(lambda: load_policy(workspace))
    config, config_records = _capture_load(lambda: load_config(config_path=local_config))
    mcp, mcp_records = _capture_load(lambda: load_mcp_config(config_path=workspace / "mcp.toml"))
    portfolio, portfolio_records = _capture_load(lambda: load_portfolio_toml(portfolio_file))

    expected_paths = (
        config_home / "ralph-workflow.toml",
        config_home / "ralph-workflow-agents.toml",
        config_home / "ralph-workflow-pipeline.toml",
        config_home / "ralph-workflow-artifacts.toml",
        config_home / "ralph-workflow-mcp.toml",
        workspace / "agents.toml",
        workspace / "pipeline.toml",
        workspace / "artifacts.toml",
        workspace / "mcp.toml",
        portfolio_file,
    )
    for records in (policy_records, config_records, mcp_records, portfolio_records):
        for path in expected_paths:
            assert not [record for record in records if str(path) in record], (
                f"expected no warning naming the missing file {path}: {records}"
            )

    assert config.agents == {}
    assert bundle.agents.agent_chains.keys() == cached_default_agents_policy().agent_chains.keys()
    assert bundle.pipeline.entry_block == cached_default_pipeline_policy().entry_block
    assert bundle.artifacts.artifacts.keys() == cached_default_artifacts_policy().artifacts.keys()
    assert mcp == McpConfig()
    assert portfolio.schema_version == default_portfolio().schema_version


def test_valid_config_files_override_the_shipped_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents_file = workspace / "agents.toml"
    agents_file.write_text(
        '[agent_chains.custom_phase]\nagents = ["claude"]\nmax_retries = 99\n\n'
        '[agent_drains.custom_phase]\nchain = "custom_phase"\nmax_retries = 2\n',
        encoding="utf-8",
    )
    mcp_file = workspace / "mcp.toml"
    mcp_file.write_text(
        '[mcp_servers.local]\ntransport = "stdio"\ncommand = "echo"\n',
        encoding="utf-8",
    )
    global_agents = tmp_path / "home" / ".config" / "ralph-workflow-agents.toml"
    global_agents.write_text('[agents.custom]\ncmd = "custom"\n', encoding="utf-8")
    local_config = tmp_path / "ralph-workflow.toml"
    local_config.write_text("", encoding="utf-8")

    bundle, policy_records = _capture_load(lambda: load_policy(workspace))
    mcp, mcp_records = _capture_load(lambda: load_mcp_config(config_path=mcp_file))
    config, config_records = _capture_load(lambda: load_config(config_path=local_config))

    assert bundle.agents.agent_chains["custom_phase"].max_retries == 99
    assert "local" in mcp.mcp_servers
    assert config.agents["custom"].cmd == "custom"
    assert not _fallback_warnings(policy_records, agents_file)
    assert not _fallback_warnings(mcp_records, mcp_file)
    assert not _fallback_warnings(config_records, global_agents)


def test_valid_portfolio_file_overrides_the_shipped_default(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text(_VALID_PORTFOLIO, encoding="utf-8")

    portfolio, records = _capture_load(lambda: load_portfolio_toml(portfolio_file))

    assert portfolio.default_budget_seconds == 100.0
    assert not _fallback_warnings(records, portfolio_file)


def _write_corrupt_config_files(repo_root: Path, global_dir: Path) -> dict[str, Path]:
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text(
        '[general]\ndeveloper_iters = 5\n\n[agent_chains]\nanalysis = "mock"\ncommit = "mock"\n',
        encoding="utf-8",
    )
    portfolio_dir = repo_root / "docs" / "ralph-workflow-policy"
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    corrupt = {
        "global_agents": global_dir / "ralph-workflow-agents.toml",
        "global_pipeline": global_dir / "ralph-workflow-pipeline.toml",
        "global_artifacts": global_dir / "ralph-workflow-artifacts.toml",
        "global_mcp": global_dir / "ralph-workflow-mcp.toml",
        "local_agents": agent_dir / "agents.toml",
        "local_pipeline": agent_dir / "pipeline.toml",
        "local_artifacts": agent_dir / "artifacts.toml",
        "local_mcp": agent_dir / "mcp.toml",
        "portfolio": portfolio_dir / "policy-portfolio.toml",
    }
    for path in corrupt.values():
        path.write_text("broken = [[[\n", encoding="utf-8")
    return corrupt


def _assert_every_kind_reported(
    records: list[str], corrupt: dict[str, Path], payloads: dict[str, bytes]
) -> None:
    for name, path in corrupt.items():
        matching = _fallback_warnings(records, path)
        assert len(matching) == 1, f"expected one fallback warning for {name}: {records}"
        assert "ralph --check-config" in matching[0]
        assert path.read_bytes() == payloads[name]


def test_check_config_reports_a_fallback_warning_for_every_config_kind(
    tmp_path: Path,
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)
    global_dir = tmp_path / "check_config_global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    corrupt = _write_corrupt_config_files(repo_root, global_dir)
    payloads = {name: path.read_bytes() for name, path in corrupt.items()}

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
    _assert_every_kind_reported(records, corrupt, payloads)


def test_diagnose_reports_a_fallback_warning_for_every_config_kind(
    tmp_path: Path,
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_git_repo
    monkeypatch.chdir(repo_root)
    global_dir = tmp_path / "diagnose_global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    corrupt = _write_corrupt_config_files(repo_root, global_dir)
    payloads = {name: path.read_bytes() for name, path in corrupt.items()}

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
    _assert_every_kind_reported(records, corrupt, payloads)


def test_global_ralph_workflow_toml_is_never_defaulted(tmp_path: Path) -> None:
    global_config = tmp_path / "home" / ".config" / "ralph-workflow.toml"
    global_config.write_text("[general\nbroken syntax = = =\n", encoding="utf-8")
    payload = global_config.read_bytes()
    workspace = tmp_path / "repo"
    (workspace / ".agent").mkdir(parents=True)
    local_config = workspace / ".agent" / "ralph-workflow.toml"
    local_config.write_text("", encoding="utf-8")
    scope = WorkspaceScope(root=workspace, local_config_path=local_config)

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(workspace_scope=scope)

    message = str(exc_info.value)
    assert "What failed: Ralph could not read" in message
    assert str(global_config) in message
    assert "Why it matters: settings in a malformed file are not safe to use." in message
    assert (
        f"Fix: correct the TOML syntax in {global_config}, then run `ralph --check-config`."
        in message
    )
    assert global_config.read_bytes() == payload


def test_project_local_ralph_workflow_toml_is_never_defaulted(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".agent").mkdir(parents=True)
    local_config = workspace / ".agent" / "ralph-workflow.toml"
    local_config.write_text("[general\nbroken syntax = = =\n", encoding="utf-8")
    payload = local_config.read_bytes()
    scope = WorkspaceScope(root=workspace, local_config_path=local_config)

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(workspace_scope=scope)

    message = str(exc_info.value)
    assert "What failed: Ralph could not read" in message
    assert str(local_config) in message
    assert "Why it matters: settings in a malformed file are not safe to use." in message
    assert (
        f"Fix: correct the TOML syntax in {local_config}, then run `ralph --check-config`."
        in message
    )
    assert local_config.read_bytes() == payload
