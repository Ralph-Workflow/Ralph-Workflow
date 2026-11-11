from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import cli_integration, preflight, validators
from ralph.project_policy.policy_mode import PolicyMode
from ralph.project_policy.remediation import RemediationInvocationError
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.workspace.protocol import Workspace

_LoadResult = run_module._LoadResult
_EXIT_SUCCESS = cli_integration._EXIT_SUCCESS
_EXIT_PREFLIGHT = cli_integration._EXIT_PREFLIGHT


def _load_result() -> _LoadResult:
    root = "/test/policy-never-blocks"
    return _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root=root, allowed_roots=[root]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=None,
        run_id="test-run-id",
    )


def _run(
    workspace: Workspace,
    *,
    mode: PolicyMode = PolicyMode.NORMAL,
    invoke: Callable[[Workspace], InvokePolicyAgent] | None = None,
    emitted: list[str] | None = None,
) -> int:
    sink = emitted if emitted is not None else []
    return cli_integration.run_project_policy_readiness(
        load_result=_load_result(),
        display_context=make_display_context(),
        mode=mode,
        workspace_factory=lambda: workspace,
        emit_factory=sink.append,
        invoke_remediation_agent_factory=invoke,
        is_tty=lambda: False,
    )


def _agent_that_never_fixes_anything(_ws: Workspace) -> InvokePolicyAgent:
    def invoke(*, phase: str, prompt_path: str) -> bool:
        del phase, prompt_path
        return False

    return invoke


def test_a_policy_that_cannot_be_made_ready_still_exits_zero() -> None:
    exit_code = _run(MemoryWorkspace(), invoke=_agent_that_never_fixes_anything)

    assert exit_code == _EXIT_SUCCESS


def test_an_agent_that_cannot_be_launched_still_exits_zero() -> None:

    def invoke(_ws: Workspace) -> InvokePolicyAgent:
        def _raise(*, phase: str, prompt_path: str) -> bool:
            del phase, prompt_path
            raise RemediationInvocationError("agent binary not found")

        return _raise

    assert _run(MemoryWorkspace(), invoke=invoke) == _EXIT_SUCCESS


def test_a_missing_agent_chain_still_exits_zero() -> None:
    assert _run(MemoryWorkspace()) == _EXIT_SUCCESS


@pytest.mark.parametrize(
    "boom",
    [
        AttributeError("'NoneType' object has no attribute 'findings'"),
        KeyError("gate-script-policy.md"),
        OSError("disk exploded"),
        RuntimeError("a bug we have not written yet"),
        ValueError("malformed policy file"),
    ],
    ids=["attribute", "key", "os", "runtime", "value"],
)
def test_any_bug_in_the_policy_code_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, boom: Exception
) -> None:

    def explode(*_args: object, **_kwargs: object) -> list[object]:
        raise boom

    monkeypatch.setattr(validators, "validate_readiness", explode)
    monkeypatch.setattr(preflight.validators, "validate_readiness", explode)

    emitted: list[str] = []
    exit_code = _run(MemoryWorkspace(), emitted=emitted)

    assert exit_code == _EXIT_SUCCESS
    assert any("failed unexpectedly" in line for line in emitted), (
        "the crash must be reported to the user, not silently swallowed"
    )


def test_a_crash_in_the_display_does_not_escape_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def explode(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("policy is broken")

    def bad_emit(_message: str) -> None:
        raise RuntimeError("the display is broken too")

    monkeypatch.setattr(validators, "validate_readiness", explode)
    monkeypatch.setattr(preflight.validators, "validate_readiness", explode)

    exit_code = cli_integration.run_project_policy_readiness(
        load_result=_load_result(),
        display_context=make_display_context(),
        workspace_factory=MemoryWorkspace,
        emit_factory=bad_emit,
        is_tty=lambda: False,
    )

    assert exit_code == _EXIT_SUCCESS


def test_a_failing_capture_restore_does_not_block_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loguru import logger as _loguru_logger

    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline._runner_session import set_last_captured_session_id
    from ralph.pipeline.events import PipelineEvent
    from tests.project_policy.policy_corpus import (
        policy_invocation_bundle,
        remediation_required_result,
    )

    debug_records: list[str] = []

    def _record_sink(message: object) -> None:
        rendered = str(message.record["message"]) if hasattr(message, "record") else str(message)
        debug_records.append(rendered)

    sink_id = _loguru_logger.add(_record_sink, level="DEBUG", format="{message}")

    raise_calls: list[str] = []

    def raising_set_last_captured_session_id(session_id: str | None) -> None:
        raise_calls.append(session_id if session_id is not None else "")
        raise RuntimeError("capture restore is broken")

    def seed_publish_execute_agent_effect(
        effect: object,
        config: object,
        pipeline_deps: object,
        workspace_scope: object,
        *args: object,
        **opts: object,
    ) -> PipelineEvent:
        set_last_captured_session_id("policy-session-id-leaked")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        cli_integration,
        "set_last_captured_session_id",
        raising_set_last_captured_session_id,
    )
    monkeypatch.setattr(
        effect_executor_module,
        "execute_agent_effect",
        seed_publish_execute_agent_effect,
    )
    monkeypatch.setattr(
        cli_integration,
        "run_policy_readiness_preflight",
        lambda *_args, **_kwargs: remediation_required_result(),
    )

    bundle = policy_invocation_bundle()
    load_result = run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root="/test/policy-capture-restore",
            allowed_roots=["/test/policy-capture-restore"],
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=bundle,
        run_id="test-run-id",
    )

    try:
        emitted: list[str] = []
        rc = cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(),
            workspace_factory=MemoryWorkspace,
            emit_factory=emitted.append,
            is_tty=lambda: False,
            working_tree_snapshot=lambda _scope: frozenset(),
            commit_policy_updates=lambda _scope, _dirty: None,
        )
    finally:
        _loguru_logger.remove(sink_id)

    raise_calls_after_run = list(raise_calls)
    assert raise_calls_after_run, (
        "the raising set_last_captured_session_id fake was never called; "
        "the run did not exercise the out-of-graph boundary"
    )
    delete_records = [r for r in debug_records if "capture restore is broken" in r]
    assert delete_records, f"the restore failure was not logged at DEBUG level: {debug_records!r}"
    assert rc == _EXIT_SUCCESS, f"a failing capture restore must not block the run; got rc={rc}"
    assert not any("failed unexpectedly" in line for line in emitted), (
        f"the injected restore failure reached the module fault boundary: {emitted!r}"
    )


def test_keyboard_interrupt_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:

    def interrupt(*_args: object, **_kwargs: object) -> list[object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(validators, "validate_readiness", interrupt)
    monkeypatch.setattr(preflight.validators, "validate_readiness", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _run(MemoryWorkspace())


@pytest.mark.parametrize("mode", [PolicyMode.REDO_ONLY, PolicyMode.RUN_AGENTS_ONLY], ids=str)
def test_only_modes_do_report_failure_through_their_exit_code(
    mode: PolicyMode,
) -> None:
    exit_code = _run(MemoryWorkspace(), mode=mode, invoke=_agent_that_never_fixes_anything)

    assert exit_code == _EXIT_PREFLIGHT


@pytest.mark.parametrize(
    "mode",
    [PolicyMode.NORMAL, PolicyMode.REDO, PolicyMode.RUN_AGENTS],
    ids=str,
)
def test_no_continuing_mode_can_ever_return_nonzero(mode: PolicyMode) -> None:
    exit_code = _run(MemoryWorkspace(), mode=mode, invoke=_agent_that_never_fixes_anything)

    assert exit_code == _EXIT_SUCCESS
