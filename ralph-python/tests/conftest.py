"""Shared fixtures for ralph test suite."""

from __future__ import annotations

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
)
from ralph.workspace.memory import MemoryWorkspace
from tests.integration.test_pipeline_happy_path import MockAgentInvoker

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the temporary git repository.
    """
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("test")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")

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
            "developer_iters": 5,
            "reviewer_reviews": 2,
            "developer_context": 1,
            "reviewer_context": 0,
            "review_depth": "standard",
            "checkpoint_enabled": True,
            "isolation_mode": True,
        },
        "agents": {
            "claude": {
                "cmd": "claude",
                "output_flag": "--output-format=stream-json",
                "yolo_flag": "--dangerously-skip-permissions",
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
                embeds_analysis=True,
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                requires_commit=True,
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure="failed",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                embeds_analysis=True,
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
                requires_commit=True,
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
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
