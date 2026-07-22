"""A retained crash record must keep BOTH startup callers out of integration.

``recover_incomplete_integration`` has two shapes of outcome. Either it
reconciled the interrupted operation and cleared the durable
``IntegrationRecord``, or it hit a transient failure -- a failed abort, a
failed reset, an unrefreshable target pointer, a fast-forward that raised
-- and deliberately LEFT that record on disk so the next startup can
retry it.

In the second shape recovery is not finished. Both production callers
(``run_loop._apply_startup_rebase_outcomes`` for the shared run loop and
``parallel.worker_runtime.run_worker_auto_integration`` for a
manifest-launched worker) used to run the startup catch-up
unconditionally afterwards. That catch-up reaches
``auto_integrate._integrate_once``, which writes a fresh
``IntegrationRecord(phase='integrating', ...)`` BEFORE it mutates git --
overwriting the retained record, and with it the pre-integration feature
SHA that a later recovery needs to restore the branch.

The gate is scoped to those two STARTUP seams. The in-run seams (commit,
phase boundary, the worker's post-phase boundary) keep integrating, so a
transient recovery fault cannot strand an agent off the shared mainline
for a whole run; recovery retries the retained record at the next
process startup.

These are fast unit tests: no real git, no subprocess, no sleeping. The
retention fact is read through the structured
``RebaseState.recovery_record_retained`` flag, never from the free-form
``last_reason`` display text.
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate import recovery_retained_record
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.agents.registry import AgentRegistry
    from ralph.display.context import DisplayContext


class _RecordingDisplay:
    """Display that records the operator warn lines pushed through it."""

    def __init__(self) -> None:
        self.warn_lines: list[str] = []

    def emit_warn_line(self, scope: str, channel: str, message: str) -> None:
        del scope, channel
        self.warn_lines.append(message)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"}}
    )


def _retained() -> RebaseState:
    """A recovery outcome that left the durable record on disk."""
    return RebaseState(
        last_action="skipped",
        last_reason=(
            "recovery: feature branch not restored, record retained for retry"
        ),
        last_target="main",
        recovery_record_retained=True,
    )


def _reconciled() -> RebaseState:
    """A recovery outcome that cleared the durable record."""
    return RebaseState(
        last_action="recovered",
        last_reason="restored feature branch after interrupted rebase",
        last_target="main",
    )


# --------------------------------------------------------------------
# The structured predicate, and every branch that must produce it.
# --------------------------------------------------------------------


def test_the_retention_predicate_reads_the_flag_not_the_reason_text() -> None:
    """Retention is a structured fact; ``last_reason`` is display text.

    A caller that pattern-matched the reason string would break the
    moment a branch reworded its message or interpolated an exception,
    so the predicate must key on the flag alone.
    """
    assert recovery_retained_record(_retained()) is True
    assert recovery_retained_record(_reconciled()) is False
    assert recovery_retained_record(None) is False
    assert (
        recovery_retained_record(
            RebaseState(
                last_action="skipped",
                last_reason="recovery: record retained for retry",
            )
        )
        is False
    ), "reason text alone must never make an outcome read as retained"


def _recovery_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.auto_integrate_recovery")


def _fake_record(phase: str, *, integrated_sha: str | None = None) -> object:
    """Durable-record stand-in exposing only the attributes recovery reads."""
    return SimpleNamespace(
        phase=phase,
        target="main",
        pre_feature_sha="a" * 40,
        integrated_feature_sha=integrated_sha,
        resolving_rebase=False,
    )


def _stub_clean_git(
    recovery: ModuleType, monkeypatch: MonkeyPatch, record: object
) -> None:
    """Fake every git collaborator into a quiet, no-op-in-flight state.

    Each branch test below then breaks exactly ONE of them, so the
    assertion is about that branch and nothing else. No real git, no
    subprocess: ``_clear_record`` is also stubbed, so a branch that
    wrongly cleared instead of retaining still reaches the assertion.
    """
    monkeypatch.setattr(recovery, "_read_record", lambda _root: record)
    monkeypatch.setattr(recovery, "_clear_record", lambda _root: None)
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(recovery, "abort_rebase", lambda **_kw: None)
    monkeypatch.setattr(
        recovery, "merge_state", lambda _root: recovery.MERGE_STATE_NONE
    )
    monkeypatch.setattr(recovery, "abort_merge", lambda _root: True)
    monkeypatch.setattr(recovery, "reset_hard", lambda _root, _sha: None)


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("simulated failure")


def test_a_failed_reset_retains_the_record_and_says_so_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """phase='integrating' whose ``reset_hard`` raised (branch 3 of 4)."""
    recovery = _recovery_module()
    _stub_clean_git(recovery, monkeypatch, _fake_record("integrating"))
    monkeypatch.setattr(recovery, "reset_hard", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"a reset_hard that raised retains the record; got {outcome!r}"
    )


def test_an_unprovable_abort_retains_the_record_and_says_so_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """phase='integrated' whose owned abort could not be proven (branch 4)."""
    recovery = _recovery_module()
    _stub_clean_git(
        recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40)
    )
    monkeypatch.setattr(recovery, "rebase_in_progress", lambda _root: True)
    monkeypatch.setattr(recovery, "abort_rebase", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"an unprovable abort retains the record; got {outcome!r}"
    )


def test_an_unrefreshable_pointer_retains_the_record_and_says_so_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """phase='integrated' whose target pointer would not refresh (branch 1).

    This is the branch that used to CLEAR the record permanently from a
    stale pointer, so it is the one that most needs to be detectable.
    """
    recovery = _recovery_module()
    _stub_clean_git(
        recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40)
    )
    monkeypatch.setattr(
        recovery,
        "_refresh_target",
        lambda _config, _root, _target: recovery.REFRESH_UNREACHABLE,
    )

    outcome = recovery.recover_incomplete_integration(
        WorkspaceScope(tmp_path), config=_config()
    )

    assert recovery_retained_record(outcome), (
        f"an unrefreshable pointer retains the record; got {outcome!r}"
    )


def test_a_transient_fast_forward_failure_retains_the_record_structurally(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """phase='integrated' whose fast-forward raised (branch 2 of 4).

    Deliberately distinguished from the PERMANENT 'advanced
    concurrently' refusal, which clears the record and must therefore
    NOT read as retained.
    """
    recovery = _recovery_module()
    _stub_clean_git(
        recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40)
    )
    monkeypatch.setattr(recovery, "branch_sha", lambda _root, _target: "c" * 40)
    monkeypatch.setattr(recovery, "is_ancestor", lambda _root, _target, _sha: True)
    monkeypatch.setattr(recovery, "fast_forward_target", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome), (
        f"a fast-forward that raised retains the record; got {outcome!r}"
    )


@pytest.mark.parametrize(
    ("ff_result", "why"),
    [
        ((True, ""), "a landed fast-forward clears the record"),
        (
            (False, "target advanced concurrently"),
            "a permanent refusal clears the record",
        ),
    ],
)
def test_a_cleared_record_never_reads_as_retained(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    ff_result: tuple[bool, str],
    why: str,
) -> None:
    """The flag must be false wherever recovery gave the record up.

    Without this the gate could over-trigger and suppress the startup
    catch-up on the two paths that genuinely finished.
    """
    recovery = _recovery_module()
    _stub_clean_git(
        recovery, monkeypatch, _fake_record("integrated", integrated_sha="b" * 40)
    )
    monkeypatch.setattr(recovery, "branch_sha", lambda _root, _target: "c" * 40)
    monkeypatch.setattr(recovery, "is_ancestor", lambda _root, _target, _sha: True)
    monkeypatch.setattr(
        recovery, "fast_forward_target", lambda _root, _target, _sha: ff_result
    )

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert recovery_retained_record(outcome) is False, f"{why}; got {outcome!r}"


def test_an_unexpected_recovery_crash_does_not_claim_the_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """The catch-all cannot honestly claim a record it may never have read.

    Marking it retained would gate the startup catch-up behind a fault
    with no bounded end, which is the opposite of recovery's fail-open
    contract.
    """
    recovery = _recovery_module()
    monkeypatch.setattr(recovery, "_read_record", _boom)

    outcome = recovery.recover_incomplete_integration(WorkspaceScope(tmp_path))

    assert outcome is not None
    assert recovery_retained_record(outcome) is False


# --------------------------------------------------------------------
# Caller 1: the shared run loop.
# --------------------------------------------------------------------


def _run_loop_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.run_loop")


def _install_run_loop_seams(
    module: ModuleType, monkeypatch: MonkeyPatch, recovered: RebaseState | None
) -> tuple[list[str], list[PipelineState]]:
    """Replace the startup seams with recorders; return (events, saves)."""
    events: list[str] = []
    saves: list[PipelineState] = []

    def _fake_recover(
        workspace_scope: object, config: object = None
    ) -> RebaseState | None:
        del workspace_scope, config
        events.append("recover")
        return recovered

    def _fake_startup(ctx: object) -> RebaseState | None:
        del ctx
        events.append("integrate")
        return RebaseState(last_action="fast_forwarded", last_target="main")

    def _fake_save(state: PipelineState, ctx: object) -> None:
        del ctx
        events.append("save")
        saves.append(state)

    monkeypatch.setattr(
        module, "_run_auto_integrate_recovery_preamble", _fake_recover
    )
    monkeypatch.setattr(module, "_run_startup_integration", _fake_startup)
    monkeypatch.setattr(module, "_save_recovered_rebase_checkpoint", _fake_save)
    return events, saves


def _loop_ctx(tmp_path: Path, display: object) -> object:
    """Minimal stand-in for the three ``_LoopContext`` slots this path reads."""
    return SimpleNamespace(
        workspace_scope=WorkspaceScope(tmp_path),
        config=_config(),
        active_display=display,
    )


def test_run_loop_does_not_integrate_over_a_retained_recovery_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """The startup catch-up must not overwrite a record recovery still owns."""
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, _retained())
    display = _RecordingDisplay()

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "integrate" not in events, (
        "startup integration writes its own durable record before mutating"
        f" git, so it must not run after a retained recovery; got {events!r}"
    )
    assert state.rebase.recovery_record_retained is True, (
        "the retained recovery outcome must survive into the checkpointed"
        " state so the next startup sees it"
    )


def test_run_loop_persists_and_announces_the_deferred_catch_up(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A deferral the operator cannot see reads as 'auto rebase does nothing'."""
    module = _run_loop_module()
    events, saves = _install_run_loop_seams(module, monkeypatch, _retained())
    display = _RecordingDisplay()

    module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "save" in events, "the retained outcome must still be checkpointed"
    assert saves and saves[0].rebase.recovery_record_retained is True
    assert any("deferred" in line for line in display.warn_lines), (
        f"expected an operator-visible deferral line, got {display.warn_lines!r}"
    )


def test_a_display_that_cannot_take_the_line_never_aborts_startup(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Presentation must not be able to raise into the startup path."""
    module = _run_loop_module()
    _events, _saves = _install_run_loop_seams(module, monkeypatch, _retained())

    class _HostileDisplay:
        def emit_warn_line(self, *_args: object) -> None:
            raise RuntimeError("simulated display failure")

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, _HostileDisplay())
    )

    assert state.rebase.recovery_record_retained is True


def test_run_loop_still_catches_up_after_a_reconciled_recovery(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Control: recovery that CLEARED the record must not block the catch-up.

    Without this, the fix above would trade one bug for a worse one --
    a fleet whose members never pick up each other's landings.
    """
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, _reconciled())
    display = _RecordingDisplay()

    state = module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, display)
    )

    assert "integrate" in events, (
        f"a reconciled recovery must still catch up, got {events!r}"
    )
    assert state.rebase.last_action == "fast_forwarded"
    assert display.warn_lines == []


def test_run_loop_still_catches_up_when_there_was_nothing_to_recover(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """The overwhelmingly common case: no durable record at all."""
    module = _run_loop_module()
    events, _saves = _install_run_loop_seams(module, monkeypatch, None)

    module._apply_startup_rebase_outcomes(
        PipelineState(phase="planning"), _loop_ctx(tmp_path, _RecordingDisplay())
    )

    assert events == ["recover", "integrate", "save"], (
        f"expected the ordinary startup sequence, got {events!r}"
    )


# --------------------------------------------------------------------
# Caller 2: the manifest-launched parallel worker.
# --------------------------------------------------------------------


def _worker_module() -> ModuleType:
    return importlib.import_module("ralph.pipeline.parallel.worker_runtime")


def _git_workspace(tmp_path: Path) -> Path:
    """Satisfy the seam's cheap ``.git`` stat guard without real git."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    return tmp_path


def _install_worker_seams(
    module: ModuleType, monkeypatch: MonkeyPatch, recovered: RebaseState | None
) -> list[str]:
    events: list[str] = []

    def _fake_recover(
        workspace_scope: object, *, config: object = None
    ) -> RebaseState | None:
        del workspace_scope, config
        events.append("recover")
        return recovered

    def _fake_integrate(*args: object, **kwargs: object) -> RebaseState | None:
        del args, kwargs
        events.append("integrate")
        return RebaseState(last_action="fast_forwarded", last_target="main")

    monkeypatch.setattr(
        module, "recover_incomplete_integration", _fake_recover, raising=False
    )
    monkeypatch.setattr(
        module, "auto_integrate_on_phase_transition", _fake_integrate, raising=False
    )
    return events


def _run_worker_seam(
    module: ModuleType,
    tmp_path: Path,
    *,
    recover_first: bool = True,
    display_context: DisplayContext | None = None,
) -> RebaseState | None:
    return module.run_worker_auto_integration(
        config=_config(),
        workspace_scope=WorkspaceScope(_git_workspace(tmp_path)),
        policy_bundle=None,
        registry=cast("AgentRegistry | None", None),
        pipeline_deps=None,
        display_context=display_context,
        recover_first=recover_first,
    )


def test_worker_does_not_integrate_over_a_retained_recovery_record(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A parallel worker faces the same overwrite as the shared run loop."""
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())

    outcome = _run_worker_seam(module, tmp_path)

    assert events == ["recover"], (
        "a worker must not integrate over a record recovery still owns;"
        f" got {events!r}"
    )
    assert outcome is not None
    assert outcome.recovery_record_retained is True, (
        "the retained recovery verdict must reach the worker's caller, not"
        " be swallowed as a silent no-op"
    )


def test_the_worker_deferral_reaches_a_real_operator_console(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Drive the REAL display resolution, not a stand-in.

    ``_emit_deferred_integration_line`` wraps ``resolve_active_display``
    and ``emit_integration_warn_line`` in ``suppress(Exception)``, so a
    wrong argument or a renamed method would fail silently in
    production and leave the worker deferral invisible -- the exact
    "auto rebase does nothing" symptom. Injecting a captured console
    into a real ``DisplayContext`` proves the line actually renders.
    """
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())
    buffer = io.StringIO()
    display_context = make_display_context(
        env={}, console=Console(file=buffer, width=200), force_width=200
    )

    outcome = _run_worker_seam(
        module, tmp_path, display_context=display_context
    )

    assert events == ["recover"]
    assert outcome is not None
    rendered = buffer.getvalue()
    assert "deferred" in rendered, (
        f"expected the deferral on the operator console, got {rendered!r}"
    )
    assert "auto-integrate" in rendered, (
        "the line must be attributed to the auto-integrate channel, got"
        f" {rendered!r}"
    )


def test_worker_still_integrates_after_a_reconciled_recovery(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Control: the cross-agent catch-up is the whole point of the seam."""
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _reconciled())

    outcome = _run_worker_seam(module, tmp_path)

    assert events == ["recover", "integrate"], (
        f"a reconciled recovery must still catch up, got {events!r}"
    )
    assert outcome is not None
    assert outcome.last_action == "fast_forwarded"


def test_the_worker_boundary_seam_is_unaffected(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """``recover_first=False`` is the per-phase boundary: no recovery, no gate.

    Deliberate scope: gating the in-run seams too would disable
    auto-integration for a whole run over a transient recovery fault and
    strand this worker off the shared mainline. Recovery retries the
    retained record at the next process startup instead.
    """
    module = _worker_module()
    events = _install_worker_seams(module, monkeypatch, _retained())

    _run_worker_seam(module, tmp_path, recover_first=False)

    assert events == ["integrate"], (
        f"the boundary seam must neither recover nor be gated, got {events!r}"
    )
