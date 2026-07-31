"""Regression harness for bounded runner agent-output retention.

These tests are subprocess_e2e: they exercise the real pipeline runner
with tracemalloc snapshots across multi-iteration loops that cannot
fit the per-test 1 s budget.

Characterization notes (wt-024 memory-perf):
- Cycle count is sized to expose sub-kilobyte-per-cycle retention that
  would accumulate fatally over an hours-long run (detectable floor
  ~512 B/cycle). Eight post-warmup cycles still yield ~4 KiB if
  retention were linear at that floor — well below the 256 KiB spread
  cap — while keeping suite wall-clock under the e2e budget.
- Bridge factories used here MUST NOT retain per-call argument dicts;
  ``_RecordingBridgeFactory.calls`` is a test-only accumulator and
  would mask production retention behind harness noise.
- Early/late timing numbers are characterization evidence reported by
  the test; the hard assertion is retained-state plateau, not a
  brittle wall-clock threshold.
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

pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.subprocess_e2e]

_LINE_COUNT = 32
_LINE_SIZE = 2048
_WARMUP_COUNT = 2
_ITERATION_COUNT = 8
_WINDOW = 4
_RETAINED_SPREAD_LIMIT = 256_000
_RETAINED_DELTA_LIMIT = 512_000
_PEAK_DELTA_LIMIT = 2_000_000


class _SharedFakeBridge:
    """Minimal SessionBridgeLike stand-in reused across cycles."""

    def __init__(self) -> None:
        self.run_id = str(uuid.uuid4())

    def shutdown(self) -> None:
        return None

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def reset_tool_registry(self) -> None:
        return None


_SHARED_BRIDGE = _SharedFakeBridge()


def _shared_bridge_factory(**_kwargs: object) -> _SharedFakeBridge:
    """Return a singleton fake bridge without retaining call arguments."""
    del _kwargs
    return _SHARED_BRIDGE


def _config() -> UnifiedConfig:
    return _ConfigStub()


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


def _traced_bytes_from_ralph() -> int:
    """Bytes currently traced to ralph package allocations.

    Process-wide totals include unrelated daemon threads; scoping to
    ``ralph/*`` (excluding tests/site-packages) is the signal under test.
    """
    snapshot = tracemalloc.take_snapshot().filter_traces(
        (
            tracemalloc.Filter(inclusive=True, filename_pattern="*ralph/*"),
            tracemalloc.Filter(inclusive=False, filename_pattern="*tests/*"),
            tracemalloc.Filter(inclusive=False, filename_pattern="*/site-packages/*"),
        )
    )
    return sum(stat.size for stat in snapshot.statistics("filename"))


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
        bridge_factory=_shared_bridge_factory,
    )
    workspace_scope = WorkspaceScope(tmp_path)

    retained_deltas: list[int] = []
    elapsed_seconds: list[float] = []

    gc.collect()
    tracemalloc.start()
    baseline_bytes = _traced_bytes_from_ralph()
    peak_retained = 0

    total_cycles = _WARMUP_COUNT + _ITERATION_COUNT
    for _ in range(total_cycles):
        started = time.perf_counter()
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
        retained = _traced_bytes_from_ralph() - baseline_bytes
        retained_deltas.append(retained)
        peak_retained = max(peak_retained, retained)
        elapsed_seconds.append(time.perf_counter() - started)

    tracemalloc.stop()

    post_warmup = retained_deltas[_WARMUP_COUNT:]
    post_warmup_elapsed = elapsed_seconds[_WARMUP_COUNT:]
    assert post_warmup
    assert post_warmup_elapsed

    early_retained = post_warmup[:_WINDOW]
    late_retained = post_warmup[-_WINDOW:]
    early_elapsed = post_warmup_elapsed[:_WINDOW]
    late_elapsed = post_warmup_elapsed[-_WINDOW:]

    retained_spread = max(post_warmup) - min(post_warmup)
    final_retained = post_warmup[-1]

    # Characterization report (not a brittle CI threshold).
    print(
        "pipeline_memory_characterization "
        f"cycles={_ITERATION_COUNT} warmup={_WARMUP_COUNT} "
        f"early_retained_mean={statistics.mean(early_retained):.0f} "
        f"late_retained_mean={statistics.mean(late_retained):.0f} "
        f"retained_spread={retained_spread} "
        f"early_elapsed_mean_ms={statistics.mean(early_elapsed) * 1000:.3f} "
        f"late_elapsed_mean_ms={statistics.mean(late_elapsed) * 1000:.3f} "
        f"early_elapsed_max_ms={max(early_elapsed) * 1000:.3f} "
        f"late_elapsed_max_ms={max(late_elapsed) * 1000:.3f}"
    )

    assert final_retained <= _RETAINED_DELTA_LIMIT, (
        f"pipeline retained delta {final_retained} bytes exceeds "
        f"{_RETAINED_DELTA_LIMIT}-byte budget after {_ITERATION_COUNT} cycles"
    )
    assert retained_spread <= _RETAINED_SPREAD_LIMIT, (
        f"post-warmup retained spread {retained_spread} bytes exceeds "
        f"{_RETAINED_SPREAD_LIMIT}-byte plateau budget "
        f"(early_mean={statistics.mean(early_retained):.0f}, "
        f"late_mean={statistics.mean(late_retained):.0f})"
    )
    assert peak_retained <= _PEAK_DELTA_LIMIT
