from __future__ import annotations

from pathlib import Path

import pytest

import ralph.pipeline.attempt_runner as attempt_runner_mod
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_recovery_record import continue_retained_resolution
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions


def test_continue_retained_resolution_fallback_uses_entry_point_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[AgentEntryPoint, dict[str, object]]] = []
    real_builder = attempt_runner_mod.build_entry_point_recovery_controller

    def spy_build(
        entry_point: AgentEntryPoint,
        *,
        config: attempt_runner_mod.UnifiedConfig | None,
        policy_bundle: attempt_runner_mod.PolicyBundle | None,
        deps: AttemptRunnerDeps,
        **kwargs: object,
    ) -> RecoveryController:
        call_kwargs: dict[str, object] = {
            "config": config,
            "policy_bundle": policy_bundle,
            "deps": deps,
            **kwargs,
        }
        calls.append((entry_point, call_kwargs))
        return real_builder(
            entry_point,
            config=config,
            policy_bundle=policy_bundle,
            deps=deps,
            **kwargs,
        )

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", spy_build)

    record = IntegrationRecord(
        phase="integrating",
        target="",
        pre_feature_sha="feature-sha",
        pre_target_sha=None,
    )

    result = continue_retained_resolution(
        tmp_path,
        record,
        None,
        None,
        None,
    )

    assert isinstance(result, str)
    assert len(calls) == 1
    entry_point, kwargs = calls[0]
    assert entry_point is AgentEntryPoint.INTEGRATION_CONTINUATION
    assert kwargs.get("config") is None
    deps = kwargs.get("deps")
    assert isinstance(deps, AttemptRunnerDeps)
    assert deps.invoke is PRODUCTION_INVOKE
    assert deps.probe_runner is None
    assert deps.workspace_probe is None
    assert deps.connectivity is None


def test_continue_retained_resolution_explicit_controller_bypasses_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[AgentEntryPoint, dict[str, object]]] = []

    def fail_build(entry_point: AgentEntryPoint, **kwargs: object) -> RecoveryController:
        calls.append((entry_point, kwargs))
        pytest.fail(
            "build_entry_point_recovery_controller should not be called when explicit controller is passed"
        )

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", fail_build)

    explicit_controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=5))

    record = IntegrationRecord(
        phase="integrating",
        target="",
        pre_feature_sha="feature-sha",
        pre_target_sha=None,
    )

    result = continue_retained_resolution(
        tmp_path,
        record,
        None,
        None,
        None,
        recovery_controller=explicit_controller,
    )

    assert isinstance(result, str)
    assert len(calls) == 0


def test_continue_retained_resolution_ambient_controller_bypasses_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[AgentEntryPoint, dict[str, object]]] = []

    def fail_build(entry_point: AgentEntryPoint, **kwargs: object) -> RecoveryController:
        calls.append((entry_point, kwargs))
        pytest.fail(
            "build_entry_point_recovery_controller should not be called when ambient controller exists"
        )

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", fail_build)

    ambient_controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=5))
    token = install_agent_effect_dependencies(recovery_controller=ambient_controller)
    try:
        record = IntegrationRecord(
            phase="integrating",
            target="",
            pre_feature_sha="feature-sha",
            pre_target_sha=None,
        )

        result = continue_retained_resolution(
            tmp_path,
            record,
            None,
            None,
            None,
        )

        assert isinstance(result, str)
        assert len(calls) == 0
    finally:
        reset_agent_effect_dependencies(token)
