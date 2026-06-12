"""Black-box unit tests for Pro integration in the pipeline run loop.

These tests stub out the runner, marker reader, and heartbeat client so
the run loop can be exercised end-to-end without a real subprocess,
network call, or wall-clock ``time.sleep``. The Pro contract is
exercised at the integration level: heartbeat client is started in Pro
mode, stopped during cleanup, and exit codes are preserved.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

    from ralph.config.models import UnifiedConfig


def _load_run_loop() -> ModuleType:
    """Load the run_loop module via importlib after runner has loaded.

    Avoids the pre-existing circular import between
    ``ralph.pipeline.runner`` and ``ralph.pipeline.run_loop`` by
    ensuring runner is fully initialised before run_loop is loaded.
    """
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> ModuleType:
    return importlib.import_module("ralph.pipeline.runner")


def _make_fake_bundle() -> PolicyBundle:
    """Build the smallest bundle that satisfies ``run``'s contract."""
    pipeline = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    agents = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=1),
            "development": AgentChainConfig(agents=["claude"], max_retries=1),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
        },
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
                json_path=".agent/artifacts/plan.json",
            )
        }
    )
    return PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)


def _seed_workspace(workspace_root: Path) -> None:
    """Write the minimum files the run loop expects on the workspace."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "PROMPT.md").write_text("# test\n", encoding="utf-8")


def _seed_marker(workspace_root: Path, *, run_id: str, token: str, port: int) -> None:
    marker_dir = workspace_root / ".ralph"
    marker_dir.mkdir(exist_ok=True)
    payload = {"runId": run_id, "port": port, "heartbeatToken": token}
    (marker_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")


def _build_config(tmp_path: Path) -> UnifiedConfig:
    config = MagicMock()
    config.general = MagicMock()
    config.general.verbosity = Verbosity.NORMAL
    config.general.developer_iters = 1
    config.general.workflow = MagicMock()
    config.general.workflow.checkpoint_enabled = True
    config.general.max_same_agent_retries = 1
    config.general.checkpoint = MagicMock()
    config.general.parallel_max_workers = None
    return cast("UnifiedConfig", config)


def _install_display_context(
    monkeypatch: pytest.MonkeyPatch, run_loop_module: ModuleType
) -> None:
    """Force ``make_display_context`` to return a deterministic context."""
    ctx = make_display_context()
    runner_module = _load_runner()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    display = ParallelDisplay(workspace_root=Path("/tmp"), display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
    """Patch heavy entry points so the run loop walks to the inner loop and exits cleanly."""
    runner_module = _load_runner()
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(
        runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle
    )
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state)


def _build_recovery_controller_mock() -> MagicMock:
    return MagicMock(
        spec=RecoveryController,
        event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
    )


def test_heartbeat_started_in_pro_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-x", token="tok", port=7432)

    recording = _RecordingHeartbeat()

    def _fake_start(_ws: object) -> _RecordingHeartbeat:
        recording.start()
        return recording

    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", _fake_start)

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    _install_display_context(monkeypatch, run_loop_module)
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0
    assert recording.started
    assert recording.stopped


def test_no_heartbeat_when_pro_mode_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    started_clients: list[object] = []

    def _fake_start(_ws: object) -> _RecordingHeartbeat:
        rec = _RecordingHeartbeat()
        started_clients.append(rec)
        return rec

    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", _fake_start)

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0
    assert started_clients == [] or all(
        not getattr(c, "started", False) for c in started_clients
    )


def test_pro_mode_exit_code_zero_on_clean_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-clean", token="tok", port=7432)

    monkeypatch.setattr(
        run_loop_module,
        "_start_pro_heartbeat_if_active",
        lambda _ws: _RecordingHeartbeat(),
    )

    config = _build_config(tmp_path)
    state = PipelineState(phase="complete")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 0


def test_pro_mode_exit_code_preserved_on_pipeline_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pipeline that returns a non-zero step code must surface that code under Pro mode."""
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_workspace(tmp_path)
    _seed_marker(tmp_path, run_id="run-fail", token="tok", port=7432)

    recording = _RecordingHeartbeat()
    monkeypatch.setattr(
        run_loop_module, "_start_pro_heartbeat_if_active", lambda _ws: recording
    )

    config = _build_config(tmp_path)
    state = PipelineState(phase="planning")
    bundle = _make_fake_bundle()
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)

    def _fake_run_inner_loop(
        _state: PipelineState, _ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int]:
        return state, "planning", 7

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _fake_run_inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (_build_recovery_controller_mock(), 1),
    )
    _install_display_context(monkeypatch, run_loop_module)

    exit_code = run_loop_module.run(config, initial_state=state)
    assert exit_code == 7
    assert recording.stopped


def test_start_pro_heartbeat_returns_none_when_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker = no heartbeat, even when RALPH_WORKFLOW_PRO is set."""
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


def test_start_pro_heartbeat_returns_none_when_run_id_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text(
        json.dumps({"heartbeatToken": "x"}), encoding="utf-8"
    )
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


def test_start_pro_heartbeat_returns_none_when_token_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text(
        json.dumps({"runId": "x"}), encoding="utf-8"
    )
    assert run_loop_module._start_pro_heartbeat_if_active(tmp_path) is None


class _RecordingHeartbeat:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_running(self) -> bool:
        return self.started and not self.stopped
