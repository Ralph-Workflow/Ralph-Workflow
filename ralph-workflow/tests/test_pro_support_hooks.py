"""Black-box unit tests for :mod:`ralph.pro_support.hooks` and ``run`` factory wiring."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

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
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import (
    PipelineStateSnapshot,
    SnapshotRegistry,
    build_pipeline_state_snapshot,
)
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig

# All tests in this module go through the full ``ralph.pipeline.run_loop.run()``
# integration path (lazy import via ``importlib.import_module`` triggers the
# heavy ``ralph.pipeline.*`` dependency graph; the run() call wires up
# display/recovery/state machinery even when ``_run_inner_loop`` is mocked
# to short-circuit). Wall-clock cost under parallel xdist load is regularly
# > 1 s on busy machines, so the default 1-second per-test ceiling is unsafe.
# The 5-second cap matches the precedent in
# ``tests/test_git_rebase_preconditions.py`` for integration tests that
# transitively load the pipeline dependency graph.
pytestmark = pytest.mark.timeout_seconds(5)


def _load_run_loop() -> object:
    return importlib.import_module("ralph.pipeline.run_loop")


def _load_runner() -> object:
    return importlib.import_module("ralph.pipeline.runner")


def _fake_bundle() -> PolicyBundle:
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
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "PROMPT.md").write_text("# test\n", encoding="utf-8")


def _build_config() -> UnifiedConfig:
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


def _patch_runner_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
    runner_module = _load_runner()
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle)
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state)


def _install_display_context(monkeypatch: pytest.MonkeyPatch, run_loop_module: object) -> None:
    ctx = make_display_context()
    runner_module = _load_runner()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    display = ParallelDisplay(workspace_root=Path("/tmp"), display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )


def test_policy_bundle_override_routes_through_inner_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    override_bundle = _fake_bundle()
    factory_calls: list[object] = []

    def _policy_bundle_factory(_scope: object, _cfg: object) -> PolicyBundle:
        factory_calls.append(object())
        return _fake_bundle()

    captured_ctx: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_ctx.append(ctx)
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, _fake_bundle())
    _install_display_context(monkeypatch, run_loop_module)

    hooks = ProPipelineHooks(
        policy_bundle_factory=_policy_bundle_factory,
        policy_bundle_override=override_bundle,
    )

    config = _build_config()
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config,
        initial_state=state,
        pro_hooks=hooks,
    )
    assert exit_code == 0
    assert factory_calls == [], "policy_bundle_factory must NOT be called when override is set"
    assert captured_ctx, "inner loop did not run"
    assert getattr(captured_ctx[0], "policy_bundle", None) is override_bundle


def test_registry_factory_is_invoked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    custom_registry = MagicMock()
    factory_calls: list[object] = []

    def _registry_factory(cfg: object) -> object:
        factory_calls.append(cfg)
        return custom_registry

    captured_registry: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_registry.append(getattr(ctx, "registry", None))
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(registry_factory=_registry_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert len(factory_calls) == 1
    assert factory_calls[0] is config
    assert captured_registry == [custom_registry]


def test_state_factory_is_invoked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    bundle = _fake_bundle()
    custom_state = PipelineState(phase="complete")
    state_factory_calls: list[object] = []

    def _state_factory(_cfg: object, _ap: object, _pp: object, _co: object | None) -> PipelineState:
        state_factory_calls.append(object())
        return custom_state

    captured_state: list[PipelineState] = []

    def _inner_loop(
        state: PipelineState, _ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_state.append(state)
        return state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, PipelineState(phase="complete"), bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(state_factory=_state_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(config, pro_hooks=hooks)
    assert exit_code == 0
    assert len(state_factory_calls) == 1
    assert captured_state[0] is custom_state


def test_recovery_controller_factory_is_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    custom_controller = MagicMock(spec=RecoveryController)
    custom_controller.event_bus = MagicMock(subscribe=lambda _cb: lambda: None)
    controller_factory_calls: list[object] = []

    def _controller_factory(_st: object, _pb: object, _cfg: object) -> tuple[object, int]:
        controller_factory_calls.append(object())
        return custom_controller, 1

    captured_controller: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_controller.append(getattr(ctx, "controller", None))
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda *_a, **_kw: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(recovery_controller_factory=_controller_factory)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert len(controller_factory_calls) == 1
    assert captured_controller[0] is custom_controller


def test_hooks_default_to_existing_runner_helpers_when_fields_are_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state, "complete", None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    exit_code = cast("Callable[..., int]", run_loop_module.run)(config, initial_state=state)
    assert exit_code == 0


def test_snapshot_registry_receives_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    bundle = _fake_bundle()
    registry = SnapshotRegistry()

    def _inner_loop(
        state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        snap = build_pipeline_state_snapshot(state, tmp_path)
        reg = getattr(ctx, "snapshot_registry", None)
        if reg is not None:
            reg.publish(snap)
        return state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, bundle)
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(snapshot_registry=registry)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    latest = registry.get_latest()
    assert latest is not None
    assert isinstance(latest, PipelineStateSnapshot)
    assert latest.phase == "complete"


def test_custom_pipeline_artifact_type_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = _load_run_loop()
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    _seed_workspace(tmp_path)

    state = PipelineState(phase="complete")
    custom_bundle = _fake_bundle()

    captured_bundle: list[object] = []

    def _inner_loop(
        _state: PipelineState, ctx: object, _prev: object
    ) -> tuple[PipelineState, str, int | None]:
        captured_bundle.append(getattr(ctx, "policy_bundle", None))
        return _state, "complete", None

    monkeypatch.setattr(run_loop_module, "_run_inner_loop", _inner_loop)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    _patch_runner_dependencies(monkeypatch, tmp_path, state, _fake_bundle())
    _install_display_context(monkeypatch, run_loop_module)

    config = _build_config()
    hooks = ProPipelineHooks(policy_bundle_override=custom_bundle)
    exit_code = cast("Callable[..., int]", run_loop_module.run)(
        config, initial_state=state, pro_hooks=hooks
    )
    assert exit_code == 0
    assert captured_bundle[0] is custom_bundle


def test_pro_pipeline_hooks_to_runner_kwargs_shape() -> None:
    """Pin the shape of :meth:`ProPipelineHooks.to_runner_kwargs` and the dataclass guards.

    Contract pinned by this test (from the engine-side handoff
    in ``ralph-workflow/docs/sphinx/pro-support.md#engine-internals-pro-contract``
    and the public docstring of :class:`ralph.pro_support.hooks.ProPipelineHooks`):

    1. ``to_runner_kwargs()`` returns a dict with EXACTLY six
       entries — one per factory or passthrough, but never the
       ``policy_bundle_override`` field.
    2. The six forwarded values are the same objects the
       constructor received (identity-equality, not just equality).
    3. ``policy_bundle_override`` is NOT in the returned dict
       (the engine inspects it separately to short-circuit
       ``policy_bundle_factory``).
    4. The dataclass is ``frozen=True`` and ``slots=True``:
       direct attribute assignment (``hooks.x = y``) and direct
       attribute deletion (``del hooks.x``) both raise
       :class:`dataclasses.FrozenInstanceError`.

    NOTE: We use direct attribute mutation, NOT
    ``dataclasses.replace``. ``dataclasses.replace`` constructs
    a new instance via ``__init__`` and therefore bypasses the
    ``frozen=True`` guard, so it would not detect a regression
    in which someone removed ``frozen=True`` from the dataclass.
    """
    sentinel_bundle = cast("PolicyBundle", object())
    sentinel_policy = MagicMock(name="policy_bundle_factory")
    sentinel_registry = MagicMock(name="registry_factory")
    sentinel_state = MagicMock(name="state_factory")
    sentinel_recovery = MagicMock(name="recovery_controller_factory")
    sentinel_marker = MagicMock(name="marker_watcher_factory")
    sentinel_registry_holder = MagicMock(name="snapshot_registry")

    hooks = ProPipelineHooks(
        policy_bundle_factory=sentinel_policy,
        registry_factory=sentinel_registry,
        state_factory=sentinel_state,
        recovery_controller_factory=sentinel_recovery,
        marker_watcher_factory=sentinel_marker,
        policy_bundle_override=sentinel_bundle,
        snapshot_registry=sentinel_registry_holder,
    )

    # (1) Exactly six entries.
    kwargs = hooks.to_runner_kwargs()
    assert isinstance(kwargs, dict)
    assert len(kwargs) == 6, (
        f"to_runner_kwargs() must forward exactly 6 entries; got {sorted(kwargs)}"
    )

    # (2) Each forwarded value is identity-equal to the constructor argument.
    expected_keys = {
        "policy_bundle_factory",
        "registry_factory",
        "state_factory",
        "recovery_controller_factory",
        "marker_watcher_factory",
        "snapshot_registry",
    }
    assert set(kwargs) == expected_keys, (
        f"to_runner_kwargs() keys drifted; expected {sorted(expected_keys)} got {sorted(kwargs)}"
    )
    assert kwargs["policy_bundle_factory"] is sentinel_policy
    assert kwargs["registry_factory"] is sentinel_registry
    assert kwargs["state_factory"] is sentinel_state
    assert kwargs["recovery_controller_factory"] is sentinel_recovery
    assert kwargs["marker_watcher_factory"] is sentinel_marker
    assert kwargs["snapshot_registry"] is sentinel_registry_holder

    # (3) policy_bundle_override is NOT forwarded via to_runner_kwargs.
    assert "policy_bundle_override" not in kwargs, (
        "to_runner_kwargs() must NOT forward policy_bundle_override; "
        "run() inspects it separately to short-circuit policy_bundle_factory"
    )

    # (4) frozen=True: direct attribute assignment raises FrozenInstanceError.
    # Cast to a mutable object so the test compiles without inline ignore
    # markers (test files must have zero suppressions per
    # tests/test_type_ignore_policy.py::test_zero_test_file_suppressions).
    mutable_hooks: Any = hooks
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_hooks.policy_bundle_factory = MagicMock(name="replacement")

    # (4b) frozen=True: direct attribute deletion raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        del mutable_hooks.policy_bundle_factory

    # (4c) slots=True: the dataclass has __slots__ defined (the field set is fixed).
    assert dataclasses.fields(ProPipelineHooks) is not None
    assert hasattr(ProPipelineHooks, "__slots__"), (
        "ProPipelineHooks must use slots=True to keep the field set closed"
    )
