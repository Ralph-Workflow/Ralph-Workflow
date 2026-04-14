from __future__ import annotations

import importlib
import importlib.util
import signal
import sys
import types
from pathlib import Path
from typing import Any, Protocol, cast

import pytest


def _install_test_dependency_stubs() -> None:
    if "loguru" not in sys.modules:
        loguru_module = types.ModuleType("loguru")
        setattr(
            loguru_module,
            "logger",
            types.SimpleNamespace(
                info=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            ),
        )
        sys.modules["loguru"] = loguru_module

    if "rich.console" not in sys.modules:
        rich_module = sys.modules.setdefault("rich", types.ModuleType("rich"))
        console_module = types.ModuleType("rich.console")

        class Console:
            def print(self, *args: object, **kwargs: object) -> None:
                return None

        setattr(console_module, "Console", Console)
        setattr(rich_module, "console", console_module)
        sys.modules["rich.console"] = console_module


_install_test_dependency_stubs()

from ralph import interrupt as interrupt_module
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.pipeline.runner import run as run_pipeline_runner
import ralph.pipeline.runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.state import PipelineState

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
    return cast(RunCommandModule, module)


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

    monkeypatch.setattr(
        runner_module,
        "_determine_effect",
        lambda state, config: PreparePromptEffect(phase=state.phase, iteration=state.iteration),
    )

    def raise_keyboard_interrupt(
        effect: object,
        config: UnifiedConfig,
    ) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "_execute_effect", raise_keyboard_interrupt)
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)

    state = PipelineState(phase="planning")

    exit_code = run_pipeline_runner(UnifiedConfig(), state)

    assert exit_code == 130
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True


def test_run_pipeline_saves_interrupted_resume_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_states: list[PipelineState] = []
    resumed_state = PipelineState(phase="development", interrupted_by_user=False)

    monkeypatch.setattr(run_command_module, "load_config", lambda *_args, **_kwargs: UnifiedConfig())
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

    assert exit_code == 130
    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True
