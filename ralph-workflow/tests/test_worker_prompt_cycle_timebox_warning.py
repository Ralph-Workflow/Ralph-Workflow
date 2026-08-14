"""Cycle-timebox warning on a parallel worker's prompt.

A fan-out worker runs in its own process from a manifest, so its pipeline
state carries no cycle timing at all — the deadline reaches it only through
the environment its parent published. Without this the worker could spend the
tail of the cycle budget with no idea a deadline was approaching, while the
serial development agent doing the same work was warned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_DURATION_SECONDS_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)
from ralph.pipeline.prompt_prep import cycle_timebox_warning_from_env

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch

_NOW = 1_000_000.0
_TARGET = "development_final_commit_cleanup"


def _published(*, warn_in: float, deadline_in: float) -> dict[str, str]:
    return {
        CYCLE_WARN_EPOCH_ENV: repr(_NOW + warn_in),
        CYCLE_DEADLINE_EPOCH_ENV: repr(_NOW + deadline_in),
        CYCLE_DURATION_SECONDS_ENV: repr(7200.0),
        CYCLE_FINALIZATION_TARGET_ENV: _TARGET,
    }


def test_worker_is_warned_once_the_published_warning_point_has_passed() -> None:
    warning = cycle_timebox_warning_from_env(
        _published(warn_in=-60.0, deadline_in=1380.0), now_epoch=_NOW
    )

    assert warning is not None
    assert warning["remaining_seconds"] == 1380.0
    assert warning["elapsed_seconds"] == 7200.0 - 1380.0
    assert warning["finalization_target"] == _TARGET


def test_worker_is_not_warned_before_the_warning_point() -> None:
    assert (
        cycle_timebox_warning_from_env(
            _published(warn_in=60.0, deadline_in=1500.0), now_epoch=_NOW
        )
        is None
    )


def test_worker_is_not_warned_when_no_deadline_was_published() -> None:
    assert cycle_timebox_warning_from_env({}, now_epoch=_NOW) is None


def test_worker_warning_ignores_unusable_published_values() -> None:
    unusable = {
        CYCLE_WARN_EPOCH_ENV: "nan",
        CYCLE_DEADLINE_EPOCH_ENV: "later",
        CYCLE_DURATION_SECONDS_ENV: "",
        CYCLE_FINALIZATION_TARGET_ENV: _TARGET,
    }

    assert cycle_timebox_warning_from_env(unusable, now_epoch=_NOW) is None


def test_worker_runtime_forwards_the_warning_to_its_materializer(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """The worker's call site must hand its materializer the live warning.

    Asserted on the kwarg the materializer actually receives, having driven the
    real worker entry point: reading the call site's source text instead let
    the environment read be replaced by an empty mapping — silencing every
    fan-out worker — while the source, and the assertion, stayed identical.
    """
    captured = _drive_worker(monkeypatch, tmp_path)

    warning = captured.materializer_kwargs["cycle_timebox_warning"]
    assert warning is not None
    assert warning["finalization_target"] == _TARGET
    assert warning["duration_seconds"] == 7200.0


def test_worker_hides_the_cycle_deadline_from_its_catch_up_integration(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Reconciliation spawns a conflict resolver that never joined the cycle.

    It must not be nagged to wrap up work it has no part in — and the worker
    must still hold its own deadline afterwards, which withdrawing outright
    would have cost it.
    """
    captured = _drive_worker(monkeypatch, tmp_path)

    # A worker reconciles twice — catching up at startup and publishing what it
    # landed — and neither resolver may see the cycle deadline.
    assert captured.deadline_visible_during_integration
    assert not any(captured.deadline_visible_during_integration)
    assert captured.materializer_kwargs["cycle_timebox_warning"] is not None


@dataclass
class _WorkerCapture:
    materializer_kwargs: dict[str, object] = field(default_factory=dict)
    deadline_visible_during_integration: list[bool] = field(default_factory=list)


def _drive_worker(monkeypatch: MonkeyPatch, tmp_path: Path) -> _WorkerCapture:
    """Run the real manifest worker with its collaborators observed."""
    from ralph.display.context import make_display_context
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.parallel import worker_runtime
    from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest

    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("Implement the modules.", encoding="utf-8")
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"
    worker_namespace.mkdir(parents=True, exist_ok=True)
    for name, value in _published(warn_in=-60.0, deadline_in=1380.0).items():
        monkeypatch.setenv(name, value)

    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Module A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        config_path=None,
        cli_overrides={},
        worker_namespace=str(worker_namespace),
        worker_artifact_dir=str(worker_namespace / "artifacts"),
        prompt_file=str(worker_namespace / "prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = worker_namespace / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    captured = _WorkerCapture()

    def _materialize(**kwargs: object) -> str:
        captured.materializer_kwargs.update(kwargs)
        (tmp_path / "rendered.md").write_text("worker prompt", encoding="utf-8")
        return "rendered.md"

    def _integrate(**_kwargs: object) -> None:
        captured.deadline_visible_during_integration.append(
            CYCLE_DEADLINE_EPOCH_ENV in os.environ
        )

    monkeypatch.setattr(worker_runtime, "run_worker_auto_integration", _integrate)
    monkeypatch.setattr(
        worker_runtime,
        "execute_agent_effect",
        lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        worker_runtime,
        "phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    deps = SimpleNamespace(phase_prompt_materializer=_materialize, agy_agents_probe=None)
    worker_runtime.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=make_display_context(),
        pipeline_deps=deps,
    )
    return captured
