"""Tests for baseline capability degradation warning in run.py."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from rich.panel import Panel

from ralph.cli.commands import run as run_module
from ralph.cli.commands._load_result import _LoadResult
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
from ralph.skills._state import CapabilityEntry, CapabilityState, CapabilityStatus
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _fake_policy_bundle() -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "complete": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
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
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


def _configure_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkspaceScope:
    """Set up a minimal workspace with .agent dir and mocked scope resolution."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    return scope


def _mock_load_result(scope: WorkspaceScope) -> _LoadResult:
    """Return a valid _LoadResult for testing."""
    return _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=scope,
        initial_state=None,
        policy_bundle=_fake_policy_bundle(),
    )


class TestWarnIfCapabilitiesDegraded:
    """Tests for the _warn_if_capabilities_degraded helper and its integration."""

    def test_warning_panel_printed_when_web_search_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When web_search is degraded, the Baseline Capability Warning panel is printed."""
        scope = _configure_workspace(monkeypatch, tmp_path)

        # Set up a fake capability state file with degraded web_search
        fake_state_file = tmp_path / ".ralph_capabilities.json"
        cap_state = CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_DEGRADED),
            visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
        )
        fake_state_file.write_text(cap_state.model_dump_json(), encoding="utf-8")

        # Mock default_state_path to return our temp file
        monkeypatch.setattr(run_module, "default_state_path", lambda: fake_state_file)

        # Mock SkillManager to return a mock with degraded web_search
        mock_manager = MagicMock()
        mock_manager.check_baseline_health.return_value = {
            "web_search": False,
            "visit_url": True,
            "docs_mcp": False,
            "skills": True,
        }
        monkeypatch.setattr(run_module, "SkillManager", lambda *args, **kwargs: mock_manager)

        # Capture console output
        mock_console = MagicMock()

        # Call the function directly
        run_module._warn_if_capabilities_degraded(mock_console, scope.root)

        # Verify warning panel was printed
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args
        panel = call_args[0][0]
        assert isinstance(panel, Panel)
        assert panel.title == "Baseline Capability Warning"

    def test_no_warning_when_state_file_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When no capability state file exists (first run), no warning is printed."""
        scope = _configure_workspace(monkeypatch, tmp_path)

        # Mock default_state_path to return a non-existent path
        fake_state_file = tmp_path / "nonexistent_capabilities.json"
        monkeypatch.setattr(run_module, "default_state_path", lambda: fake_state_file)

        mock_console = MagicMock()

        run_module._warn_if_capabilities_degraded(mock_console, scope.root)

        mock_console.print.assert_not_called()

    def test_no_warning_when_all_mandatory_capabilities_healthy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When all three mandatory capabilities are healthy, no warning is printed."""
        scope = _configure_workspace(monkeypatch, tmp_path)

        # Set up a healthy capability state
        fake_state_file = tmp_path / ".ralph_capabilities_healthy.json"
        cap_state = CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
        )
        fake_state_file.write_text(cap_state.model_dump_json(), encoding="utf-8")

        monkeypatch.setattr(run_module, "default_state_path", lambda: fake_state_file)

        mock_manager = MagicMock()
        mock_manager.check_baseline_health.return_value = {
            "web_search": True,
            "visit_url": True,
            "docs_mcp": True,
            "skills": True,
        }
        monkeypatch.setattr(run_module, "SkillManager", lambda *args, **kwargs: mock_manager)

        mock_console = MagicMock()

        run_module._warn_if_capabilities_degraded(mock_console, scope.root)

        mock_console.print.assert_not_called()

    def test_warning_emitted_for_degraded_visit_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When visit_url is degraded, the Baseline Capability Warning panel is printed."""
        scope = _configure_workspace(monkeypatch, tmp_path)

        fake_state_file = tmp_path / ".ralph_capabilities_visit_url_degraded.json"
        cap_state = CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            visit_url=CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
        )
        fake_state_file.write_text(cap_state.model_dump_json(), encoding="utf-8")

        monkeypatch.setattr(run_module, "default_state_path", lambda: fake_state_file)

        mock_manager = MagicMock()
        mock_manager.check_baseline_health.return_value = {
            "web_search": True,
            "visit_url": False,
            "docs_mcp": False,
            "skills": True,
        }
        monkeypatch.setattr(run_module, "SkillManager", lambda *args, **kwargs: mock_manager)

        mock_console = MagicMock()

        run_module._warn_if_capabilities_degraded(mock_console, scope.root)

        mock_console.print.assert_called_once()
        panel = mock_console.print.call_args[0][0]
        assert isinstance(panel, Panel)
        assert panel.title == "Baseline Capability Warning"

    def test_warning_emitted_for_degraded_skills(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When skills bundle is degraded, the Baseline Capability Warning panel is printed."""
        scope = _configure_workspace(monkeypatch, tmp_path)

        fake_state_file = tmp_path / ".ralph_capabilities_skills_degraded.json"
        cap_state = CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_OUTDATED),
        )
        fake_state_file.write_text(cap_state.model_dump_json(), encoding="utf-8")

        monkeypatch.setattr(run_module, "default_state_path", lambda: fake_state_file)

        mock_manager = MagicMock()
        mock_manager.check_baseline_health.return_value = {
            "web_search": True,
            "visit_url": True,
            "docs_mcp": False,
            "skills": False,
        }
        monkeypatch.setattr(run_module, "SkillManager", lambda *args, **kwargs: mock_manager)

        mock_console = MagicMock()

        run_module._warn_if_capabilities_degraded(mock_console, scope.root)

        mock_console.print.assert_called_once()
        panel = mock_console.print.call_args[0][0]
        assert isinstance(panel, Panel)
        assert panel.title == "Baseline Capability Warning"
