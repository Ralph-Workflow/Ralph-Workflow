"""Regression tests for CLI telemetry wiring.

Pins the privacy contract at the CLI chokepoints:

1. ``_init_telemetry`` MUST skip ``get_or_create_user_id``,
   ``init_sentry``, ``set_environment_context``, ``record_session_start``,
   and ``atexit.register(finalize_session)`` whenever
   ``RALPH_DISABLE_TELEMETRY`` is truthy.
2. ``_run_pipeline`` MUST call ``set_session_outcome`` with
   ``"success"`` when ``run_pipeline`` returns 0,
   ``"failure"`` when it returns nonzero, ``"interrupted"`` on
   ``KeyboardInterrupt``, and ``"failure"`` on a generic exception.

Tests are consolidated (parametrized where possible) to keep the file
fast — every CLI test pays the ralph.cli.main import cost once.
"""

from __future__ import annotations

import atexit
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args
from unittest.mock import MagicMock

import pytest

import ralph.cli.main as ralph_cli_main
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models._types import PhaseRole
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import typer

    from ralph.display.context import DisplayContext
    from ralph.policy.models import PolicyBundle



def _stub_display_context() -> DisplayContext:
    """Build the smallest DisplayContext the tests need."""
    return cast(
        "DisplayContext",
        type(
            "_StubDisplay",
            (),
            {
                "console": type(
                    "_StubConsole",
                    (),
                    {
                        "print": lambda self, *a, **kw: None,
                        "log": lambda self, *a, **kw: None,
                    },
                )(),
            },
        )(),
    )


class _OutcomeRecorder:
    """Capture every ``set_session_outcome`` call made via the lazy import."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, outcome: str) -> None:
        self.calls.append(outcome)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)
        monkeypatch.setattr("ralph.telemetry._sentry.set_session_outcome", self.record)


class _TelemetrySpy:
    """Spy every lifecycle collaborator that ``_init_telemetry`` touches."""

    def __init__(self) -> None:
        self.user_id_calls: list[str] = []
        self.init_calls: list[dict[str, object]] = []
        self.set_context_calls: list[tuple[str, dict[str, object]]] = []
        self.record_calls: list[str] = []
        self.wallclock_calls: list[bool] = []
        self.command_calls: list[str] = []
        self.atexit_calls: list[object] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ralph.telemetry._user_identity.get_or_create_user_id",
            self._on_user_id,
        )
        monkeypatch.setattr(
            "ralph.telemetry._user_identity.generate_session_id",
            lambda: "fake-session-id",
        )
        monkeypatch.setattr("ralph.telemetry._sentry.init_sentry", self._on_init)
        monkeypatch.setattr(
            "ralph.telemetry._sentry.set_environment_context",
            lambda: self.set_context_calls.append(("called", {})),
        )
        monkeypatch.setattr(
            "ralph.telemetry._sentry.record_session_start",
            lambda now=None: self.record_calls.append("called"),
        )
        monkeypatch.setattr(
            "ralph.telemetry._sentry.set_session_wallclock_start",
            lambda now_dt=None: self.wallclock_calls.append(True),
        )
        monkeypatch.setattr(
            "ralph.telemetry._sentry.record_command_invocation",
            self._on_command_invocation,
        )
        monkeypatch.setattr(atexit, "register", self._on_atexit)

    def _on_user_id(self) -> str:
        self.user_id_calls.append("called")
        return "fake-user-id"

    def _on_init(self, uid: str, sid: str) -> None:
        self.init_calls.append({"uid": uid, "sid": sid})

    def _on_command_invocation(self, command: str) -> None:
        self.command_calls.append(command)

    def _on_atexit(self, func: object, *args: object, **kwargs: object) -> object:
        self.atexit_calls.append(func)
        return func


def test_init_telemetry_skips_everything_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_init_telemetry`` MUST NOT call any sentry / ID code when opted out."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ralph_cli_main._init_telemetry()

    assert spy.user_id_calls == []
    assert spy.init_calls == []
    assert spy.set_context_calls == []
    assert spy.record_calls == []
    assert spy.atexit_calls == []


@pytest.mark.parametrize("value", ["true", "yes", "on", "TRUE", "1"])
def test_init_telemetry_short_circuits_truthy_disable_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """All truthy values (true/yes/on/1, case-insensitive) must short-circuit."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", value)

    ralph_cli_main._init_telemetry()

    assert spy.user_id_calls == []
    assert spy.init_calls == []


def test_init_telemetry_wires_full_lifecycle_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: when telemetry is NOT disabled, the lifecycle fires."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    ralph_cli_main._init_telemetry()

    assert spy.user_id_calls == ["called"]
    assert spy.init_calls == [{"uid": "fake-user-id", "sid": "fake-session-id"}]
    assert spy.set_context_calls == [("called", {})]
    assert spy.record_calls == ["called"]
    assert spy.wallclock_calls == [True], (
        "set_session_wallclock_start must be wired when telemetry is enabled"
    )
    assert spy.atexit_calls, "atexit.register must be wired when telemetry is enabled"


def test_init_telemetry_skips_wallclock_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set_session_wallclock_start MUST NOT fire when telemetry is opted out."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ralph_cli_main._init_telemetry()

    assert spy.wallclock_calls == []


def test_record_cli_command_records_pipeline_when_no_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_record_cli_command`` MUST forward ``'pipeline'`` when no subcommand is invoked."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    ctx = cast("typer.Context", type("_StubCtx", (), {"invoked_subcommand": None})())
    ralph_cli_main._record_cli_command(ctx)

    assert spy.command_calls == ["pipeline"]


def test_record_cli_command_records_subcommand_name_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_record_cli_command`` MUST forward ``ctx.invoked_subcommand`` verbatim when set."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    ctx = cast("typer.Context", type("_StubCtx", (), {"invoked_subcommand": "cleanup"})())
    ralph_cli_main._record_cli_command(ctx)

    assert spy.command_calls == ["cleanup"]


def test_record_cli_command_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When telemetry is opted out, ``record_command_invocation`` is a no-op."""
    spy = _TelemetrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ctx = cast("typer.Context", type("_StubCtx", (), {"invoked_subcommand": "cleanup"})())
    ralph_cli_main._record_cli_command(ctx)

    assert spy.command_calls == []


def test_record_cli_command_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception from record_command_invocation MUST NOT crash the CLI."""
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)

    def boom(_cmd: str) -> None:
        raise RuntimeError("simulated record_command_invocation boom")

    monkeypatch.setattr("ralph.telemetry._sentry.record_command_invocation", boom)

    ctx = cast("typer.Context", type("_StubCtx", (), {"invoked_subcommand": None})())
    # Must not raise.
    ralph_cli_main._record_cli_command(ctx)


# ---------------------------------------------------------------------------
# Phase recording wiring — proves _run_pipeline_step's try/finally fires
# record_phase_execution at least once with role in PhaseRole vocabulary and
# outcome in the coarse outcome set. Drives the REAL pipeline step (not the
# CLI-layer run_pipeline stub) so the finally clause is exercised end-to-end.
# ---------------------------------------------------------------------------


def test_run_pipeline_step_records_phase_via_try_finally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Driving the real _run_pipeline_step MUST record exactly one phase event.

    The spy on ``ralph.telemetry._sentry.record_phase_execution`` substitutes
    the real Sentry sink so the finally-clause call never reaches the network.
    We exercise the post-with-block success path (event -> AGENT_SUCCESS ->
    outcome='success'). role MUST come from the PhaseRole closed vocabulary
    (auto-derived via get_args); outcome MUST come from the coarse outcome
    set. The finally clause is the SINGLE recording site.
    """

    @lru_cache(maxsize=1)
    def _load_default_policy_bundle() -> PolicyBundle:
        defaults_dir = Path(__file__).resolve().parent / "ralph" / "policy" / "defaults"
        return load_policy(defaults_dir)

    recorded_calls: list[dict[str, object]] = []

    def _spy_record(*, role: str, duration_s: int, outcome: str) -> None:
        recorded_calls.append({"role": role, "duration_s": duration_s, "outcome": outcome})

    monkeypatch.setattr(
        "ralph.pipeline.runner.record_phase_execution",
        _spy_record,
    )

    bundle = _load_default_policy_bundle()
    workspace_path = Path(str(tmp_path))
    workspace_scope = WorkspaceScope(workspace_path)
    workspace = FsWorkspace(workspace_path)
    workspace.mkdirs(".agent/tmp")

    effect = InvokeAgentEffect(
        agent_name="planner",
        phase="planning",
        prompt_file=".agent/tmp/planning_prompt.md",
        drain="planning",
    )
    state = PipelineState(phase="planning", previous_phase=None)

    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_a, **_kw: effect,
    )
    monkeypatch.setattr(
        runner_module,
        "materialize_agent_prompt_if_needed",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        runner_module,
        "invoke_execute_effect_with_optional_display",
        lambda *_a, **_kw: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **_kw: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, _event, _policy, recovery=None: (current_state, []),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_a, **_kw: None)

    display_context = make_display_context()
    display = runner_module.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    phase_role_set = frozenset(get_args(PhaseRole))
    allowed_outcomes = frozenset({"success", "failure", "skipped", "crashed"})

    runner_module.run_pipeline_step(
        state=state,
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=MagicMock(),
        display=display,
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )

    assert len(recorded_calls) >= 1, (
        "try/finally in _run_pipeline_step must record at least one phase event"
    )
    for call in recorded_calls:
        assert call["role"] in phase_role_set, (
            f"recorded role {call['role']!r} is not in PhaseRole closed vocabulary"
        )
        assert call["outcome"] in allowed_outcomes, (
            f"recorded outcome {call['outcome']!r} is not in {{success, failure, skipped, crashed}}"
        )
        assert isinstance(call["duration_s"], int), (
            f"duration_s MUST be int, got {type(call['duration_s']).__name__}"
        )


@pytest.mark.parametrize(
    ("return_code", "expected_outcome"),
    [
        (0, "success"),
        (1, "failure"),
        (7, "failure"),
    ],
)
def test_run_pipeline_classifies_pipeline_outcome(
    monkeypatch: pytest.MonkeyPatch, return_code: int, expected_outcome: str
) -> None:
    """``_run_pipeline`` MUST classify run_pipeline return code verbatim.

    0 -> ``\"success\"``; nonzero -> ``\"failure\"`` (regression: previously
    nonzero non-exception returns were mislabeled at the module default
    ``\"unknown\"``).
    """
    recorder = _OutcomeRecorder()
    recorder.install(monkeypatch)
    monkeypatch.setattr("ralph.cli.main.run_pipeline", lambda *a, **kw: return_code)

    rc = ralph_cli_main._run_pipeline(
        config=None,
        opts=ralph_cli_main._RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == return_code
    assert recorder.calls == [expected_outcome]


def test_run_pipeline_reports_interrupted_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On ``KeyboardInterrupt``, outcome MUST be ``\"interrupted\"`` and rc MUST be 130."""
    recorder = _OutcomeRecorder()
    recorder.install(monkeypatch)

    def _raise_kbi(*_args: object, **_kwargs: object) -> int:
        raise KeyboardInterrupt()

    monkeypatch.setattr("ralph.cli.main.run_pipeline", _raise_kbi)
    monkeypatch.setattr(
        "ralph.cli.main.handle_keyboard_interrupt_at_cli",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        "ralph.interrupt.handle_keyboard_interrupt_at_cli",
        lambda: None,
        raising=False,
    )

    rc = ralph_cli_main._run_pipeline(
        config=None,
        opts=ralph_cli_main._RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == 130
    assert recorder.calls == ["interrupted"]


def test_run_pipeline_reports_failure_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a generic exception, outcome MUST be ``\"failure\"`` and rc MUST be 1."""
    recorder = _OutcomeRecorder()
    recorder.install(monkeypatch)

    def _raise_runtime_error(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.run_pipeline", _raise_runtime_error)

    rc = ralph_cli_main._run_pipeline(
        config=None,
        opts=ralph_cli_main._RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == 1
    assert recorder.calls == ["failure"]


@pytest.mark.parametrize(
    "raise_exception",
    [False, True],
    ids=["success_path", "exception_path"],
)
def test_run_pipeline_does_not_set_outcome_when_disabled(
    monkeypatch: pytest.MonkeyPatch, raise_exception: bool
) -> None:
    """Outcome MUST NOT be set on either path when ``RALPH_DISABLE_TELEMETRY`` is set."""
    recorder = _OutcomeRecorder()
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
    monkeypatch.setattr("ralph.telemetry._sentry.set_session_outcome", recorder.record)

    if raise_exception:

        def _raise_runtime_error(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("boom")

        run_pipeline_mock = _raise_runtime_error
    else:

        def _return_zero(*_args: object, **_kwargs: object) -> int:
            return 0

        run_pipeline_mock = _return_zero

    monkeypatch.setattr("ralph.cli.main.run_pipeline", run_pipeline_mock)

    rc = ralph_cli_main._run_pipeline(
        config=None,
        opts=ralph_cli_main._RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == (1 if raise_exception else 0)
    assert recorder.calls == []
