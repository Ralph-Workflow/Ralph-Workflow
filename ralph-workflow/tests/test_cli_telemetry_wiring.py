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
from typing import TYPE_CHECKING, cast

import pytest

import ralph.cli.main as ralph_cli_main

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext


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
        monkeypatch.setattr(atexit, "register", self._on_atexit)

    def _on_user_id(self) -> str:
        self.user_id_calls.append("called")
        return "fake-user-id"

    def _on_init(self, uid: str, sid: str) -> None:
        self.init_calls.append({"uid": uid, "sid": sid})

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
    assert spy.atexit_calls, "atexit.register must be wired when telemetry is enabled"


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
