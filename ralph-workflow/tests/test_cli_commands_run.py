"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
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

ACTIVE_AGENT_CHAINS = {
    "planning": ["claude"],
    "development": ["claude", "opencode"],
    "analysis": ["claude"],
    "review": ["claude"],
    "fix": ["claude"],
    "commit": ["claude"],
}

ACTIVE_AGENT_DRAINS = {
    "planning": "planning",
    "development": "development",
    "development_analysis": "analysis",
    "development_commit": "commit",
    "review": "review",
    "review_analysis": "analysis",
    "review_commit": "commit",
    "fix": "fix",
}


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


class _CaptureConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        self.lines.append(" ".join(str(arg) for arg in args))


def _fake_config(developer_iters: int = 1, reviewer_reviews: int = 1) -> UnifiedConfig:
    config = UnifiedConfig(
        agent_chains=dict(ACTIVE_AGENT_CHAINS),
        agent_drains=dict(ACTIVE_AGENT_DRAINS),
    )
    general = config.general.model_copy(
        update={
            "developer_iters": developer_iters,
            "reviewer_reviews": reviewer_reviews,
        }
    )
    return config.model_copy(update={"general": general})


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
    monkeypatch.setattr(run_module, "console", console)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: None)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    assert run_module.run_pipeline(resume=True) == 0
    assert any("No checkpoint found to resume from" in line for line in console.lines)


def test_run_pipeline_dry_run_reports_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_workspace(monkeypatch, tmp_path)
    config = _fake_config(developer_iters=4, reviewer_reviews=2)
    state = PipelineState(phase="review")

    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(run_module.ckpt, "load", lambda: state)
    monkeypatch.setattr(run_module, "_run_func", lambda *_args, **_kwargs: 0)

    console = _CaptureConsole()
    monkeypatch.setattr(run_module, "console", console)

    assert run_module.run_pipeline(dry_run=True, resume=True) == 0
    assert "Dry run mode" in console.lines[0]
    assert "Phase: review" in console.lines[1]
    assert "Iterations: 4" in console.lines[2]
    assert "Review passes: 2" in console.lines[3]


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
    monkeypatch.setattr(run_module, "console", console)
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

    monkeypatch.setattr(run_module, "console", console)
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
    monkeypatch.setattr(run_module, "console", console)

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
