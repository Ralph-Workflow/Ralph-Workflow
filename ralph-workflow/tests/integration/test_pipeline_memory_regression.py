"""Regression harness for bounded runner agent-output retention."""

from __future__ import annotations

import gc
import json
import tracemalloc
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pytest import MonkeyPatch

    from ralph.config.models import AgentConfig, UnifiedConfig
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.integration.test_pipeline_memory_regression_helper__configstub import _ConfigStub
from tests.integration.test_pipeline_memory_regression_helper__registryfactory import (
    _RegistryFactory,
)

_LINE_COUNT = 32
_LINE_SIZE = 2048
_ITERATION_COUNT = 5
_RETAINED_DELTA_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 6_000_000


def _config() -> UnifiedConfig:
    return cast("UnifiedConfig", _ConfigStub())


def _fake_invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: object | None = None,
) -> Iterator[str]:
    del config, prompt_file, options
    session_payload: dict[str, str] = {"session_id": "sess-development"}
    yield json.dumps(session_payload)
    payload = "x" * _LINE_SIZE
    for idx in range(1, _LINE_COUNT):
        yield f"development:{idx}:{payload}"


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_run_pipeline_memory_regression(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    del monkeypatch

    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development",
        prompt_file="PROMPT.md",
    )
    display_context = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context=display_context,
        registry_factory=_RegistryFactory.from_config,
    )
    workspace_scope = WorkspaceScope(tmp_path)

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
        event = runner_module.execute_agent_effect(
            effect,
            _config(),
            pipeline_deps,
            workspace_scope,
            display_context=display_context,
            verbosity=Verbosity.QUIET,
            invoke_agent=_fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
        )
        assert event == PipelineEvent.AGENT_SUCCESS

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained_delta_bytes = final_current - baseline_current
    peak_delta_bytes = peak_current - baseline_current

    assert retained_delta_bytes <= _RETAINED_DELTA_LIMIT
    assert peak_delta_bytes <= _PEAK_DELTA_LIMIT
