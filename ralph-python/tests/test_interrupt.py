from __future__ import annotations

import importlib
import importlib.util
import signal
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import pytest


INTERRUPTED_EXIT_CODE = 130


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

GeneralConfig = config_models.GeneralConfig
UnifiedConfig = config_models.UnifiedConfig
run_pipeline_runner = runner_module.run
PipelineState = pipeline_state_module.PipelineState

GeneralConfig.model_rebuild(_types_namespace={"Path": Path})
UnifiedConfig.model_rebuild(_types_namespace={"Path": Path})


class RunCommandModule(Protocol):
    ckpt: Any
    console: Any
    _run_func: Any

    def load_config(self, *args: object, **kwargs: object) -> UnifiedConfig: ...

    def run_pipeline(
        self,
        config_path: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
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


def test_interrupt_handler_sets_flag() -> None:
    module = importlib.reload(interrupt_module)
    previous_handler = signal.getsignal(signal.SIGINT)

    assert not module.user_interrupted_occurred()

    try:
        module.setup_interrupt_handler()
        signal.raise_signal(signal.SIGINT)
        assert module.user_interrupted_occurred()
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def test_runner_saves_interrupted_checkpoint_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_states: list[PipelineState] = []

    def raise_keyboard_interrupt(
        _state: PipelineState,
        _bundle: object,
    ) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "_determine_effect_from_policy", raise_keyboard_interrupt)
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)

    state = PipelineState(phase="planning")

    exit_code = run_pipeline_runner(UnifiedConfig(), state)

    assert exit_code == INTERRUPTED_EXIT_CODE
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True


def test_run_pipeline_saves_interrupted_resume_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_states: list[PipelineState] = []
    resumed_state = PipelineState(phase="development", interrupted_by_user=False)

    monkeypatch.setattr(
        run_command_module, "load_config", lambda *_args, **_kwargs: UnifiedConfig()
    )
    monkeypatch.setattr(run_command_module.ckpt, "load", lambda: resumed_state)
    monkeypatch.setattr(run_command_module.ckpt, "save", saved_states.append)
    monkeypatch.setattr(run_command_module.console, "print", lambda *args, **kwargs: None)

    def raise_keyboard_interrupt(
        config: UnifiedConfig,
        initial_state: PipelineState | None = None,
    ) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(run_command_module, "_run_func", raise_keyboard_interrupt)

    exit_code = run_command_module.run_pipeline(resume=True)

    assert exit_code == INTERRUPTED_EXIT_CODE
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True
