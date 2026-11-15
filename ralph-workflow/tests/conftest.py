from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, ArtifactsPolicy, PipelinePolicy
    from ralph.workspace.memory import MemoryWorkspace
    from tests.integration._mock_agent_invoker import MockAgentInvoker

import pytest
from _pytest.unraisableexception import gc_collect_iterations_key

from tests._real_config_guard_support import (
    _REAL_HOME,
    _REAL_XDG_CONFIG_HOME,
    _clear_recorded_read_violations,
    _clear_recorded_write_violations,
    _compute_file_sha256,
    _ConfigPathSnapshot,
    _diff_config_snapshots,
    _get_test_read_violations,
    _install_config_guard_audit_hook,
    _read_violations_count,
    _resolve_guarded_config_dirs,
    _scan_guarded_config_files,
    _set_current_test_node,
)

__all__ = [
    "_REAL_HOME",
    "_REAL_XDG_CONFIG_HOME",
    "_ConfigPathSnapshot",
    "_clear_recorded_read_violations",
    "_compute_file_sha256",
    "_diff_config_snapshots",
    "_get_test_read_violations",
    "_install_config_guard_audit_hook",
    "_read_violations_count",
    "_resolve_guarded_config_dirs",
    "_scan_guarded_config_files",
    "_set_current_test_node",
]

from ralph.mcp.explore import recovery as _explore_recovery
from ralph.mcp.server._mcp_health_event import clear_host_contention_signals
from ralph.pipeline._runner_session import set_last_captured_session_id
from ralph.pipeline.agent_retry_intent import (
    cleared_agent_retry_intent,
    set_last_captured_retry_intent,
)
from ralph.process import reset_process_manager
from ralph.test_suite_registry import REQUIRED_AUTO_INTEGRATE_E2E_FILES

pytest_plugins = ("ralph.testing.pytest_timeout_plugin", "tests._per_test_budget")
_REQUIRED_AUTO_INTEGRATE_E2E_PATHS = frozenset(REQUIRED_AUTO_INTEGRATE_E2E_FILES) | {
    "tests/test_integration_ladder_real_git.py",
}
_AGY_DEFAULT_TEST_PATHS = frozenset(
    {
        "tests/test_agy_pty_invocation.py",
        "tests/test_agy_runtime_home_auth.py",
    }
)


@pytest.fixture(autouse=True)
def _isolate_test_environment_and_globals(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    _apply_placeholder_hosted_provider_credentials(monkeypatch)
    _apply_disable_universal_skill_updates(monkeypatch)
    _apply_disable_explore_recovery_drain_thread(monkeypatch)
    with _isolate_explore_recovery_globals():
        _apply_fake_claude_cli_mcp_probe(monkeypatch)
        _apply_isolate_process_home(request, monkeypatch, tmp_path_factory)
        _set_current_test_node(request.node.nodeid)
        _apply_isolate_pipeline_thread_locals()
        _apply_clear_host_contention_signals()
        _apply_reset_default_process_manager()
        try:
            yield
        finally:
            _set_current_test_node(None)
            _apply_reset_default_process_manager()
            _apply_clear_host_contention_signals()
            _reset_rich_style_color_caches()


def _apply_placeholder_hosted_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "RALPH_MCP_ENDPOINT",
        "RALPH_MCP_RUN_ID",
        "RALPH_MCP_INHERITED_FD",
        "RALPH_MCP_RESPAWN",
    ):
        if var in os.environ:
            del os.environ[var]


def _apply_disable_universal_skill_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")


def _apply_disable_explore_recovery_drain_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_explore_recovery, "_ensure_drain_thread", lambda: None)


@contextmanager
def _isolate_explore_recovery_globals() -> Iterator[None]:
    with _explore_recovery._PENDING_LOCK:
        pending_snapshot = _explore_recovery._PENDING_RECOVERY.copy()
        wakeup_was_set = _explore_recovery._DRAIN_WAKEUP.is_set()
        _explore_recovery._PENDING_RECOVERY.clear()
    _explore_recovery._DRAIN_WAKEUP.clear()
    try:
        yield
    finally:
        with _explore_recovery._PENDING_LOCK:
            _explore_recovery._PENDING_RECOVERY.clear()
            if pending_snapshot:
                _explore_recovery._PENDING_RECOVERY.update(pending_snapshot)
        if wakeup_was_set:
            _explore_recovery._DRAIN_WAKEUP.set()
        else:
            _explore_recovery._DRAIN_WAKEUP.clear()


def _apply_clear_host_contention_signals() -> None:
    clear_host_contention_signals()


@pytest.fixture(scope="session", autouse=True)
def _warm_pygments_python_lexer() -> Generator[None, None, None]:
    from pygments.lexers import get_lexer_by_name

    get_lexer_by_name("python")
    yield


@pytest.fixture(scope="session", autouse=True)
def _fake_agy_models_probe() -> Generator[None, None, None]:
    from ralph.agents import registry

    original_probe = registry._default_agy_models_probe
    registry._default_agy_models_probe = lambda: "\n".join(registry._AGY_MODELS)
    try:
        yield
    finally:
        registry._default_agy_models_probe = original_probe


@pytest.fixture(scope="session", autouse=True)
def _guard_real_config_files(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    _install_config_guard_audit_hook()
    _clear_recorded_read_violations()
    _clear_recorded_write_violations()
    guarded_dirs = _resolve_guarded_config_dirs()
    before = _scan_guarded_config_files(guarded_dirs)
    session_home = tmp_path_factory.mktemp("session-home")
    session_config = session_home / ".config"
    session_config.mkdir(parents=True)
    orig_home = os.environ.get("HOME")
    orig_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["HOME"] = str(session_home)
    os.environ["XDG_CONFIG_HOME"] = str(session_config)
    try:
        yield
    finally:
        if orig_home is not None:
            os.environ["HOME"] = orig_home
        else:
            os.environ.pop("HOME", None)
        if orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)
        after = _scan_guarded_config_files(guarded_dirs)
        violations = _diff_config_snapshots(before, after)
        for node, p in _clear_recorded_read_violations():
            if node:
                violations.append(f"READ: {p} (by {node})")
            else:
                violations.append(f"READ: {p}")
        for node, p in _clear_recorded_write_violations():
            if node:
                violations.append(f"TRANSIENT MUTATION: {p} (by {node})")
            else:
                violations.append(f"TRANSIENT MUTATION: {p}")
        if violations:
            msg = (
                "Test suite touched real user configuration files in violation of isolation rules:\n"
                + "\n".join(f"  - {v}" for v in violations)
                + "\nAll tests must use isolated environments (e.g. tmp_path, isolated HOME/XDG_CONFIG_HOME)."
            )
            pytest.fail(msg, pytrace=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    from ralph.mcp.runtime_executors import shutdown_runtime_executors
    from ralph.process import reset_process_manager

    reset_process_manager()
    shutdown_runtime_executors()


def _apply_reset_default_process_manager() -> None:
    reset_process_manager()


def _apply_fake_claude_cli_mcp_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.mcp.transport import claude as claude_transport

    claude_transport.reset_claude_mcp_proxy_report()
    monkeypatch.setattr(claude_transport, "claude_cli_mcp_server_lister", lambda: None)


def pytest_configure(config: pytest.Config) -> None:
    config.stash[gc_collect_iterations_key] = 0
    _bypass_macos_git_shim()


_MACOS_GIT_SHIM = "/usr/bin/git"
_XCODE_SELECT_LINK = Path("/var/db/xcode_select_link")


def _bypass_macos_git_shim() -> None:
    if sys.platform != "darwin" or shutil.which("git") != _MACOS_GIT_SHIM:
        return
    developer_dir = os.environ.get("DEVELOPER_DIR")
    if not developer_dir and _XCODE_SELECT_LINK.is_symlink():
        developer_dir = os.path.realpath(_XCODE_SELECT_LINK)
    if not developer_dir:
        return
    real_git = Path(developer_dir) / "usr" / "bin" / "git"
    if not real_git.is_file():
        return
    bypass_dir = Path(tempfile.gettempdir()) / f"ralph-pytest-git-{os.getuid()}"
    bypass_dir.mkdir(mode=0o700, exist_ok=True)
    link = bypass_dir / "git"
    if not (link.is_symlink() and link.resolve() == real_git.resolve()):
        staging = bypass_dir / f"git.{os.getpid()}"
        staging.unlink(missing_ok=True)
        staging.symlink_to(real_git)
        staging.replace(link)
    os.environ["PATH"] = f"{bypass_dir}{os.pathsep}{os.environ.get('PATH', '')}"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    cwd = Path.cwd()
    relative_dirs: dict[Path, str] = {}
    for item in items:
        parent = item.path.parent
        relative_dir = relative_dirs.get(parent)
        if relative_dir is None:
            relative_dir = parent.relative_to(cwd).as_posix()
            relative_dirs[parent] = relative_dir
        relative_path = (
            item.path.name if relative_dir == "." else f"{relative_dir}/{item.path.name}"
        )
        if relative_path in _REQUIRED_AUTO_INTEGRATE_E2E_PATHS:
            item.add_marker("required_auto_integrate_e2e")
        if "agy" in item.nodeid.casefold() and relative_path not in _AGY_DEFAULT_TEST_PATHS:
            item.add_marker("smoke")


if TYPE_CHECKING:
    from collections.abc import Generator

    from git import Repo


def _reset_rich_style_color_caches() -> None:
    if "rich.style" in sys.modules:
        rich_style = sys.modules["rich.style"]
        rich_style.Style.parse.cache_clear()
        rich_style.Style._add.cache_clear()


def _apply_isolate_process_home(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:

    worker_root: str = _autouse_home_base(tmp_path_factory)
    unique_suffix: str = request.node.nodeid
    fake_home: str = worker_root + "/" + unique_suffix + "/home"
    os.environ["HOME"] = fake_home
    os.environ["XDG_CONFIG_HOME"] = fake_home + "/.config"
    for var in (
        "RALPH_UPSTREAM_MCP_CONFIG",
        "RALPH_CYCLE_WARN_EPOCH",
        "RALPH_CYCLE_DEADLINE_EPOCH",
        "RALPH_DEV_WARN_EPOCH",
        "RALPH_DEV_DEADLINE_EPOCH",
    ):
        if var in os.environ:
            del os.environ[var]
    if os.environ.get("ANTHROPIC_API_KEY") != "test-dummy-key":
        os.environ["ANTHROPIC_API_KEY"] = "test-dummy-key"
    if os.environ.get("OPENAI_API_KEY") != "test-dummy-key":
        os.environ["OPENAI_API_KEY"] = "test-dummy-key"
    if os.environ.get("CURSOR_API_KEY") != "test-dummy-key":
        os.environ["CURSOR_API_KEY"] = "test-dummy-key"
    config_dir = fake_home + "/.config"
    loader_mod = sys.modules.get("ralph.config.loader")
    if loader_mod is not None:
        loader_mod.GLOBAL_CONFIG_PATH = Path(config_dir + "/ralph-workflow.toml")
        loader_mod.GLOBAL_AGENTS_CONFIG_PATH = Path(config_dir + "/ralph-workflow-agents.toml")
    state_store_mod = sys.modules.get("ralph.skills._state_store")
    if state_store_mod is not None:
        capabilities_path = Path(config_dir + "/ralph-workflow-capabilities.json")
        state_store_mod.DEFAULT_STATE_PATH = capabilities_path
        state_store_mod.default_state_path = lambda: capabilities_path
    agy_log_path = Path(fake_home + "/.gemini/antigravity-cli/cli.log")
    agy_diagnostic_mod = sys.modules.get("ralph.agents._agy_upstream_diagnostic")
    if agy_diagnostic_mod is not None:
        agy_diagnostic_mod._AGY_CLI_LOG_PATH = agy_log_path
    smoke_plumbing_mod = sys.modules.get("ralph.pipeline.plumbing.smoke_plumbing")
    if smoke_plumbing_mod is not None:
        smoke_plumbing_mod._AGY_CLI_LOG_PATH = agy_log_path


_autouse_home_base_cache: dict[int, tuple[pytest.TempPathFactory, str]] = {}


def _autouse_home_base(tmp_path_factory: pytest.TempPathFactory) -> str:
    cached = _autouse_home_base_cache.get(id(tmp_path_factory))
    if cached is not None:
        return cached[1]
    base = str(tmp_path_factory.getbasetemp() / "autouse-home")
    _autouse_home_base_cache[id(tmp_path_factory)] = (tmp_path_factory, base)
    return base


def _apply_isolate_pipeline_thread_locals() -> None:
    set_last_captured_session_id(None)
    set_last_captured_retry_intent(cleared_agent_retry_intent())


def _configure_repo_identity(repo: Repo) -> None:
    writer = repo.config_writer()
    try:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgSign", "false")
        writer.set_value("core", "logAllRefUpdates", "false")
        writer.set_value("core", "fsync", "none")
        writer.set_value("gc", "auto", "0")
    finally:
        writer.release()
    exclude_path = Path(repo.git_dir) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a", encoding="utf-8") as f:
        f.write(".agent/\n")


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    template_root = tmp_path_factory.mktemp("git-template")
    fake_home = template_root / "home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    orig_home = os.environ.get("HOME")
    orig_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["HOME"] = str(fake_home)
    os.environ["XDG_CONFIG_HOME"] = str(fake_config)
    try:
        from git import Repo

        repo = Repo.init(template_root, initial_branch="main")
        try:
            _configure_repo_identity(repo)

            readme = template_root / "README.md"
            readme.write_text("test")
            repo.index.add(["README.md"])
            repo.index.commit("initial commit")
        finally:
            repo.close()
    finally:
        if orig_home is not None:
            os.environ["HOME"] = orig_home
        else:
            os.environ.pop("HOME", None)
        if orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)

    return template_root


@pytest.fixture
def tmp_git_repo(tmp_path: Path, _git_repo_template: Path) -> Path:
    repo_path = tmp_path / "repo"
    shutil.copytree(_git_repo_template, repo_path)
    return repo_path


@pytest.fixture
def sample_config_dict() -> dict[str, object]:
    return {
        "general": {
            "verbosity": 2,
            "developer_context": 1,
            "checkpoint_enabled": True,
        },
        "agents": {
            "claude": {
                "cmd": "claude",
                "output_flag": "--output-format=stream-json",
                "yolo_flag": "--permission-mode auto",
                "json_parser": "claude",
            },
            "opencode": {
                "cmd": "opencode",
                "output_flag": "--json-stream",
                "json_parser": "opencode",
            },
        },
        "agent_chains": {
            "development": ["claude", "opencode"],
            "review": ["claude"],
        },
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    return workspace_dir


@pytest.fixture
def minimal_agents_policy() -> AgentsPolicy:
    from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

    return AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(agents=["claude", "opencode"], max_retries=3),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "review": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "fix": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_commit": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "review": AgentDrainConfig(chain="review"),
            "review_analysis": AgentDrainConfig(chain="review_analysis"),
            "fix": AgentDrainConfig(chain="fix"),
            "review_commit": AgentDrainConfig(chain="review_commit"),
        },
    )


@pytest.fixture
def minimal_pipeline_policy() -> PipelinePolicy:
    from ralph.policy.models import (
        PhaseDefinition,
        PhaseTransition,
        PipelinePolicy,
        RecoveryPolicy,
    )

    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure="failed_terminal",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback="fix",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review"),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed_terminal",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


@pytest.fixture
def memory_workspace() -> MemoryWorkspace:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Test Prompt\n\nThis is a test prompt.")
    return ws


@pytest.fixture
def mock_agent_invoker(
    memory_workspace: MemoryWorkspace,
) -> MockAgentInvoker:
    from tests.integration._mock_agent_invoker import MockAgentInvoker

    return MockAgentInvoker(memory_workspace)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def initial_state() -> PipelineState:
    from ralph.pipeline.state import PipelineState

    return PipelineState(
        phase="planning",
        policy_entry_phase="planning",
        budget_caps={"iteration": 5},
    )


@pytest.fixture
def default_policy() -> tuple[AgentsPolicy, PipelinePolicy, ArtifactsPolicy]:
    from ralph.policy.models import (
        AgentChainConfig,
        AgentDrainConfig,
        AgentsPolicy,
        ArtifactsPolicy,
        PhaseDefinition,
        PhaseTransition,
        PipelinePolicy,
        RecoveryPolicy,
    )

    agents_policy = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(agents=["claude"], max_retries=3),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "planning_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "planning_analysis": AgentDrainConfig(chain="planning_analysis"),
        },
    )
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="development", on_loopback="planning"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed_terminal",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    return (agents_policy, pipeline_policy, ArtifactsPolicy())


@pytest.fixture
def cli_runner() -> object:
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def config_with_test_agent() -> UnifiedConfig:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, UnifiedConfig

    return UnifiedConfig(
        agents={
            "test-agent": AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )
        },
    )
