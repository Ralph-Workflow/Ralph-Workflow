"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


_EXIT_PREFLIGHT = 2


def _configure_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkspaceScope:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    return scope


def _fake_config() -> UnifiedConfig:
    return UnifiedConfig()


def _policy_bundle_for_testing() -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
                "complete": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
                "complete": AgentDrainConfig(chain="complete"),
            },
        ),
        pipeline=PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


class TestInlinePromptPersistence:
    """Tests for inline prompt persistence and quick-mode preflight bypass."""

    @pytest.mark.timeout_seconds(3)
    @pytest.mark.subprocess_e2e
    def test_inline_prompt_is_written_to_product_criteria_md(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_pipeline with inline_prompt writes to .agent/PRODUCT_CRITERIA.md."""
        scope = _configure_workspace(monkeypatch, tmp_path)
        monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
        monkeypatch.setattr(run_module.state, "run_func", lambda *_args, **_kwargs: 0)

        run_module.run_pipeline(inline_prompt="do a quick change")

        product_criteria = scope.root / ".agent" / "PRODUCT_CRITERIA.md"
        assert product_criteria.exists(), "PRODUCT_CRITERIA.md must be created for inline prompts"
        assert product_criteria.read_text(encoding="utf-8") == "do a quick change"

    @pytest.mark.timeout_seconds(3)
    @pytest.mark.subprocess_e2e
    def test_inline_prompt_bypasses_prompt_md_preflight(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Quick-mode run with inline_prompt succeeds even when workspace has no PROMPT.md."""
        # Set up workspace with .agent dir but no PROMPT.md
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        scope = WorkspaceScope(tmp_path)
        monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
        monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
        monkeypatch.setattr(run_module.state, "run_func", lambda *_args, **_kwargs: 0)

        assert not (tmp_path / "PROMPT.md").exists()
        result = run_module.run_pipeline(dry_run=True, inline_prompt="quick task")
        assert result == 0
