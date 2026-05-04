"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.pipeline.state import PipelineState
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
from ralph.policy.validation import PolicyValidationError
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_EXIT_PREFLIGHT = 2



def _policy_bundle_for_testing() -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
                "review": AgentChainConfig(agents=["claude"]),
                "review_analysis": AgentChainConfig(agents=["claude"]),
                "fix": AgentChainConfig(agents=["claude"]),
                "complete": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
                "review": AgentDrainConfig(chain="review"),
                "review_analysis": AgentDrainConfig(chain="review_analysis"),
                "fix": AgentDrainConfig(chain="fix"),
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
        self._string_io = self.file  # type: ignore[assignment]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        # Capture in lines for backward compatibility with existing tests
        for arg in args:
            if isinstance(arg, Text):
                self.lines.append(arg.plain)
            else:
                self.lines.append(str(arg))
        # Also print to parent (writes to StringIO)
        super().print(*args, **kwargs)

    def getvalue(self) -> str:
        return self._string_io.getvalue()


def _attach_display_context(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    console: _CaptureConsole,
) -> None:
    """Patch module's make_display_context to return a context with our captured console."""
    ctx = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=80,
        mode="wide",
        narrow=False,
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    def fake_make_display_context(**kwargs):
        return ctx

    monkeypatch.setattr(module, "make_display_context", fake_make_display_context)


_DEFAULT_DRAINS = [
    "planning",
    "planning_analysis",
    "development",
    "development_analysis",
    "development_commit",
]


def _fake_config() -> UnifiedConfig:
    return UnifiedConfig(
        agent_chains={d: ["claude"] for d in _DEFAULT_DRAINS},
        agent_drains={d: d for d in _DEFAULT_DRAINS},
    )


def _configure_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkspaceScope:
    (tmp_path / ".agent").mkdir()
    (tmp_path / "PROMPT.md").write_text("# Goal\n\nRun the pipeline.\n", encoding="utf-8")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    return scope


def test_run_pipeline_load_config_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[str] = []

    def raise_config(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - raises instantly
        raise RuntimeError("boom")

    def capture_error(message: str, *args: object, **kwargs: object) -> None:
        errors.append(message)

    monkeypatch.setattr(run_module, "load_config", raise_config)
    monkeypatch.setattr(run_module.logger, "error", capture_error)
    assert run_module.run_pipeline() == 1
    assert errors == ["Failed to load configuration: {}"]


def test_run_pipeline_resume_without_checkpoint_prints_notice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()
    _attach_display_context(monkeypatch, run_module, console)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: None)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline(resume=True) == 0
    assert any("No checkpoint found to resume from" in line for line in console.lines)


def test_run_pipeline_dry_run_reports_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    config = _fake_config()
    state = PipelineState(phase="development")

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: state)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    console = _CaptureConsole()
    _attach_display_context(monkeypatch, run_module, console)

    assert run_module.run_pipeline(dry_run=True, resume=True) == 0
    assert "Dry run mode" in console.lines[0]
    assert "Phase: development" in console.lines[1]


class _RegistryWithFromConfigOnly:
    called_with: object | None = None

    @classmethod
    def from_config(cls, config: object) -> _RegistryWithFromConfigOnly:
        cls.called_with = config
        return cls()

    def get(self, _name: str) -> object:
        return object()


def test_run_pipeline_builds_preflight_registry_from_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    config = _fake_config()
    console = _CaptureConsole()

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    _attach_display_context(monkeypatch, run_module, console)
    monkeypatch.setattr(run_module, "AgentRegistry", _RegistryWithFromConfigOnly)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    _RegistryWithFromConfigOnly.called_with = None

    assert run_module.run_pipeline() == 0
    assert _RegistryWithFromConfigOnly.called_with is config


def test_run_pipeline_preflight_uses_loaded_policy_bundle_even_when_it_is_not_a_policybundle_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    config = _fake_config()
    fake_bundle = type(
        "_FakeBundle",
        (),
        {
            "agents": type(
                "_Agents",
                (),
                {
                    "agent_chains": {
                        "development": type("_Chain", (), {"agents": ["ghost"]})()
                    }
                },
            )(),
            "pipeline": type(
                "_Pipeline",
                (),
                {"phases": {"development": object()}},
            )(),
        },
    )()

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(run_module, "load_policy", lambda *args, **kwargs: fake_bundle)
    monkeypatch.setattr(run_module, "AgentRegistry", _RegistryWithFromConfigOnly)
    monkeypatch.setattr(run_module, "_validate_loaded_policy_bundle", lambda bundle: None)
    monkeypatch.setattr(
        run_module,
        "validate_agent_chains_satisfiable",
        lambda bundle, registry: (_ for _ in ()).throw(PolicyValidationError("unknown agent")),
    )
    monkeypatch.setattr(run_module, "validate_recovery_config", lambda bundle: None)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline() == _EXIT_PREFLIGHT
    assert _RegistryWithFromConfigOnly.called_with is config


def test_run_pipeline_loads_policy_with_main_config_as_agents_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope = _configure_workspace(monkeypatch, tmp_path)
    config = _fake_config()
    captured: dict[str, object] = {}

    def fake_load_policy(policy_dir, *, config=None):
        captured["policy_dir"] = policy_dir
        captured["config"] = config
        return _policy_bundle_for_testing()

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(run_module, "load_policy", fake_load_policy)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline() == 0
    assert captured == {"policy_dir": scope.root / ".agent", "config": config}


def test_run_pipeline_runner_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()
    logged: list[str] = []

    _attach_display_context(monkeypatch, run_module, console)
    monkeypatch.setattr(
        run_module.logger, "error", lambda message, *args, **kwargs: logged.append(message)
    )
    monkeypatch.setattr(run_module, "_run_func", None)

    assert run_module.run_pipeline() == 1
    assert any("Pipeline runner is unavailable" in line for line in console.lines)
    assert logged and logged[-1] == "Pipeline runner is unavailable"


def test_run_pipeline_runner_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the runner raises an unexpected exception, run_pipeline returns 1 and
    shows an error on the console. This validates the observable behavior of the
    outer exception handler; it does not assert which specific logger method is called,
    since that is an implementation detail."""
    _configure_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: _fake_config())
    console = _CaptureConsole()

    def raising_runner(
        *args: object, **kwargs: object
    ) -> None:  # pragma: no cover - raises intentionally
        raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "_run_func", raising_runner)
    # Suppress any logging to avoid noise in test output
    monkeypatch.setattr(run_module.logger, "critical", lambda *args, **kwargs: None)
    _attach_display_context(monkeypatch, run_module, console)

    assert run_module.run_pipeline() == 1
    assert any("Pipeline failed" in line for line in console.lines)


def test_run_pipeline_injects_workspace_scope_when_config_path_is_implicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    (tmp_path / "PROMPT.md").write_text("Prompt\n")
    scope = WorkspaceScope(tmp_path)

    def fake_load_config(*args: object, **kwargs: object) -> UnifiedConfig:
        captured["kwargs"] = kwargs
        return _fake_config()

    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(run_module, "load_config", fake_load_config)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline() == 0
    assert captured["kwargs"] == {"workspace_scope": scope}


def test_standalone_run_module_is_not_a_cli_surface() -> None:
    """Cleanup should remove the standalone run.py CLI entry surface."""
    assert not hasattr(run_module, "app")


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
        monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

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
        monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

        assert not (tmp_path / "PROMPT.md").exists()
        result = run_module.run_pipeline(dry_run=True, inline_prompt="quick task")
        assert result == 0


class TestValidateCounterOverrides:
    """Tests for CLI counter override validation via the shared policy validator."""

    def _pipeline_with_counters(self, *counter_names: str) -> PipelinePolicy:
        from ralph.policy.models import BudgetCounterConfig  # noqa: PLC0415

        return PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    transitions=PhaseTransition(on_success="work"),
                )
            },
            entry_phase="work",
            terminal_phase="work",
            budget_counters={name: BudgetCounterConfig(default_max=5) for name in counter_names},
        )

    def test_unknown_counter_raises_policy_validation_error(self) -> None:
        from ralph.policy.validation import _validate_cli_counter_overrides  # noqa: PLC0415

        policy = self._pipeline_with_counters("declared_counter")
        errors: list[str] = []
        _validate_cli_counter_overrides(policy, {"unknown_counter": 3}, errors)
        assert any("unknown_counter" in e for e in errors)

    def test_declared_counter_passes_validation(self) -> None:
        from ralph.policy.validation import _validate_cli_counter_overrides  # noqa: PLC0415

        policy = self._pipeline_with_counters("my_counter")
        errors: list[str] = []
        _validate_cli_counter_overrides(policy, {"my_counter": 5}, errors)
        assert errors == []

    def test_empty_overrides_passes_validation(self) -> None:
        from ralph.policy.validation import _validate_cli_counter_overrides  # noqa: PLC0415

        policy = self._pipeline_with_counters()
        errors: list[str] = []
        _validate_cli_counter_overrides(policy, {}, errors)
        assert errors == []
