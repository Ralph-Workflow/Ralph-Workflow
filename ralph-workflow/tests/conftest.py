"""Shared fixtures for ralph test suite."""

from __future__ import annotations

import shutil
import signal
import threading
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
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
from tests.integration.test_pipeline_happy_path import MockAgentInvoker

if TYPE_CHECKING:
    from pathlib import Path


class TestExecutionTimeoutError(TimeoutError):
    pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    timeout_marker = item.get_closest_marker("timeout_seconds")
    if timeout_marker is not None and timeout_marker.args:
        timeout_seconds = float(timeout_marker.args[0])
    else:
        timeout_seconds = timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS)

    def _handle_timeout(signum: int, frame) -> None:
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


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a reusable template git repository for fast per-test copies."""
    template_root = tmp_path_factory.mktemp("git-template")
    repo = Repo.init(template_root)
    writer = repo.config_writer()
    writer.set_value("user", "name", "Test User")
    writer.set_value("user", "email", "test@example.com")
    writer.release()

    readme = template_root / "README.md"
    readme.write_text("test")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")

    return template_root


@pytest.fixture
def tmp_git_repo(tmp_path: Path, _git_repo_template: Path) -> Path:
    """Create a temporary git repository for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the temporary git repository.
    """
    shutil.copytree(_git_repo_template, tmp_path, dirs_exist_ok=True)
    return tmp_path


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
            "reviewer_context": 0,
            "review_depth": "standard",
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
    return MockAgentInvoker(memory_workspace)


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect XDG_CONFIG_HOME to a temp dir and clear upstream MCP env for every test.

    Prevents bootstrap helpers from writing to the real ~/.config during tests.
    Clears RALPH_UPSTREAM_MCP_CONFIG so tests don't inherit real upstream server
    configs from the parent process, which would cause real network I/O.
    Tests that need specific upstream configs must set them explicitly via monkeypatch.
    """
    config_dir = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)
