"""Crash guards convert ordinary errors into recovery but never swallow control-flow exits.

The pipeline step, phase-handler and fan-out crash guards turn an
``Exception`` (and, by the recovery-first design, a ``SystemExit`` raised by
library code) into a recoverable phase failure. A ``BaseException`` that is
neither -- a harness timeout, ``GeneratorExit``, ``asyncio.CancelledError`` --
is a control-flow signal from outside the phase and must propagate unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import Effect, FanOutEffect, InvokeAgentEffect
from ralph.pipeline.events import Event, PhaseFailureEvent
from ralph.pipeline.fan_out import execute_fan_out_sync
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(5)


class _OutsideAbort(BaseException):
    """Stand-in for a harness timeout: a BaseException that is not an Exception."""


def _raise_outside_abort(*_args: object, **_kwargs: object) -> None:
    raise _OutsideAbort("harness deadline")


def _defaults_policy_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"


def _development_effect() -> InvokeAgentEffect:
    return InvokeAgentEffect(
        agent_name="claude/haiku",
        phase="development",
        prompt_file=".agent/tmp/development_prompt.md",
    )


def test_run_pipeline_step_propagates_non_exception_base_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = load_policy(_defaults_policy_dir())
    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["claude/haiku"])},
    )
    effect = _development_effect()
    monkeypatch.setattr(
        runner_module, "call_determine_effect_from_policy", lambda *_a, **_k: effect
    )
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_module, "invoke_execute_effect_with_optional_display", _raise_outside_abort
    )
    saved = MagicMock()
    monkeypatch.setattr(ckpt, "save", saved)
    display_context = make_display_context()

    with pytest.raises(_OutsideAbort):
        runner_module.run_pipeline_step(
            state=state,
            policy_bundle=bundle,
            workspace_scope=WorkspaceScope(tmp_path),
            config=MagicMock(),
            display=ParallelDisplay(display_context),
            display_context=display_context,
            verbosity=Verbosity.QUIET,
            registry=MagicMock(get=MagicMock(return_value=None)),
            pipeline_subscriber=None,
            recovery_controller=RecoveryController(
                options=RecoveryControllerOptions(policy_bundle=bundle, cycle_cap=10)
            ),
        )

    saved.assert_not_called()


def test_phase_event_after_agent_run_propagates_non_exception_base_exception(
    tmp_path: Path,
) -> None:
    def aborting_handler(effect: Effect, ctx: object) -> list[Event]:
        del effect, ctx
        raise _OutsideAbort("harness deadline")

    with pytest.raises(_OutsideAbort):
        runner_module.phase_event_after_agent_run(
            effect=_development_effect(),
            config=UnifiedConfig(),
            policy_bundle=MagicMock(),
            workspace=FsWorkspace(tmp_path),
            display_context=make_display_context(),
            handle_phase_fn=aborting_handler,
        )


def test_phase_event_after_agent_run_still_converts_ordinary_errors(tmp_path: Path) -> None:
    def failing_handler(effect: Effect, ctx: object) -> list[Event]:
        del effect, ctx
        raise RuntimeError("handler blew up")

    event = runner_module.phase_event_after_agent_run(
        effect=_development_effect(),
        config=UnifiedConfig(),
        policy_bundle=MagicMock(),
        workspace=FsWorkspace(tmp_path),
        display_context=make_display_context(),
        handle_phase_fn=failing_handler,
    )

    assert isinstance(event, PhaseFailureEvent)
    assert event.recoverable is True
    assert "RuntimeError" in event.reason


def _run_fan_out_with_install(tmp_path: Path, install: object) -> PipelineState:
    bundle = load_policy(_defaults_policy_dir())
    unit = WorkUnit(unit_id="unit-a", description="A")
    state = PipelineState(
        phase="development",
        work_units=(unit,),
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    return execute_fan_out_sync(
        effect=FanOutEffect(work_units=(unit,), max_workers=1),
        state=state,
        display=ParallelDisplay(make_display_context()),
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        _install_signal_handlers=install,
    )


def test_fan_out_propagates_non_exception_base_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved = MagicMock()
    monkeypatch.setattr(ckpt, "save", saved)

    with pytest.raises(_OutsideAbort):
        _run_fan_out_with_install(tmp_path, _raise_outside_abort)

    saved.assert_not_called()


def test_fan_out_still_converts_ordinary_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing_install(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("install blew up")

    monkeypatch.setattr(ckpt, "save", MagicMock())

    recovered = _run_fan_out_with_install(tmp_path, failing_install)

    assert recovered.last_error is not None
    assert "RuntimeError" in recovered.last_error
