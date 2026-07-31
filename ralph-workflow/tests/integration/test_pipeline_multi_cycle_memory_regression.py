"""Twelve-cycle regression harness for pipeline retained memory and elapsed drift.

Twelve invocations make a linear 1 MiB/cycle leak exceed 10 MiB after the
warmup window, well above tracemalloc noise while remaining fast enough for the
60-second verification budget.
"""

from __future__ import annotations

import gc
import json
import statistics
import time
import tracemalloc
import uuid
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.integration.test_pipeline_memory_regression_helper__configstub import _ConfigStub
from tests.integration.test_pipeline_memory_regression_helper__registryfactory import (
    _RegistryFactory,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pytest import MonkeyPatch

    from ralph.config.models import AgentConfig, UnifiedConfig

pytestmark = [pytest.mark.timeout_seconds(10)]

_CYCLES = 12
_WARMUP_CYCLES = 2
_LINE_COUNT = 8
_LINE_SIZE = 1024
_RETAINED_SPREAD_LIMIT = 512_000
# ponytail: a 3x band tolerates shared-CI jitter; tighten only with stable runners.
_MAX_ELAPSED_DRIFT_FACTOR = 3.0


class _SharedFakeBridge:
    def __init__(self) -> None:
        self.run_id = str(uuid.uuid4())

    def shutdown(self) -> None:
        return None

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def reset_tool_registry(self) -> None:
        return None


_BRIDGE = _SharedFakeBridge()


def _bridge_factory(**_kwargs: object) -> _SharedFakeBridge:
    return _BRIDGE


def _config() -> UnifiedConfig:
    return _ConfigStub()


def _invoke(
    config: AgentConfig, prompt_file: str, *, options: object | None = None
) -> Iterator[str]:
    del config, prompt_file, options
    session_payload: dict[str, str] = {"session_id": "multi-cycle"}
    yield json.dumps(session_payload)
    payload = "x" * _LINE_SIZE
    for index in range(_LINE_COUNT):
        yield f"development:{index}:{payload}"


def _ralph_bytes() -> int:
    snapshot = tracemalloc.take_snapshot().filter_traces(
        (
            tracemalloc.Filter(inclusive=True, filename_pattern="*ralph/*"),
            tracemalloc.Filter(inclusive=False, filename_pattern="*tests/*"),
            tracemalloc.Filter(inclusive=False, filename_pattern="*/site-packages/*"),
        )
    )
    return sum(stat.size for stat in snapshot.statistics("filename"))


@pytest.mark.integration
def test_pipeline_multi_cycle_memory_regression_no_drift(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    del monkeypatch
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
    display_context = make_display_context()
    deps = make_test_pipeline_deps(
        display_context=display_context,
        registry_factory=_RegistryFactory.from_config,
        bridge_factory=_bridge_factory,
    )
    retained: list[int] = []
    elapsed_seconds: list[float] = []
    gc.collect()
    tracemalloc.start()
    baseline = _ralph_bytes()
    try:
        for _ in range(_CYCLES):
            started = time.perf_counter()
            assert (
                runner_module.execute_agent_effect(
                    effect,
                    _config(),
                    deps,
                    WorkspaceScope(tmp_path),
                    display_context=display_context,
                    verbosity=Verbosity.QUIET,
                    invoke_agent=_invoke,
                    agent_invocation_error=AgentInvocationError,
                )
                == PipelineEvent.AGENT_SUCCESS
            )
            gc.collect()
            retained.append(_ralph_bytes() - baseline)
            elapsed_seconds.append(time.perf_counter() - started)
    finally:
        tracemalloc.stop()

    post_warmup = retained[_WARMUP_CYCLES:]
    post_warmup_elapsed = elapsed_seconds[_WARMUP_CYCLES:]
    early_retained, late_retained = post_warmup[:2], post_warmup[-2:]
    early_elapsed, late_elapsed = post_warmup_elapsed[:2], post_warmup_elapsed[-2:]
    retained_spread = max(post_warmup) - min(post_warmup)
    early_elapsed_mean = statistics.mean(early_elapsed)
    late_elapsed_mean = statistics.mean(late_elapsed)
    print(
        "pipeline_multi_cycle_memory_characterization "
        f"cycles={_CYCLES} warmup={_WARMUP_CYCLES} "
        f"early_retained_mean={statistics.mean(early_retained):.0f} "
        f"late_retained_mean={statistics.mean(late_retained):.0f} "
        f"retained_spread={retained_spread} "
        f"early_elapsed_mean_ms={early_elapsed_mean * 1000:.3f} "
        f"late_elapsed_mean_ms={late_elapsed_mean * 1000:.3f}"
    )
    assert retained_spread <= _RETAINED_SPREAD_LIMIT
    assert late_elapsed_mean <= early_elapsed_mean * _MAX_ELAPSED_DRIFT_FACTOR, (
        "late-cycle elapsed mean exceeded the shared-CI jitter band "
        f"(early={early_elapsed_mean * 1000:.3f}ms, "
        f"late={late_elapsed_mean * 1000:.3f}ms, "
        f"factor={_MAX_ELAPSED_DRIFT_FACTOR})"
    )
