"""Shared fixtures for ralph test suite."""

from __future__ import annotations

import hashlib
import signal
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.config.prompt_helper_config import PromptHelperConfig
from ralph.pipeline.state import PipelineState
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
from ralph.runtime import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    timeout_seconds_from_env,
)
from ralph.workspace.memory import MemoryWorkspace

pytest_plugins = ("ralph.testing.pytest_timeout_plugin",)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path
    from types import FrameType

    from tests.integration._mock_agent_invoker import MockAgentInvoker


class TestExecutionTimeoutError(TimeoutError):
    pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    timeout_marker = item.get_closest_marker("timeout_seconds")
    if timeout_marker is not None and timeout_marker.args:
        timeout_seconds = float(timeout_marker.args[0])
    else:
        timeout_seconds = timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS)

    def _handle_timeout(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        raise TestExecutionTimeoutError(f"test exceeded {timeout_seconds} seconds: {item.nodeid}")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.fixture(autouse=True)
def _isolate_process_home(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Redirect user-level config lookups to a unique lightweight sandbox per test.

    The sandbox path is derived from the test node id under the worker's base temp dir,
    but the directories are not created eagerly. That keeps per-test isolation for HOME /
    XDG config lookups without paying the cost of materializing a fresh tmp_path tree for
    every test in the full parallel suite.
    """

    worker_root = tmp_path_factory.getbasetemp() / "autouse-home"
    unique_suffix = hashlib.sha1(request.node.nodeid.encode("utf-8")).hexdigest()
    fake_home = worker_root / unique_suffix / "home"
    fake_xdg = fake_home / ".config"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))
    monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)


def _configure_repo_identity(repo: object) -> None:
    writer = repo.config_writer()
    try:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
    finally:
        writer.release()


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a reusable template git repository for fast per-test clones."""
    from git import Repo

    template_root = tmp_path_factory.mktemp("git-template")
    repo = Repo.init(template_root)
    try:
        _configure_repo_identity(repo)

        readme = template_root / "README.md"
        readme.write_text("test")
        repo.index.add(["README.md"])
        repo.index.commit("initial commit")
    finally:
        repo.close()

    return template_root


@pytest.fixture
def tmp_git_repo(tmp_path: Path, _git_repo_template: Path) -> Path:
    """Create a temporary git repository for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the temporary git repository.
    """
    from git import Repo

    repo_path = tmp_path / "repo"
    repo = Repo.init(str(repo_path))
    try:
        readme_src = _git_repo_template / "README.md"
        readme_dst = repo_path / "README.md"
        readme_dst.write_text(readme_src.read_text())
        repo.index.add(["README.md"])
        repo.index.commit("initial commit")
        _configure_repo_identity(repo)
    finally:
        repo.close()
    return repo_path


@pytest.fixture
def sample_config_dict() -> dict[str, object]:
    """Sample configuration dictionary for testing.

    Returns:
        Dictionary with sample config values.
    """
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
                "can_commit": True,
                "json_parser": "claude",
            },
            "opencode": {
                "cmd": "opencode",
                "output_flag": "--json-stream",
                "can_commit": False,
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
    """Create a workspace directory for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the workspace directory.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    return workspace_dir


@pytest.fixture
def minimal_agents_policy() -> AgentsPolicy:
    """Create a minimal agents policy for testing.

    Returns:
        AgentsPolicy with planning and development chains/drains.
    """
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
    """Create a minimal pipeline policy for testing.

    Returns:
        PipelinePolicy with standard phase routing.
    """
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
    """Create an in-memory workspace for testing.

    Returns:
        MemoryWorkspace instance with a default prompt file.
    """
    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Test Prompt\n\nThis is a test prompt.")
    return ws


@pytest.fixture
def mock_agent_invoker(
    memory_workspace: MemoryWorkspace,
) -> MockAgentInvoker:
    """Create a mock agent invoker for testing.

    Args:
        memory_workspace: In-memory workspace fixture.

    Returns:
        MockAgentInvoker instance.
    """
    from tests.integration._mock_agent_invoker import MockAgentInvoker  # lazy import

    return MockAgentInvoker(memory_workspace)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory for integration tests."""
    return tmp_path


@pytest.fixture
def initial_state() -> PipelineState:
    """Create a default initial pipeline state for integration tests."""
    return PipelineState(
        phase="planning",
        policy_entry_phase="planning",
        budget_caps={"iteration": 5},
    )


@pytest.fixture
def default_policy() -> tuple[AgentsPolicy, PipelinePolicy, ArtifactsPolicy]:
    """Create a default policy tuple for integration tests."""
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
    """Provide a Typer CLI test runner."""
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Return a temporary workspace root."""
    return tmp_path


@pytest.fixture
def config_with_helper_agent() -> UnifiedConfig:
    """Return a UnifiedConfig with prompt-helper-agent in the agents dict."""
    return UnifiedConfig(
        prompt_helper=PromptHelperConfig(agent="prompt-helper-agent"),
        agents={
            "prompt-helper-agent": AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )
        },
    )
