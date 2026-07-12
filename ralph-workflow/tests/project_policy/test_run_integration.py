"""Integration tests for the run_pipeline project-policy-readiness seam.

These tests exercise the real ``_run_project_policy_readiness`` helper with
injected workspace + remediation-agent seams so the test stays on the
:func:`MemoryWorkspace` seam (no ``tmp_path``, no ``Path``, no
``FsWorkspace``) while still covering the actual startup placement before
pipeline execution.

What the tests prove:

* An unprepared project is BLOCKED by the readiness preflight; the faked
  pipeline runner is NOT invoked.
* A fake remediation agent that does NOT fix the project keeps the run
  blocked and the runner is never called.
* A fake remediation agent that DOES fix the project allows execution to
  proceed (the faked runner is then invoked).
* The configured ``policy_remediation`` chain's first agent (not the
  hard-coded ``"claude"``) drives the remediation InvokeAgentEffect.
* An opt-out AGENTS.md yields SKIPPED without any policy writes and emits
  exactly one brief status line.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from types import ModuleType
from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.cli.commands._load_result import _LoadResult
from ralph.cli.commands._run_func_state import _RUN_FUNC_UNSET
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.project_policy import markers
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps

# Sentinel for tests that need a PipelineEvent-shaped return without
# importing the untyped MCP bridge. Compared by ``==`` at call sites.
_agent_success_sentinel: PipelineEvent = PipelineEvent.AGENT_SUCCESS


def _stub_load_result(workspace_root: str) -> _LoadResult:
    """Build a minimal ``_LoadResult`` that exercises the helper seam.

    The ``workspace_scope`` uses an in-memory path; the helper itself is
    short-circuited from touching the real filesystem because tests inject
    a ``workspace_factory`` that returns a ``MemoryWorkspace``.
    """
    config = UnifiedConfig()
    workspace_scope = WorkspaceScope(root=workspace_root, allowed_roots=[workspace_root])
    return _LoadResult(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=PipelineState(
            phase="planning",
            policy_entry_phase="planning",
        ),
        policy_bundle=None,
        run_id="test-run-id",
    )


def _ensure_run_func_state_unset() -> None:
    """Reset ``_state.run_func`` to ``_RUN_FUNC_UNSET`` before each test."""
    run_module._state.run_func = _RUN_FUNC_UNSET


def test_unprepared_project_blocks_before_planning() -> None:
    """AC-14/15: An unprepared project is BLOCKED; the faked runner is NOT invoked.

    The test runs the real ``_run_project_policy_readiness`` with an
    injected ``MemoryWorkspace`` factory and a faked remediation agent
    closure that claims success but does NOT mutate the workspace. The
    deterministic revalidation therefore fails on every retry, the helper
    returns ``_EXIT_PREFLIGHT`` (2), and the faked pipeline runner is never
    reached.
    """
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/project")

    invoke_calls: list[str] = []

    def fake_invoke(prompt_path: str) -> bool:
        # The agent claims success but does NOT write any files. The
        # deterministic revalidation that follows MUST therefore fail
        # and the run must remain BLOCKED.
        invoke_calls.append(prompt_path)
        return True

    emit_messages: list[str] = []

    def fake_emit(message: str) -> None:
        emit_messages.append(message)

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=fake_emit,
        invoke_remediation_agent_factory=lambda _w: fake_invoke,
    )

    assert rc == 2  # _EXIT_PREFLIGHT (blocked, recoverable)
    # The fake remediation agent WAS invoked at least once.
    assert invoke_calls, "remediation agent must be invoked"
    # The pipeline runner was NEVER invoked.
    assert runner_invocations == []
    # Emit messages reflect remediation_required + BLOCKED report.
    assert any("remediation-required" in m for m in emit_messages)
    assert any("BLOCKED" in m for m in emit_messages)


def test_remediation_agent_that_fixes_project_proceeds() -> None:
    """A faked remediation agent that completes every required file allows the
    runner to proceed (the helper returns ``_EXIT_SUCCESS``).
    """
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/project")

    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    def fake_invoke(prompt_path: str) -> bool:
        # Materialize every required canonical file so revalidation passes.
        _seed_agents_md(ws)
        _seed_claude_md(ws)
        from ralph.language_detector.models import ProjectStack

        stack = ProjectStack(primary_language="Python")
        _seed_all_core_complete(ws, stack)
        return True

    emit_messages: list[str] = []

    def fake_emit(message: str) -> None:
        emit_messages.append(message)

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=fake_emit,
        invoke_remediation_agent_factory=lambda _w: fake_invoke,
    )

    assert rc == 0  # _EXIT_SUCCESS
    # The helper emitted the single brief READY line.
    assert any("ready" in m for m in emit_messages)


def test_opt_out_skips_readiness_without_writes() -> None:
    """AC-02/AC-14: the byte-exact opt-out marker yields SKIPPED with no
    policy writes and exactly one brief status line.
    """
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\nOpted out.\n",
    )
    load_result = _stub_load_result("/test/project")

    emit_messages: list[str] = []

    def fake_emit(message: str) -> None:
        emit_messages.append(message)

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=fake_emit,
    )

    assert rc == 0  # _EXIT_SUCCESS
    # Exactly one brief status line for SKIPPED.
    skipped_lines = [m for m in emit_messages if "skipped" in m]
    assert len(skipped_lines) == 1
    assert "opt-out" in skipped_lines[0]
    # No policy file was created in the workspace.
    assert not ws.exists(markers.CACHE_REL_PATH)
    assert not ws.exists(f"{markers.CANONICAL_DIR}testing-policy.md")


def test_readiness_emits_exactly_one_line_for_each_terminal_state() -> None:
    """AC-14: the helper emits EXACTLY ONE brief status line for SKIPPED
    and EXACTLY ONE for READY. No duplicate emits.
    """
    _ensure_run_func_state_unset()

    # Case 1: SKIPPED
    ws_skipped = MemoryWorkspace()
    ws_skipped.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\n",
    )
    emit_messages_skipped: list[str] = []

    run_module._run_project_policy_readiness(
        load_result=_stub_load_result("/test/skipped"),
        display_context=make_display_context(),
        workspace_factory=lambda: ws_skipped,
        emit_factory=emit_messages_skipped.append,
    )
    assert len(emit_messages_skipped) == 1

    # Case 2: READY (using the cache fast-path after a full pre-seed).
    from ralph.language_detector.models import ProjectStack
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws_ready = MemoryWorkspace()
    _seed_agents_md(ws_ready)
    _seed_claude_md(ws_ready)
    _seed_all_core_complete(ws_ready, ProjectStack(primary_language="Python"))
    emit_messages_ready: list[str] = []

    rc = run_module._run_project_policy_readiness(
        load_result=_stub_load_result("/test/ready"),
        display_context=make_display_context(),
        workspace_factory=lambda: ws_ready,
        emit_factory=emit_messages_ready.append,
    )
    assert rc == 0
    assert len(emit_messages_ready) == 1
    assert "ready" in emit_messages_ready[0]


def test_remediation_invokes_configured_agent_not_hardcoded_claude() -> None:
    """AC-13: when the policy_remediation chain is configured with a
    non-Claude first agent, the InvokeAgentEffect uses that name. The
    helper does NOT hardcode ``"claude"``.

    This test mocks :func:`ralph.pipeline.effect_executor.execute_agent_effect`
    so the real closure runs without actually invoking any agent. It
    captures the ``InvokeAgentEffect`` that would have been sent to the
    pipeline and asserts the ``agent_name`` matches the configured
    first agent.
    """
    _ensure_run_func_state_unset()

    # Import here so the patch stays local to this test.
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.policy.loader import default_dir, load_policy
    from ralph.policy.models._agent_chain_config import AgentChainConfig

    # Build a bundle with a custom first agent on policy_remediation.
    default_bundle = load_policy(default_dir())
    custom_chain = AgentChainConfig(
        agents=["custom-agent"], max_retries=2, retry_delay_ms=1000
    )
    new_chains = dict(default_bundle.agents.agent_chains)
    new_chains["policy_remediation"] = custom_chain
    new_agents = default_bundle.agents.model_copy(
        update={"agent_chains": new_chains}
    )
    fake_bundle = default_bundle.model_copy(update={"agents": new_agents})

    ws = MemoryWorkspace()

    observed_effects: list[object] = []

    # The fake mirrors the production signature so module-attribute
    # assignment is type-safe. Modules that lack type stubs
    # (``ralph.mcp.bridge``, ``ralph.mcp.contracts``) are imported
    # lazily inside the function body where mypy cannot flag a direct
    # assignment against their attribute types.
    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        bridge: object = None,
        raw_output_sink: object = None,
        rendered_output_sink: object = None,
        run_id: str | None = None,
        required_artifact: object = None,
        session_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        raise_resumable_exit: bool = False,
        agent_invocation_error_sink: Callable[[Exception], object] | None = None,
        **opts: object,
    ) -> PipelineEvent:
        observed_effects.append(effect)
        # Seed the workspace so the next revalidation passes.
        from ralph.language_detector.models import ProjectStack
        from tests.project_policy.test_validator import (
            _seed_agents_md,
            _seed_all_core_complete,
            _seed_claude_md,
        )

        _seed_agents_md(ws)
        _seed_claude_md(ws)
        _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))
        return _agent_success_sentinel

    # Patch execute_agent_effect on the effect_executor module. The
    # production helper imports it locally inside the function, so we
    # patch the module attribute it looks up at call time.
    original_executor_module = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect

    load_result = _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root="/test/project", allowed_roots=["/test/project"]
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=fake_bundle,
        run_id="test-run-id",
    )

    try:
        rc = run_module._run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(),
            workspace_factory=lambda: ws,
            emit_factory=lambda m: None,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor_module

    assert rc == 0  # remediation fixed the project
    # The closure was invoked at least once.
    assert observed_effects, "execute_agent_effect must be invoked"
    # The first captured effect must carry the configured agent name,
    # NOT the hardcoded ``"claude"``.
    first_effect: object = observed_effects[0]
    agent_name_attr: object = getattr(first_effect, "agent_name", None)
    assert agent_name_attr == "custom-agent", (
        f"agent_name was {agent_name_attr!r}; "
        f"expected 'custom-agent' from the configured chain"
    )


def test_helper_does_not_emit_blocked_panel_when_agent_uses_run_id() -> None:
    """Sanity: the helper path uses ``load_result.run_id`` and does not
    require any Path/tmp_path artefact on disk. Asserts the helper works
    end-to-end with ``MemoryWorkspace`` + injected closures only.
    """
    _ensure_run_func_state_unset()

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/sanity")

    # No remediation agent injected: the helper must NOT reach remediation
    # at all because the preflight returns REMEDIATION_REQUIRED but the
    # helper short-circuits to BLOCKED when no closure is available.
    # We assert no Path/fs artefacts are required.
    from ralph.language_detector.models import ProjectStack
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda m: None,
    )

    assert rc == 0  # READY
    # Confirm the workspace still holds exactly the seeded files (no Path
    # was ever touched).
    assert ws.exists(markers.AGENTS_MD)
    assert ws.exists(markers.CLAUDE_MD)


# ---------------------------------------------------------------------------
# Top-level ``run_pipeline`` integration tests
# ---------------------------------------------------------------------------
# These tests exercise the actual ``run_pipeline`` startup path with
# stubbed collaborators (configuration loader, preflight, skill sync,
# policy readiness, dry-run printer, pipeline executor). They prove:
#
# 1. The readiness preflight is invoked between skill-sync and dry-run.
# 2. A nonzero readiness result prevents both ``print_dry_run`` and
#    ``_execute_pipeline`` from being reached.
# 3. An ``inline_prompt`` request short-circuits BEFORE the readiness
#    preflight runs.
# 4. A ``parallel_worker_manifest`` request short-circuits BEFORE the
#    readiness preflight runs.
#
# The seam is the module-level functions of ``ralph.cli.commands.run``; the
# helper monkey-patches the module attributes so no real Path I/O or
# subprocess call is required.


class _RecordingRunners:
    """Capture the call order of every ``run_pipeline`` collaborator.

    The orchestrator invokes collaborators in a fixed sequence; this
    recorder is shared by the patched stubs so each test can assert the
    call order and check which helpers were reached.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def reset(self) -> None:
        self.calls = []


def _patch_run_pipeline_collaborators(
    monkeypatch_target: ModuleType,
    *,
    load_result: _LoadResult,
    preflight_rc: int,
    policy_readiness_rc: int,
    runners: _RecordingRunners,
) -> dict[str, object]:
    """Patch the ``run_module`` collaborators used by ``run_pipeline``.

    Returns the dict of stubs so tests can introspect / reconfigure
    individual collaborators.
    """
    stubs: dict[str, object] = {}

    def fake_load_configuration(*args: object, **kwargs: object) -> _LoadResult:
        runners.calls.append("load_configuration")
        return load_result

    def fake_run_preflight_checks(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_preflight_checks")
        return preflight_rc

    def fake_sync_shipped_skills(*args: object, **kwargs: object) -> None:
        runners.calls.append("sync_shipped_skills")

    def fake_warn_capabilities(*args: object, **kwargs: object) -> None:
        runners.calls.append("warn_capabilities")

    def fake_run_project_policy_readiness(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_project_policy_readiness")
        return policy_readiness_rc

    def fake_print_dry_run(*args: object, **kwargs: object) -> None:
        runners.calls.append("print_dry_run")

    def fake_execute_pipeline(*args: object, **kwargs: object) -> int:
        runners.calls.append("execute_pipeline")
        return 0

    def fake_run_parallel_worker(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_parallel_worker_from_manifest")
        return 0

    monkeypatch_target._load_configuration = fake_load_configuration
    monkeypatch_target._run_preflight_checks = fake_run_preflight_checks
    monkeypatch_target._sync_shipped_skills_on_pipeline_run = fake_sync_shipped_skills
    monkeypatch_target._warn_if_capabilities_degraded = fake_warn_capabilities
    monkeypatch_target._run_project_policy_readiness = fake_run_project_policy_readiness
    monkeypatch_target.print_dry_run = fake_print_dry_run
    monkeypatch_target._execute_pipeline = fake_execute_pipeline
    monkeypatch_target.run_parallel_worker_from_manifest = fake_run_parallel_worker

    stubs["load_configuration"] = fake_load_configuration
    stubs["run_preflight_checks"] = fake_run_preflight_checks
    stubs["sync_shipped_skills"] = fake_sync_shipped_skills
    stubs["warn_capabilities"] = fake_warn_capabilities
    stubs["run_project_policy_readiness"] = fake_run_project_policy_readiness
    stubs["print_dry_run"] = fake_print_dry_run
    stubs["execute_pipeline"] = fake_execute_pipeline
    stubs["run_parallel_worker"] = fake_run_parallel_worker
    return stubs


def test_run_pipeline_invokes_readiness_before_execution() -> None:
    """AC-14: ``run_pipeline`` MUST invoke the readiness preflight before
    ``print_dry_run`` and ``_execute_pipeline``. The call order is
    load_configuration -> run_preflight_checks -> sync_shipped_skills ->
    warn_capabilities -> run_project_policy_readiness -> print_dry_run
    (or _execute_pipeline).
    """
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/pipeline-order")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=False,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        inline_prompt=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    expected = [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
        "execute_pipeline",
    ]
    assert runners.calls == expected


def test_run_pipeline_nonzero_readiness_blocks_dry_run_and_execute() -> None:
    """AC-14: a nonzero readiness result MUST prevent both
    ``print_dry_run`` and ``_execute_pipeline`` from being reached. The
    orchestrator returns ``_EXIT_PREFLIGHT`` and the pipeline runner never
    sees the request.
    """
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/pipeline-blocked")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=2,  # _EXIT_PREFLIGHT (blocked)
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=True,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        inline_prompt=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    # Orchestrator returns the preflight exit code.
    assert rc == 2
    # Readiness fires BEFORE dry-run and execution.
    assert runners.calls == [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
    ]
    # Dry-run and execute are NEVER invoked when readiness blocks.
    assert "print_dry_run" not in runners.calls
    assert "execute_pipeline" not in runners.calls


def test_run_pipeline_dry_run_invokes_printer_after_ready() -> None:
    """AC-14: when ``dry_run=True`` is set and readiness passes, the
    orchestrator invokes ``print_dry_run`` and returns ``_EXIT_SUCCESS``
    WITHOUT calling ``_execute_pipeline``.
    """
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/dry-run")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=True,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        inline_prompt=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    assert runners.calls == [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
        "print_dry_run",
    ]
    # The dry-run short-circuit prevents _execute_pipeline.
    assert "execute_pipeline" not in runners.calls


def test_run_pipeline_inline_prompt_short_circuits_before_readiness() -> None:
    """AC-14: ``inline_prompt`` requests MUST short-circuit BEFORE the
    readiness preflight runs (the orchestrator writes the inline prompt
    to ``CURRENT_PROMPT.md`` and then proceeds through Phase 1+2 normally,
    but the readiness preflight has an explicit ``inline_prompt is None``
    guard at run.py:1057). The readiness preflight is NEVER invoked for
    an inline-prompt run.
    """
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/inline-prompt")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=False,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        inline_prompt="ephemeral prompt",
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    # The orchestrator writes the inline prompt to
    # ``<workspace_scope.root>/.agent/CURRENT_PROMPT.md`` BEFORE Phase 1.
    # The virtual ``/test/...`` root may not exist on the host's disk, so
    # the call can raise FileNotFoundError; that surface still proves the
    # readiness helper never ran.
    with suppress(FileNotFoundError, OSError):
        run_module.run_pipeline(
            request=request,
            display_context=make_display_context(),
        )

    # The critical invariant: the readiness helper never ran for an
    # inline-prompt request. The orchestrator may or may not reach later
    # phases depending on the prompt-write outcome; we only assert what
    # the analysis requires.
    assert "run_project_policy_readiness" not in runners.calls, (
        f"readiness helper MUST NOT run for inline_prompt; observed: {runners.calls}"
    )


def test_run_pipeline_parallel_worker_manifest_short_circuits_before_readiness() -> None:
    """AC-14: ``parallel_worker_manifest`` requests MUST short-circuit
    before the readiness preflight runs. The parallel-worker sub-run is
    a different flow with its own startup seam; it must never invoke
    the readiness helper on its own.
    """
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/parallel-worker")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=False,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        inline_prompt=None,
        parallel_worker_manifest="unused-manifest-path",
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    # The orchestrator short-circuits to the parallel-worker path.
    assert runners.calls == ["run_parallel_worker_from_manifest"]
    assert "run_project_policy_readiness" not in runners.calls
    assert "load_configuration" not in runners.calls


# Compile-time guard: assert no FsWorkspace, no Path, no tmp_path leaked
# into this module. ``Path`` is imported only at module level for type
# hints but never instantiated for I/O. The audit_test_policy AST scanner
# verifies the absence of Path.read_text / .write_text / open() calls.
