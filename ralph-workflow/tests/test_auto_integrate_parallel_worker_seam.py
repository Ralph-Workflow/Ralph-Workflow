"""A manifest-launched parallel worker must carry its own integration seams.

``ralph.pipeline.parallel.worker_runtime.run_parallel_worker_from_manifest``
runs a worker WITHOUT entering the shared run loop, so it inherits none
of that loop's auto-integration hooks. Nothing under
``ralph/pipeline/parallel/`` referenced integration or rebase at all,
and the coordinator-side join that would have covered it
(``runner._integrate_after_fan_out``, reached via ``FanOutEffect``) is
dormant under the bundled ``dispatch_mode = "agent_subagents"``.

The result was that in the exact topology auto-integration exists for --
several agents advancing one shared mainline at the same time -- a
manifest-launched worker neither published its landings to its siblings
nor picked theirs up, which on its own reads as "auto rebase does not
work".

These are fast unit tests: no real git, no subprocess, no sleeping. The
real-git proof of the conflicted path lives in
``tests/test_auto_integrate_fleet_conflict_e2e.py``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.agents.registry import AgentRegistry
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.policy.models import PolicyBundle
    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions


class _IntegrationCall:
    """One recorded invocation of the boundary integration entry point."""

    def __init__(
        self,
        *,
        workspace_scope: object,
        conflict_resolver: object,
        rebase_stop_resolver: object,
    ) -> None:
        self.workspace_scope = workspace_scope
        self.conflict_resolver = conflict_resolver
        self.rebase_stop_resolver = rebase_stop_resolver


def _worker_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.parallel.worker_runtime")


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"}}
    )


def _display_context() -> DisplayContext:
    return make_display_context(env={}, force_width=80)


def _git_workspace(tmp_path: Path) -> Path:
    """Mark ``tmp_path`` as a git checkout for the seam's cheap stat guard.

    The seam short-circuits on a workspace that is not a checkout, so
    every test that expects it to DO something has to look like one.
    Nothing here reads git itself -- the integration entry points are
    replaced by recorders -- so a bare directory is enough.
    """
    (tmp_path / ".git").mkdir(exist_ok=True)
    return tmp_path


def _write_manifest(tmp_path: Path) -> tuple[Path, Path]:
    """Write a minimal worker manifest; return (manifest_path, worker_ns)."""
    _git_workspace(tmp_path)
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        config_path=None,
        cli_overrides={},
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path, worker_ns


class _FakePolicyBundle:
    """Structural stand-in for the slots the worker flow reads."""

    pipeline = object()
    artifacts = object()
    agents = object()


class _FakeRegistry:
    """Registry that has no agents installed."""

    def get(self, name: str) -> None:
        del name


def _fake_registry_class() -> object:
    return type(
        "_FakeRegistryClass",
        (),
        {"from_config": classmethod(lambda cls, config: _FakeRegistry())},
    )


class _FakeWorkspace:
    """Workspace whose ``read`` returns a fixed prompt body."""

    def __init__(
        self, root: Path, *, allowed_roots: tuple[Path, ...] | None = None
    ) -> None:
        del root, allowed_roots

    def read(self, path: str) -> str:
        del path
        return "worker prompt body"


def _install_worker_stubs(
    module: ModuleType,
    monkeypatch: MonkeyPatch,
    *,
    agent_event: PipelineEvent = PipelineEvent.AGENT_SUCCESS,
) -> None:
    """Stub every collaborator the worker flow needs except the seams."""
    monkeypatch.setattr(
        module,
        "load_config",
        lambda _path, _overrides, *, workspace_scope: _config(),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "load_policy_for_workspace_scope",
        lambda *args, **kwargs: _FakePolicyBundle(),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "create_initial_state",
        lambda *args, **kwargs: PipelineState(phase="development"),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "determine_effect_from_policy",
        lambda *args, **kwargs: InvokeAgentEffect(
            agent_name="developer",
            phase="development",
            prompt_file="ignored.md",
            drain="development",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        module, "AgentRegistry", _fake_registry_class(), raising=False
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        module,
        "execute_agent_effect",
        lambda *args, **kwargs: agent_event,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        lambda **kwargs: agent_event,
        raising=False,
    )


def _record_seams(
    module: ModuleType, monkeypatch: MonkeyPatch
) -> tuple[list[str], list[_IntegrationCall]]:
    """Replace the two seam entry points with recorders.

    Returns ``(ordered_events, integration_calls)``. ``ordered_events``
    interleaves ``"recover"`` and ``"integrate"`` markers so a test can
    assert that recovery happens BEFORE any integration, and that both
    happen before / after the agent as the seam contract requires.
    """
    events: list[str] = []
    calls: list[_IntegrationCall] = []

    def _fake_recover(
        workspace_scope: object, *, config: object = None
    ) -> RebaseState | None:
        del workspace_scope, config
        events.append("recover")
        return None

    def _fake_integrate(
        config: object,
        workspace_scope: object,
        state: object,
        *,
        conflict_resolver: object = None,
        rebase_stop_resolver: object = None,
        display: object = None,
    ) -> RebaseState | None:
        del config, state, display
        events.append("integrate")
        calls.append(
            _IntegrationCall(
                workspace_scope=workspace_scope,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
            )
        )
        return None

    monkeypatch.setattr(
        module, "recover_incomplete_integration", _fake_recover, raising=False
    )
    monkeypatch.setattr(
        module, "auto_integrate_on_phase_transition", _fake_integrate, raising=False
    )
    return events, calls


def _worker_pipeline_deps(display_context: DisplayContext) -> PipelineDeps:
    """Real ``PipelineDeps`` whose prompt materializer returns a fixed path."""

    def _materialize(
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str:
        del context, options, kwargs
        return ".agent/tmp/development_prompt.md"

    return make_test_pipeline_deps(
        display_context, phase_prompt_materializer=_materialize
    )


def _run_worker(
    module: ModuleType, tmp_path: Path, monkeypatch: MonkeyPatch
) -> tuple[int, list[str], list[_IntegrationCall]]:
    manifest_path, _worker_ns = _write_manifest(tmp_path)
    _install_worker_stubs(module, monkeypatch)
    events, calls = _record_seams(module, monkeypatch)
    display_context = _display_context()
    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=display_context,
        pipeline_deps=_worker_pipeline_deps(display_context),
    )
    return exit_code, events, calls


def test_worker_recovers_before_it_integrates_and_before_it_works(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Crash recovery runs exactly once, ahead of every integration."""
    module = _worker_module()

    exit_code, events, _calls = _run_worker(module, tmp_path, monkeypatch)

    assert exit_code == 0
    assert events.count("recover") == 1, (
        f"recovery must run exactly once per worker, got {events!r}"
    )
    assert events[0] == "recover", (
        f"recovery must precede any integration, got {events!r}"
    )


def test_worker_runs_a_startup_and_a_boundary_integration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Two seams: catch up before the phase, publish after it succeeds."""
    module = _worker_module()

    exit_code, events, calls = _run_worker(module, tmp_path, monkeypatch)

    assert exit_code == 0
    assert events == ["recover", "integrate", "integrate"], (
        "a worker must integrate once at startup and once after a successful"
        f" phase, got {events!r}"
    )
    for call in calls:
        assert isinstance(call.workspace_scope, WorkspaceScope)
        assert Path(call.workspace_scope.root) == tmp_path, (
            "each worker must integrate through its OWN scope root, which is"
            " what keys the boundary refresh throttle"
        )


def test_worker_integration_is_offered_a_rebase_stop_resolver(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A conflicted rebase must be resolvable in place, not only aborted.

    Without a ``rebase_stop_resolver`` the integration can still rebase
    and fast-forward, but a conflicted replay is abandoned and retried
    as an endpoint merge -- so this assertion is what separates "the
    worker syncs" from "the worker syncs and can resolve".
    """
    module = _worker_module()

    _exit_code, _events, calls = _run_worker(module, tmp_path, monkeypatch)

    assert calls, "expected at least one integration"
    for call in calls:
        assert call.rebase_stop_resolver is not None
        assert call.conflict_resolver is not None


def test_a_failed_phase_does_not_run_the_boundary_integration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Only a SUCCESSFUL phase publishes; a failed one has nothing to land."""
    module = _worker_module()
    manifest_path, _worker_ns = _write_manifest(tmp_path)
    _install_worker_stubs(
        module, monkeypatch, agent_event=PipelineEvent.AGENT_FAILURE
    )
    events, _calls = _record_seams(module, monkeypatch)
    display_context = _display_context()

    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=display_context,
        pipeline_deps=_worker_pipeline_deps(display_context),
    )

    assert exit_code == 1
    assert events == ["recover", "integrate"], (
        f"a failed phase must not run the boundary seam, got {events!r}"
    )


def test_missing_dependencies_degrade_to_integration_without_resolvers(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """No policy / registry / deps means no resolver -- never a dead seam.

    The cross-agent catch-up is the bulk of the value and needs none of
    those collaborators, so it must still run; only in-place conflict
    resolution, which genuinely requires a Ralph MCP session, is
    withheld.
    """
    module = _worker_module()
    _events, calls = _record_seams(module, monkeypatch)

    outcome = module.run_worker_auto_integration(
        config=_config(),
        workspace_scope=WorkspaceScope(_git_workspace(tmp_path)),
        policy_bundle=None,
        registry=cast("AgentRegistry | None", None),
        pipeline_deps=None,
        display_context=None,
        recover_first=True,
    )

    assert outcome is None
    assert len(calls) == 1, "the catch-up integration must still be attempted"
    assert calls[0].conflict_resolver is None
    assert calls[0].rebase_stop_resolver is None


def test_a_workspace_that_is_not_a_checkout_costs_nothing(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """No git checkout, no seam: not even a crash-record read.

    The guard is a cheap ``stat``. It matters because a worker whose
    workspace is not a repository would otherwise pay for a record read,
    a ``ParallelDisplay`` and two resolver closures on every seam only to
    reach the same ``None``.
    """
    module = _worker_module()
    _events, calls = _record_seams(module, monkeypatch)

    assert (
        module.run_worker_auto_integration(
            config=_config(),
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=None,
            registry=cast("AgentRegistry | None", None),
            pipeline_deps=None,
            display_context=None,
            recover_first=True,
        )
        is None
    )
    assert calls == [], "a non-checkout workspace must not reach the integration"


def test_a_raising_seam_never_aborts_the_worker(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Auto-integration must degrade, never propagate into the worker.

    A worker's job is the phase it was launched for. An integration that
    blew up used to have no containment of its own in this topology,
    because the topology had no integration at all; the seam therefore
    carries the same broad guard the shared run loop uses.
    """
    module = _worker_module()

    def _explode(
        workspace_scope: object, *, config: object = None
    ) -> RebaseState | None:
        del workspace_scope, config
        raise RuntimeError("simulated recovery failure")

    monkeypatch.setattr(
        module, "recover_incomplete_integration", _explode, raising=False
    )

    assert (
        module.run_worker_auto_integration(
            config=_config(),
            workspace_scope=WorkspaceScope(_git_workspace(tmp_path)),
            policy_bundle=cast("PolicyBundle | None", None),
            registry=cast("AgentRegistry | None", None),
            pipeline_deps=None,
            display_context=None,
            recover_first=True,
        )
        is None
    )
