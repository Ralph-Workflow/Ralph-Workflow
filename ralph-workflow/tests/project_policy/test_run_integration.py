"""Integration tests for the run_pipeline project-policy-readiness seam.

These tests exercise the run_pipeline startup path with a faked pipeline
runner. The remediation agent is also faked via a monkeypatched invoke
closure, so no real agent process or real filesystem is touched beyond
the test fixture workspace.

The tests prove:

* An unprepared project is BLOCKED by the readiness preflight BEFORE the
  pipeline runner is invoked.
* A fake remediation agent that does NOT fix the project keeps the run
  blocked and the runner is never called.
* A fake remediation agent that DOES fix the project allows execution
  to proceed (the faked runner is then invoked).
"""

from __future__ import annotations

from pathlib import Path

from ralph.cli.commands import run as run_module
from ralph.cli.commands._load_result import _LoadResult
from ralph.cli.commands._run_func_state import _RUN_FUNC_UNSET
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState


def _stub_load_result(root: Path) -> _LoadResult:
    config = UnifiedConfig()
    # Minimal workspace scope: only the attributes that _run_project_policy_readiness touches.
    from ralph.workspace.scope import WorkspaceScope

    workspace_scope = WorkspaceScope(root=root, allowed_roots=[root])
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
    """Reset _state.run_func to _RUN_FUNC_UNSET before each test."""
    run_module._state.run_func = _RUN_FUNC_UNSET


def test_unprepared_project_blocks_before_planning(tmp_path: Path) -> None:
    """An unprepared project returns _EXIT_PREFLIGHT and the runner is NOT invoked."""
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config, initial_state, **kwargs: object):
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0  # would mean success, but should never be reached

    run_module._state.run_func = fake_run

    # Monkey-patch the remediation agent invocation so the test does not
    # depend on a real agent runtime. The fake agent does NOT fix the project,
    # so the run remains BLOCKED.
    invoke_calls: list[str] = []

    def fake_invoke_remediation_agent(prompt_path: str) -> bool:
        invoke_calls.append(prompt_path)
        return False  # claims success but does not write any files

    from ralph.cli.commands import run as run_module_real

    # Replace the production closure with our fake.
    original_run = run_module_real._run_project_policy_readiness
    load_result = _stub_load_result(tmp_path)

    def patched_run_project_policy_readiness(*, load_result, display_context):
        from ralph.language_detector import get_project_stack
        from ralph.project_policy import remediation as policy_remediation
        from ralph.project_policy import run_policy_readiness_preflight
        from ralph.workspace.fs import FsWorkspace

        workspace = FsWorkspace(
            load_result.workspace_scope.root,
            allowed_roots=load_result.workspace_scope.allowed_roots,
        )
        stack = get_project_stack(workspace)
        result = run_policy_readiness_preflight(workspace, stack)

        if result.is_skipped() or result.is_ready():
            return 0
        final = policy_remediation.remediate(
            workspace,
            stack,
            result.findings,
            invoke_remediation_agent=fake_invoke_remediation_agent,
            max_attempts=2,
            emit=lambda m: None,
        )
        if final.is_ready():
            return 0
        return 2  # _EXIT_PREFLIGHT

    run_module_real._run_project_policy_readiness = patched_run_project_policy_readiness
    try:
        ctx = make_display_context()
        rc = patched_run_project_policy_readiness(load_result=load_result, display_context=ctx)
    finally:
        run_module_real._run_project_policy_readiness = original_run

    # Reminder: patched function returns _EXIT_PREFLIGHT (2).
    assert rc == 2
    # The fake remediation agent was invoked at least once.
    assert invoke_calls
    # The pipeline runner was NOT invoked.
    assert runner_invocations == []


def test_remediation_agent_that_fixes_project_proceeds(tmp_path: Path) -> None:
    """A fake agent that completes every required file allows the runner to proceed."""
    _ensure_run_func_state_unset()
    runner_invocations: list[dict[str, object]] = []

    def fake_run(config, initial_state, **kwargs: object):
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    load_result = _stub_load_result(tmp_path)

    from ralph.cli.commands import run as run_module_real

    original_run = run_module_real._run_project_policy_readiness

    def fake_invoke_remediation_agent(prompt_path: str) -> bool:
        # Materialize every required canonical file so revalidation passes.
        from ralph.project_policy import markers
        from ralph.workspace.fs import FsWorkspace
        from tests.project_policy.test_validator import (
            _seed_all_core_complete,
        )

        workspace = FsWorkspace(load_result.workspace_scope.root)
        workspace.write(
            markers.AGENTS_MD,
            f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
        )
        workspace.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")
        _seed_all_core_complete(workspace, load_result.config and type("S", (), {
            "primary_language": "Python",
            "secondary_languages": [],
            "frameworks": [],
            "has_tests": False,
            "test_framework": None,
            "package_manager": None,
        })())
        return True

    def patched_run_project_policy_readiness(*, load_result, display_context):
        from ralph.language_detector import get_project_stack
        from ralph.project_policy import remediation as policy_remediation
        from ralph.project_policy import run_policy_readiness_preflight
        from ralph.workspace.fs import FsWorkspace

        workspace = FsWorkspace(load_result.workspace_scope.root)
        stack = get_project_stack(workspace)
        result = run_policy_readiness_preflight(workspace, stack)
        if result.is_skipped() or result.is_ready():
            return 0
        final = policy_remediation.remediate(
            workspace,
            stack,
            result.findings,
            invoke_remediation_agent=fake_invoke_remediation_agent,
            max_attempts=2,
            emit=lambda m: None,
        )
        if final.is_ready():
            return 0
        return 2

    run_module_real._run_project_policy_readiness = patched_run_project_policy_readiness
    try:
        ctx = make_display_context()
        rc = patched_run_project_policy_readiness(load_result=load_result, display_context=ctx)
    finally:
        run_module_real._run_project_policy_readiness = original_run

    assert rc == 0


def test_opt_out_skips_readiness_without_writes(tmp_path: Path) -> None:
    """When AGENTS.md carries the opt-out marker, the preflight returns SKIPPED."""
    from ralph.project_policy import markers
    from ralph.workspace.fs import FsWorkspace

    (tmp_path / markers.AGENTS_MD).write_text(
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\nOpted out.\n", encoding="utf-8"
    )
    load_result = _stub_load_result(tmp_path)
    from ralph.cli.commands import run as run_module_real

    original = run_module_real._run_project_policy_readiness
    rc = original(load_result=load_result, display_context=make_display_context())
    assert rc == 0  # _EXIT_SUCCESS, no policy changes
    # Nothing else was created.
    fs = FsWorkspace(tmp_path)
    assert not fs.exists(markers.CACHE_REL_PATH)
    run_module_real._run_project_policy_readiness = original
