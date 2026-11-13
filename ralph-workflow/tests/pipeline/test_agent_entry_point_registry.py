from __future__ import annotations

from importlib import import_module

import pytest

from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import registered_entry_points

_ENTRY_POINT_MODULES = (
    "ralph.pipeline.runner",
    "ralph.pipeline.parallel.worker_runtime",
    "ralph.pipeline._integration_continuation",
    "ralph.pipeline.conflict_resolution.session",
    "ralph.pipeline.plumbing.commit_plumbing",
    "ralph.pipeline.plumbing.smoke_plumbing",
    "ralph.project_policy.cli_integration",
    "ralph._session_runtime_runtime",
    "ralph.recovery.canary_probe",
)


@pytest.mark.timeout_seconds(3.0)
def test_every_agent_entry_point_registers_from_its_owning_module() -> None:
    for module_name in _ENTRY_POINT_MODULES:
        import_module(module_name)

    assert set(AgentEntryPoint) == set(registered_entry_points())
