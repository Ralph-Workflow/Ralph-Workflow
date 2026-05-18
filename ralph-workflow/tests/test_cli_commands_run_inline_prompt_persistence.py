"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.theme import RALPH_THEME
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

    import pytest


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


class _CaptureConsole(Console):
    """A Rich Console that also captures output in .lines."""

    def __init__(self) -> None:
        super().__init__(
            file=StringIO(),
            color_system=None,
            force_terminal=False,
            theme=RALPH_THEME,
        )
        self._string_io = self.file
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        for arg in args:
            if isinstance(arg, Text):
                self.lines.append(arg.plain)
            else:
                self.lines.append(str(arg))
        super().print(*args, **kwargs)

    def getvalue(self) -> str:
        return self._string_io.getvalue()


class _RegistryWithFromConfigOnly:
    called_with: object | None = None

    @classmethod
    def from_config(cls, config: object) -> _RegistryWithFromConfigOnly:
        cls.called_with = config
        return cls()

    def get(self, _name: str) -> object:
        return object()


class TestInlinePromptPersistence:
    """Tests for inline prompt persistence and quick-mode preflight bypass."""

    def test_inline_prompt_is_written_to_current_prompt_md(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_pipeline with inline_prompt writes to .agent/CURRENT_PROMPT.md."""
        scope = _configure_workspace(monkeypatch, tmp_path)
        monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
        monkeypatch.setattr(run_module.state, "run_func", lambda *_args, **_kwargs: 0)

        run_module.run_pipeline(inline_prompt="do a quick change")

        current_prompt = scope.root / ".agent" / "CURRENT_PROMPT.md"
        assert current_prompt.exists(), "CURRENT_PROMPT.md must be created for inline prompts"
        assert current_prompt.read_text(encoding="utf-8") == "do a quick change"

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
