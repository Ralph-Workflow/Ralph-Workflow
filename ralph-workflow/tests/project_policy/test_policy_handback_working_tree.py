"""Working-tree hand-back regression (S-3) and footer neutralisation (S-7).

The pre-S-4 build leaves the policy surfaces the bootstrap seeded dirty
for the next phase to trip over. On the BLOCKED hand-back the helper
never reaches the auto-commit; on the post-remediation READY route the
helper DOES reach the auto-commit, but ``pre_run_dirty`` is taken AFTER
the bootstrap has already dirtied every policy surface, so the
deterministic ``commit_scoped_updates(..., exclude=pre_run_dirty)``
commits nothing. The pre_run_dirty snapshot must move ABOVE the
bootstrap so the policy surfaces the run actually authored are
committed, and the auto-commit must run on BOTH hand-back routes.

The S-7 case extends the same contract to the footer: a phase label
left pinned after its loop ended reads exactly like the hang this
phase label exists to rule out. The S-4 fix wraps the ``with display``
block in :func:`remediation_status_bar_session`, which captures the
pre-policy footer model on entry and restores it (or pushes the
neutral ``Running`` label) on exit.

The cases assert only the user-observable behavior: after the
hand-back, ``list_dirty_paths(workspace_scope.root)`` reports no
policy-scope path, and the pty-backed console's post-preflight
transcript does not redirect the operator's status bar at a
remediation label that has already finished. ``StatusBar._live`` and
every other private display attribute stay off limits. The cases use
the same real bundle the production closure resolves, so the chain
agent that runs is the ``policy_remediation`` chain from the bundled
defaults.
"""

from __future__ import annotations

import json
import os
import select
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.project_policy.policy_mode import PolicyMode
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps
    from ralph.workspace.protocol import Workspace


_POLICY_SCOPE_SEEDS: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/ralph-workflow-policy/agent-policy.md",
    "docs/ralph-workflow-policy/architecture-policy.md",
    "docs/ralph-workflow-policy/clean-code-policy.md",
    "docs/ralph-workflow-policy/dependency-policy.md",
    "docs/ralph-workflow-policy/documentation-policy.md",
    "docs/ralph-workflow-policy/gate-script-policy.md",
    "docs/ralph-workflow-policy/linting-policy.md",
    "docs/ralph-workflow-policy/security-policy.md",
    "docs/ralph-workflow-policy/testing-policy.md",
    "docs/ralph-workflow-policy/typechecking-policy.md",
    "docs/ralph-workflow-policy/verification-policy.md",
)


def _policy_paths_in_scopes(paths: frozenset[str]) -> frozenset[str]:
    """Return the subset of ``paths`` that fall under the auto-commit scopes."""
    return frozenset(paths) & frozenset(_POLICY_SCOPE_SEEDS)


def _load_git_run_result(tmp_git_repo: Path) -> tuple[FsWorkspace, WorkspaceScope, run_module._LoadResult]:
    """Build the workspace + workspace_scope + load result on the same git root."""
    workspace = FsWorkspace(tmp_git_repo, allowed_roots=[tmp_git_repo])
    workspace_scope = WorkspaceScope(
        root=str(tmp_git_repo),
        allowed_roots=[str(tmp_git_repo)],
    )
    bundle = load_policy(default_dir())
    load_result = run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=workspace_scope,
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=bundle,
        run_id="test-run-id",
    )
    return workspace, workspace_scope, load_result


def _ensure_run_func_state_unset() -> None:
    """Reset ``_state.run_func`` to ``_RUN_FUNC_UNSET`` before each test."""
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _build_handback_run_pipeline_stubs_for_git_root(
    *,
    load_result: run_module._LoadResult,
    preflight_order: list[str],
    captured_pipeline_state: list[PipelineState],
) -> dict[str, object]:
    """Build the run_module stubs the dirty-tree cases need."""
    from ralph.pipeline._runner_session import apply_session_capture

    def stub_load_configuration(*args: object, **kwargs: object) -> run_module._LoadResult:
        del args, kwargs
        preflight_order.append("load_configuration")
        return load_result

    def stub_preflight_checks(*args: object, **kwargs: object) -> int:
        del args, kwargs
        preflight_order.append("run_preflight_checks")
        return 0

    def stub_sync_shipped_skills(*args: object, **kwargs: object) -> None:
        del args, kwargs
        preflight_order.append("sync_shipped_skills")

    def stub_warn_capabilities(*args: object, **kwargs: object) -> None:
        del args, kwargs
        preflight_order.append("warn_capabilities")

    def stub_policy_readiness(
        *,
        load_result: run_module._LoadResult,
        display_context: DisplayContext,
        display: object | None = None,
        mode: PolicyMode = PolicyMode.NORMAL,
        workspace_factory: Callable[[], Workspace] | None = None,
        emit_factory: Callable[[str], None] | None = None,
        invoke_remediation_agent_factory: Callable[[Workspace], object] | None = None,
    ) -> int:
        del mode, invoke_remediation_agent_factory
        preflight_order.append("run_project_policy_readiness")
        return cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            display=display,
            workspace_factory=workspace_factory,
            emit_factory=emit_factory,
            is_tty=lambda: False,
        )

    def stub_execute_pipeline(
        *args: object,
        **kwargs: object,
    ) -> int:
        candidate: object = args[0] if args else kwargs.get("request")
        initial_state: PipelineState | None = getattr(candidate, "initial_state", None)
        if initial_state is None:
            raise AssertionError("stub_execute_pipeline received no initial_state")
        preflight_order.append("execute_pipeline")
        captured_pipeline_state.append(apply_session_capture(initial_state))
        return 0

    return {
        "_load_configuration": stub_load_configuration,
        "_run_preflight_checks": stub_preflight_checks,
        "_sync_shipped_skills_on_pipeline_run": stub_sync_shipped_skills,
        "_warn_if_capabilities_degraded": stub_warn_capabilities,
        "_run_project_policy_readiness": stub_policy_readiness,
        "_execute_pipeline": stub_execute_pipeline,
    }


if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext


@pytest.mark.timeout_seconds(10)
def test_blocked_handback_leaves_no_policy_scope_path_dirty(
    tmp_git_repo: Path,
) -> None:
    """The BLOCKED hand-back must commit the policy surfaces the bootstrap seeded."""
    _ensure_run_func_state_unset()
    _workspace, _workspace_scope, load_result = _load_git_run_result(tmp_git_repo)

    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    captured_pipeline_state: list[PipelineState] = []
    preflight_order: list[str] = []

    def failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        scoped_ws: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, scoped_ws, args, opts
        from ralph.pipeline._runner_session import (
            set_last_captured_session_id as _clear_session,
        )
        _clear_session(None)
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = failing_execute_agent_effect

    stubs = _build_handback_run_pipeline_stubs_for_git_root(
        load_result=load_result,
        preflight_order=preflight_order,
        captured_pipeline_state=captured_pipeline_state,
    )
    original_readiness = run_module._run_project_policy_readiness
    original_execute = run_module._execute_pipeline
    original_load = run_module._load_configuration
    original_preflight = run_module._run_preflight_checks
    original_sync = run_module._sync_shipped_skills_on_pipeline_run
    original_warn = run_module._warn_if_capabilities_degraded
    for attr_name, stub in stubs.items():
        setattr(run_module, attr_name, stub)

    try:
        request = run_module.RunPipelineRequest(
            config_path=None,
            cli_overrides=None,
            dry_run=False,
            resume=False,
            verbosity=None,
            counter_overrides=None,
            parallel_worker_manifest=None,
            pro_hooks=None,
            model_identity=None,
        )
        rc = run_module.run_pipeline(
            request=request,
            display_context=make_display_context(),
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor
        run_module._load_configuration = original_load
        run_module._run_preflight_checks = original_preflight
        run_module._sync_shipped_skills_on_pipeline_run = original_sync
        run_module._warn_if_capabilities_degraded = original_warn
        run_module._run_project_policy_readiness = original_readiness
        run_module._execute_pipeline = original_execute

    assert rc == 0, "NORMAL-mode run must continue into the development pipeline"
    assert "execute_pipeline" in preflight_order, (
        f"phase 4 was not reached after the BLOCKED preflight: {preflight_order!r}"
    )

    after_dirty = list_dirty_paths(tmp_git_repo)
    new_policy_dirty = _policy_paths_in_scopes(after_dirty - pre_run_dirty)
    assert not new_policy_dirty, (
        "BLOCKED hand-back left policy-scope paths dirty for the next "
        f"phase to trip over: {sorted(new_policy_dirty)!r}. The "
        "auto-commit must run on the BLOCKED hand-back too; "
        "the pre_run_dirty snapshot must be taken BEFORE the preflight "
        "seeds the policy surfaces."
    )


@pytest.mark.timeout_seconds(10)
def test_post_remediation_ready_handback_leaves_no_policy_scope_path_dirty(
    tmp_git_repo: Path,
) -> None:
    """The post-remediation READY hand-back must commit the policy surfaces."""
    _ensure_run_func_state_unset()
    workspace, _workspace_scope, load_result = _load_git_run_result(tmp_git_repo)

    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    captured_pipeline_state: list[PipelineState] = []
    preflight_order: list[str] = []

    def seed_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        scoped_ws: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, scoped_ws, args, opts
        from ralph.project_policy import markers as pp_markers
        from tests.project_policy.test_validator import _complete_policy_body

        if effect.phase == "policy_remediation":
            workspace.mkdirs(pp_markers.CANONICAL_DIR.rstrip("/"))
            for filename in pp_markers.CORE_POLICY_FILES:
                workspace.write(
                    f"{pp_markers.CANONICAL_DIR}{filename}",
                    _complete_policy_body(
                        filename=filename,
                        lang=None
                        if filename not in {
                            "typechecking-policy.md",
                            "linting-policy.md",
                        }
                        else "Python",
                    ),
                )
            workspace.write(
                pp_markers.AGENTS_MD,
                f"{pp_markers.AGENTS_BLOCK_BEGIN}\n"
                f"See {pp_markers.CANONICAL_DIR}.\n"
                f"{pp_markers.AGENTS_BLOCK_END}\n",
            )
            workspace.write(pp_markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md.\n")
        elif effect.phase == "policy_remediation_analysis":
            from ralph.project_policy import analysis as policy_analysis

            workspace.mkdirs(
                f"{pp_markers.CACHE_REL_PATH.rsplit('/', 1)[0]}/artifacts"
            )
            workspace.write(
                policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
                json.dumps(
                    {
                        "type": policy_analysis.ANALYSIS_ARTIFACT_TYPE,
                        "content": {
                            "status": "completed",
                            "summary": "policy is sound",
                        },
                    }
                ),
            )
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_SUCCESS

    original_executor = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = seed_execute_agent_effect

    stubs = _build_handback_run_pipeline_stubs_for_git_root(
        load_result=load_result,
        preflight_order=preflight_order,
        captured_pipeline_state=captured_pipeline_state,
    )
    original_readiness = run_module._run_project_policy_readiness
    original_execute = run_module._execute_pipeline
    original_load = run_module._load_configuration
    original_preflight = run_module._run_preflight_checks
    original_sync = run_module._sync_shipped_skills_on_pipeline_run
    original_warn = run_module._warn_if_capabilities_degraded
    for attr_name, stub in stubs.items():
        setattr(run_module, attr_name, stub)

    try:
        request = run_module.RunPipelineRequest(
            config_path=None,
            cli_overrides=None,
            dry_run=False,
            resume=False,
            verbosity=None,
            counter_overrides=None,
            parallel_worker_manifest=None,
            pro_hooks=None,
            model_identity=None,
        )
        rc = run_module.run_pipeline(
            request=request,
            display_context=make_display_context(),
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor
        run_module._load_configuration = original_load
        run_module._run_preflight_checks = original_preflight
        run_module._sync_shipped_skills_on_pipeline_run = original_sync
        run_module._warn_if_capabilities_degraded = original_warn
        run_module._run_project_policy_readiness = original_readiness
        run_module._execute_pipeline = original_execute

    assert rc == 0, "NORMAL-mode run must continue into the development pipeline"
    assert "execute_pipeline" in preflight_order, (
        f"phase 4 was not reached after the post-remediation preflight: "
        f"{preflight_order!r}"
    )

    after_dirty = list_dirty_paths(tmp_git_repo)
    new_policy_dirty = _policy_paths_in_scopes(after_dirty - pre_run_dirty)
    assert not new_policy_dirty, (
        "post-remediation READY hand-back left policy-scope paths dirty: "
        f"{sorted(new_policy_dirty)!r}. The pre_run_dirty snapshot must "
        "be taken BEFORE the preflight seeds the policy surfaces, "
        "otherwise the deterministic chore commit's exclusion set "
        "swallows the surfaces the run actually authored."
    )


@pytest.mark.timeout_seconds(10)
def test_post_preflight_footer_is_neutralized_after_handback(
    tmp_git_repo: Path,
) -> None:
    """The post-preflight footer does not still advertise a remediation loop."""
    _ensure_run_func_state_unset()

    workspace, _workspace_scope, load_result = _load_git_run_result(tmp_git_repo)

    master_fd, slave_fd = os.openpty()
    os.set_blocking(master_fd, False)
    slave_file = os.fdopen(slave_fd, "w")

    console = Console(file=slave_file, force_terminal=True)
    accumulated: list[bytes] = []
    stop_event = threading.Event()

    def _drain() -> None:
        while not stop_event.is_set():
            try:
                readable, _, _ = select.select([master_fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except (BlockingIOError, OSError):
                continue
            if not chunk:
                continue
            accumulated.append(chunk)

    threading.Thread(target=_drain, name="pytest-pty-drain", daemon=True).start()

    def failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        scoped_ws: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, scoped_ws, args, opts
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = failing_execute_agent_effect

    try:
        rc = cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(console=console),
            workspace_factory=lambda: workspace,
            emit_factory=lambda _m: None,
            is_tty=lambda: False,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor
        stop_event.set()
        try:
            os.set_blocking(master_fd, False)
            while True:
                try:
                    _chunk = os.read(master_fd, 4096)
                except (BlockingIOError, OSError):
                    break
                if not _chunk:
                    break
        finally:
            for fd in (master_fd, slave_fd):
                with suppress(OSError):
                    os.close(fd)

    assert rc == 0, "preflight should exit 0 even on the BLOCKED hand-back"
    transcript = b"".join(accumulated).decode("utf-8", errors="replace")
    assert "Remediation 1" not in transcript, (
        "post-preflight footer still renders the 'Remediation 1' label "
        "after the BLOCKED hand-back -- a phase label left pinned after "
        "its loop ended reads exactly like the hang this phase label "
        "exists to rule out. The S-4 fix wraps the 'with display' "
        "block in remediation_status_bar_session (mirroring "
        "conflict_status_bar_session) so the pre-policy footer is "
        "captured on entry and restored on exit."
    )
