from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from ralph.agents.worker_result import WorkerResult
from ralph.cli.commands import run as run_module
from ralph.display.context import make_display_context
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.pipeline import fan_out as fan_out_module
from ralph.pipeline import prompt_prep as prompt_prep_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.checkpoint import CHECKPOINT_PATH, worker_checkpoint_path
from ralph.pipeline.effects import FanOutEffect, PreparePromptEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseParallelization
from ralph.prompts import materialize as materialize_module
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace import FsWorkspace


def _legacy_display() -> runner_module.LegacyConsoleDisplay:
    return runner_module.LegacyConsoleDisplay(make_display_context())


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=False)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    bundle.agents.agent_drains = {
        "development": MagicMock(
            chain="developer", drain_class="development", capability_class=None
        ),
    }
    bundle.agents.agent_chains = {"developer": MagicMock(agents=["developer"])}
    return bundle


def test_parallel_workers_would_collide_on_shared_prompt_and_checkpoint_files_without_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    units = (_make_work_unit("unit-a"), _make_work_unit("unit-b"))
    effect = FanOutEffect(work_units=units, max_workers=2)
    state = PipelineState(
        phase="development",
        work_units=units,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    workspace_scope = WorkspaceScope(tmp_path)
    policy_bundle = _make_policy_bundle(max_workers=2)
    prompt_writes: list[str] = []
    checkpoint_writes: list[Path] = []
    loaded_policy = load_policy(tmp_path / ".agent")
    (tmp_path / "PROMPT.md").write_text("Base development prompt", encoding="utf-8")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "PLAN.md").write_text(
        "# Execution Plan\n\n1. Implement both workers\n",
        encoding="utf-8",
    )

    real_dump_rendered_prompt = materialize_module.dump_rendered_prompt

    class _FakeExecutor:
        def __init__(
            self,
            command: object,
            signal_bridge: object | None = None,
            **kwargs: object,
        ) -> None:
            del command, signal_bridge
            self._cwd = Path(cast("str | Path", kwargs.get("cwd", tmp_path)))
            extra_env = kwargs.get("extra_env", {})
            self._extra_env = cast("dict[str, str]", extra_env)

        async def run(
            self,
            unit: WorkUnit,
            *,
            on_output: Callable[[str], None],
            on_status: Callable[[WorkerStatus], None],
        ) -> WorkerResult:
            del on_output
            on_status(WorkerStatus.RUNNING)
            prompt_prep_module.materialize_prepared_prompt(
                PreparePromptEffect(phase="development"),
                loaded_policy.pipeline,
                loaded_policy.artifacts,
                WorkspaceScope(self._cwd),
                agents_policy=loaded_policy.agents,
                state=PipelineState(phase="development", work_units=(unit,)),
                env=self._extra_env,
            )
            worker_ns = Path(self._extra_env[str(WORKER_NAMESPACE_ENV)])
            artifact_dir = worker_ns / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "development_result.json").write_text("{}", encoding="utf-8")
            fan_out_module.ckpt.save(
                PipelineState(phase="development", work_units=(unit,)),
                path=worker_checkpoint_path(worker_ns),
            )
            return WorkerResult(
                unit_id=unit.unit_id,
                exit_code=0,
                final_message="ok",
                duration_ms=1,
            )

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

        def build(self, session: object) -> object:
            del session

            class _Handle:
                endpoint = "http://example.invalid/mcp"

                def shutdown(self) -> None:
                    return None

            return _Handle()

    def _record_checkpoint_save(_state: PipelineState, path: Path = CHECKPOINT_PATH) -> None:
        checkpoint_writes.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("checkpoint", encoding="utf-8")

    def _record_prompt_dump(
        workspace: FsWorkspace,
        phase: str,
        prompt: str,
        *,
        worker_namespace: Path | None = None,
    ) -> str:
        path = real_dump_rendered_prompt(
            workspace,
            phase,
            prompt,
            worker_namespace=worker_namespace,
        )
        prompt_writes.append(path)
        return path

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr(fan_out_module.ckpt, "save", _record_checkpoint_save)
    monkeypatch.setattr(materialize_module, "dump_rendered_prompt", _record_prompt_dump)
    monkeypatch.setattr(
        fan_out_module,
        "write_parallel_development_summary",
        lambda *args, **kwargs: None,
    )

    fan_out_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert sorted(prompt_writes) == sorted(
        [
            str(tmp_path / ".agent" / "workers" / "unit-a" / "tmp" / "development_prompt.md"),
            str(tmp_path / ".agent" / "workers" / "unit-b" / "tmp" / "development_prompt.md"),
        ]
    )
    worker_checkpoint_a = tmp_path / ".agent" / "workers" / "unit-a" / "tmp" / "checkpoint.json"
    worker_checkpoint_b = tmp_path / ".agent" / "workers" / "unit-b" / "tmp" / "checkpoint.json"
    shared_checkpoint = tmp_path / ".agent" / "checkpoint.json"
    prompt_a = tmp_path / ".agent" / "workers" / "unit-a" / "tmp" / "development_prompt.md"
    prompt_b = tmp_path / ".agent" / "workers" / "unit-b" / "tmp" / "development_prompt.md"
    assert checkpoint_writes.count(worker_checkpoint_a) == 1
    assert checkpoint_writes.count(worker_checkpoint_b) == 1
    assert checkpoint_writes.count(CHECKPOINT_PATH) <= 1
    assert not shared_checkpoint.exists()
    assert worker_checkpoint_a.exists()
    assert worker_checkpoint_b.exists()
    assert prompt_a.read_text(encoding="utf-8") != prompt_b.read_text(encoding="utf-8")
    assert "Work unit unit-a" in prompt_a.read_text(encoding="utf-8")
    assert '"src/unit-a"' in prompt_a.read_text(encoding="utf-8")
    assert "Work unit unit-b" in prompt_b.read_text(encoding="utf-8")
    assert '"src/unit-b"' in prompt_b.read_text(encoding="utf-8")


def test_parallel_worker_mode_does_not_call_shared_pipeline_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: list[str] = []
    outer_execute_calls: list[str] = []
    shared_checkpoint = tmp_path / ".agent" / "checkpoint.json"
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(
        run_module,
        "_load_configuration",
        lambda *args, **kwargs: called.append("load_configuration") or object(),
    )
    monkeypatch.setattr(
        run_module,
        "_run_preflight_checks",
        lambda *args, **kwargs: called.append("preflight") or 0,
    )
    monkeypatch.setattr(run_module, "run_parallel_worker_from_manifest", lambda **kwargs: 0)
    monkeypatch.setattr(
        run_module,
        "_execute_pipeline",
        lambda *args, **kwargs: outer_execute_calls.append("execute_pipeline") or 0,
    )

    request = run_module.RunPipelineRequest(
        parallel_worker_manifest=tmp_path / "worker-manifest.json"
    )
    result = run_module.run_pipeline(request=request)

    assert result == 0
    assert called == []
    assert outer_execute_calls == []
    assert not shared_checkpoint.exists()


def test_parallel_worker_manifest_persists_parent_config_and_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_module = importlib.import_module("ralph.mcp.protocol.env")
    manifest_env = getattr(env_module, "RALPH_PARALLEL_WORKER_MANIFEST_ENV", None)
    if manifest_env is None:
        pytest.fail("Expected RALPH_PARALLEL_WORKER_MANIFEST_ENV to be defined", pytrace=False)

    effect = FanOutEffect(
        work_units=(_make_work_unit("unit-a"),),
        max_workers=1,
        phase="development",
    )
    manifest_paths = fan_out_module._persist_parallel_worker_manifests(
        effect=effect,
        repo_root=tmp_path,
        session_drain="development",
        config_path=tmp_path / "configs" / "parent.toml",
        cli_overrides={"agent": "opencode", "verbose": True},
    )
    manifest_path = manifest_paths["unit-a"]
    manifest = fan_out_module.ParallelWorkerManifest.load(manifest_path)

    assert manifest.config_path == str(tmp_path / "configs" / "parent.toml")
    assert manifest.cli_overrides == {"agent": "opencode", "verbose": True}

    same_workspace = fan_out_module.SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=MagicMock(),
        executor_command=("python", "-m", "ralph"),
        worker_commands={"unit-a": ("python", "-m", "ralph")},
        signal_bridge=MagicMock(),
        worker_namespace_root=tmp_path / ".agent" / "workers",
        worker_manifest_paths=manifest_paths,
        session_drain="development",
        session_capabilities=frozenset(),
        session_model_identity=None,
        session_capability_profile=None,
    )

    monkeypatch.setattr(
        "ralph.pipeline.parallel.worker_session.build_worker_session",
        lambda *args, **kwargs: MagicMock(
            mcp_handle=MagicMock(endpoint="http://example.invalid/mcp"),
            session=MagicMock(session_id="session-123"),
        ),
    )

    class _CaptureExecutor:
        extra_env: dict[str, str]

        def __init__(
            self,
            command: object,
            signal_bridge: object | None = None,
            **kwargs: object,
        ) -> None:
            del command, signal_bridge
            self.extra_env = cast("dict[str, str]", kwargs["extra_env"])

    monkeypatch.setattr(
        "ralph.pipeline.parallel.parallel_coordinator.subprocess_executor.SubprocessAgentExecutor",
        _CaptureExecutor,
    )

    prepare_executor = getattr(fan_out_module.coordinator, "_prepare_executor", None)
    if prepare_executor is None:
        prepare_executor = fan_out_module.coordinator.prepare_executor

    executor, _bundle, _worker_namespace = prepare_executor(
        _make_work_unit("unit-a"),
        MagicMock(),
        same_workspace,
    )

    captured_executor = cast("_CaptureExecutor", executor)
    assert isinstance(captured_executor, _CaptureExecutor)
    assert captured_executor.extra_env[manifest_env] == str(manifest_path)
