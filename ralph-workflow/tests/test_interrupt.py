from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import pytest


INTERRUPTED_EXIT_CODE = 130

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
    "planning_analysis": "analysis",
    "development": "development",
    "development_analysis": "analysis",
    "development_commit": "commit",
    "review": "review",
    "review_analysis": "analysis",
    "review_commit": "commit",
    "fix": "fix",
}


def _install_test_dependency_stubs() -> None:
    if "loguru" not in sys.modules:
        loguru_module = types.ModuleType("loguru")
        cast("Any", loguru_module).logger = types.SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        sys.modules["loguru"] = loguru_module

    if "rich.console" not in sys.modules:
        rich_module = sys.modules.setdefault("rich", types.ModuleType("rich"))
        console_module = types.ModuleType("rich.console")

        class Console:
            def print(self, *args: object, **kwargs: object) -> None:
                return None

        cast("Any", console_module).Console = Console
        cast("Any", rich_module).console = console_module
        sys.modules["rich.console"] = console_module


_install_test_dependency_stubs()

runner_module = importlib.import_module("ralph.pipeline.runner")
interrupt_module = importlib.import_module("ralph.interrupt")
config_models = importlib.import_module("ralph.config.models")
pipeline_effects = importlib.import_module("ralph.pipeline.effects")
pipeline_state_module = importlib.import_module("ralph.pipeline.state")
policy_loader_module = importlib.import_module("ralph.policy.loader")
workspace_scope_module = importlib.import_module("ralph.workspace.scope")

GeneralConfig = config_models.GeneralConfig
UnifiedConfig = config_models.UnifiedConfig
run_pipeline_runner = runner_module.run
PipelineState = pipeline_state_module.PipelineState
WorkspaceScope = workspace_scope_module.WorkspaceScope
load_policy = policy_loader_module.load_policy

GeneralConfig.model_rebuild(_types_namespace={"Path": Path})
UnifiedConfig.model_rebuild(_types_namespace={"Path": Path})


def _config_with_agent_policy() -> UnifiedConfig:
    return UnifiedConfig(
        agent_chains=dict(ACTIVE_AGENT_CHAINS),
        agent_drains=dict(ACTIVE_AGENT_DRAINS),
    )


class RunCommandModule(Protocol):
    ckpt: Any
    console: Any
    _state: Any

    def load_config(self, *args: object, **kwargs: object) -> UnifiedConfig: ...

    def run_pipeline(
        self,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
        dry_run: bool = False,
        resume: bool = False,
    ) -> int: ...


def _load_run_command_module() -> RunCommandModule:
    module_path = Path(__file__).resolve().parents[1] / "ralph" / "cli" / "commands" / "run.py"
    spec = importlib.util.spec_from_file_location("test_run_command_module", module_path)
    if spec is None or spec.loader is None:
        raise AssertionError("Failed to load run command module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("RunCommandModule", module)


run_command_module = _load_run_command_module()


def test_request_user_interrupt_sets_flag() -> None:
    state_module = importlib.import_module("ralph.interrupt.state")
    importlib.reload(state_module)
    module = importlib.reload(interrupt_module)
    assert not module.user_interrupted_occurred()
    module.request_user_interrupt()
    assert module.user_interrupted_occurred()


def test_runner_saves_interrupted_checkpoint_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_states: list[PipelineState] = []

    def raise_keyboard_interrupt(
        _state: PipelineState,
        _bundle: object,
        _workspace_scope: object,
    ) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "_determine_effect_from_policy", raise_keyboard_interrupt)
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: load_policy(defaults_dir)
    )

    state = PipelineState(phase="planning")

    exit_code = run_pipeline_runner(_config_with_agent_policy(), state)

    assert exit_code == INTERRUPTED_EXIT_CODE
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True


def test_run_pipeline_saves_interrupted_resume_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved_states: list[PipelineState] = []
    resumed_state = PipelineState(phase="development", interrupted_by_user=False)

    (tmp_path / ".agent").mkdir()
    (tmp_path / "PROMPT.md").write_text("# Goal\n\nResume the pipeline.\n", encoding="utf-8")
    monkeypatch.setattr(
        run_command_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    monkeypatch.setattr(
        run_command_module, "load_config", lambda *_args, **_kwargs: _config_with_agent_policy()
    )
    monkeypatch.setattr(run_command_module.ckpt, "load", lambda: resumed_state)
    monkeypatch.setattr(run_command_module.ckpt, "save", saved_states.append)

    def raise_keyboard_interrupt(
        config: UnifiedConfig,
        initial_state: PipelineState | None = None,
    ) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(run_command_module._state, "run_func", raise_keyboard_interrupt)

    exit_code = run_command_module.run_pipeline(resume=True)

    assert exit_code == INTERRUPTED_EXIT_CODE
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True
