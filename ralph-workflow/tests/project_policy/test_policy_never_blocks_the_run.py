"""Policy failure is SEPARATE from development work. It never aborts the run.

This is the single most important behavioral contract in the project-policy
subsystem, so it gets its own suite.

Project policy is documentation ABOUT a project. It is not a precondition for
working ON that project. Before this contract existed, a stale RALPH-LANG block
for a language nobody used would return exit code 2 from the startup preflight
and kill the run outright -- no planning, no development, nothing. A user's whole
session, lost to a documentation nit.

The rule now: NO policy outcome may produce a non-zero exit from a normal run.
Not a blocked policy, not an exhausted budget, not a missing agent, not a crashed
agent, and -- crucially -- not a BUG in the policy code itself. Whatever happens,
the run proceeds to planning as if the policy pipeline had never existed.

The sole exception is the ``_ONLY`` modes, which have no development run to
proceed to, and so must report failure through their exit code.
"""

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

# Reached through the module rather than a `from ... import _Name`: tests may not
# import private names out of ralph (audit_repo_structure), but the CLI's exit
# codes and load-result shape are exactly what this suite is asserting about.
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
    """Drive the real CLI entry point with injected seams."""
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
    """The reported bug: findings remain, so the preflight returned exit 2 and
    the run died before planning. It must exit 0 and carry on."""
    exit_code = _run(MemoryWorkspace(), invoke=_agent_that_never_fixes_anything)

    assert exit_code == _EXIT_SUCCESS


def test_an_agent_that_cannot_be_launched_still_exits_zero() -> None:
    """A broken agent subprocess is infrastructure breakage, not a policy
    shortfall. It must not cost the user their run."""

    def invoke(_ws: Workspace) -> InvokePolicyAgent:
        def _raise(*, phase: str, prompt_path: str) -> bool:
            del phase, prompt_path
            raise RemediationInvocationError("agent binary not found")

        return _raise

    assert _run(MemoryWorkspace(), invoke=invoke) == _EXIT_SUCCESS


def test_a_missing_agent_chain_still_exits_zero() -> None:
    """No policy_remediation chain configured at all: warn, proceed."""
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
    """THE CRUCIAL ONE. Even if the policy pipeline crashes outright -- a real
    bug, not a handled condition -- the run continues to the normal pipeline like
    nothing ever happened.

    Simulated by making the deterministic validator explode, which is on the path
    of every single policy run.
    """

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
    """The fault handler must survive a broken display -- a crash in the crash
    handler would defeat the entire point."""

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


def test_keyboard_interrupt_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fault boundary catches Exception, deliberately NOT BaseException.
    Ctrl-C must still stop the program."""

    def interrupt(*_args: object, **_kwargs: object) -> list[object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(validators, "validate_readiness", interrupt)
    monkeypatch.setattr(preflight.validators, "validate_readiness", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _run(MemoryWorkspace())


@pytest.mark.parametrize(
    "mode", [PolicyMode.REDO_ONLY, PolicyMode.RUN_AGENTS_ONLY], ids=str
)
def test_only_modes_do_report_failure_through_their_exit_code(
    mode: PolicyMode,
) -> None:
    """The one exception. An --*-only invocation has no development run to
    proceed to, so its exit code is the only signal it can give a CI job."""
    exit_code = _run(
        MemoryWorkspace(), mode=mode, invoke=_agent_that_never_fixes_anything
    )

    assert exit_code == _EXIT_PREFLIGHT


@pytest.mark.parametrize(
    "mode",
    [PolicyMode.NORMAL, PolicyMode.REDO, PolicyMode.RUN_AGENTS],
    ids=str,
)
def test_no_continuing_mode_can_ever_return_nonzero(mode: PolicyMode) -> None:
    """Every mode that continues into the development run exits 0 on a policy
    failure. This is the invariant, stated once over every such mode."""
    exit_code = _run(
        MemoryWorkspace(), mode=mode, invoke=_agent_that_never_fixes_anything
    )

    assert exit_code == _EXIT_SUCCESS
