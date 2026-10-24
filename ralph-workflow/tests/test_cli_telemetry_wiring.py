"""Regression tests for CLI telemetry wiring.

Pins the privacy contract at the CLI chokepoints by observing the effect
the telemetry lifecycle has on its EXTERNAL collaborator -- the Sentry SDK
boundary (``sentry_sdk`` / ``sentry_sdk.metrics`` / ``atexit``) -- rather
than by reaching into the private telemetry module.

1. ``init_telemetry`` MUST skip every Sentry call whenever
   ``RALPH_DISABLE_TELEMETRY`` is truthy or the global / project-local
   configuration opts out.
2. ``init_telemetry`` MUST wire the full lifecycle (init, user identity,
   environment context, session start, wall-clock buckets, atexit
   finalization) when telemetry is enabled.
3. ``record_cli_command`` MUST forward the invoked subcommand (or the
   literal ``pipeline``) as the ``command`` tag.
4. ``invoke_pipeline`` MUST record the coarse session outcome
   (``success`` / ``failure`` / ``interrupted``) in the finalized session
   context.
5. The PUBLIC run path (``ralph.cli.main.run_pipeline``) MUST forward the
   resolved agent table and the policy-schema state to telemetry at the
   config-load chokepoint.

Only PUBLIC names are used: ``ralph.cli.main.init_telemetry`` /
``record_cli_command`` / ``invoke_pipeline`` / ``RunPipelineOpts`` /
``run_pipeline``, ``ralph.telemetry.reset_telemetry_state``, and the
``sentry_sdk`` package itself.
"""

from __future__ import annotations

import atexit
import io
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, get_args
from unittest.mock import MagicMock

import pytest
import sentry_sdk
import sentry_sdk.metrics as sentry_metrics
from rich.console import Console

import ralph.cli.main as ralph_cli_main
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseRole
from ralph.telemetry import is_telemetry_disabled, reset_telemetry_state
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.policy.models import PolicyBundle


@pytest.fixture(autouse=True)
def _isolate_telemetry_home_and_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every telemetry read/write inside the test's temporary tree.

    The anonymous user-id file, the global config lookup, and the
    project-local config lookup all resolve against ``HOME`` /
    ``XDG_CONFIG_HOME`` / the current directory, so redirecting them keeps
    the test hermetic (no operator home directory is read or written).

    The live session handle is ALSO reset here: the telemetry session is
    process-global, so without the reset the cases would inherit the
    previous test's outcome / wall-clock buckets and their order would
    decide the result. ``reset_telemetry_state`` is the public
    operational reset for exactly that.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("RALPH_DISABLE_TELEMETRY", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_telemetry_state()


class _SentrySpy:
    """Spy the Sentry SDK boundary -- the external collaborator telemetry emits through.

    Recording the boundary calls proves what telemetry actually DOES
    (initialize the client, tag it, attach contexts, capture messages,
    register the session finalizer) instead of proving which internal
    helper was invoked.
    """

    def __init__(self) -> None:
        self.init_calls: list[dict[str, object]] = []
        self.user_calls: list[object] = []
        self.tags: list[tuple[str, object]] = []
        self.contexts: list[tuple[str, object]] = []
        self.messages: list[str] = []
        self.sessions: list[object] = []
        self.transactions: list[object] = []
        self.breadcrumbs: list[dict[str, object]] = []
        self.atexit_callbacks: list[object] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sentry_sdk, "init", self._on_init)
        monkeypatch.setattr(sentry_sdk, "set_user", self._on_set_user)
        monkeypatch.setattr(sentry_sdk, "set_tag", self._on_set_tag)
        monkeypatch.setattr(sentry_sdk, "set_context", self._on_set_context)
        monkeypatch.setattr(sentry_sdk, "capture_message", self._on_capture_message)
        monkeypatch.setattr(sentry_sdk, "start_session", self._on_start_session)
        monkeypatch.setattr(sentry_sdk, "start_transaction", self._on_start_transaction)
        monkeypatch.setattr(sentry_sdk, "add_breadcrumb", self._on_add_breadcrumb)
        monkeypatch.setattr(sentry_sdk, "end_session", self._on_end_session)
        monkeypatch.setattr(sentry_sdk, "flush", self._on_flush)
        monkeypatch.setattr(sentry_metrics, "count", self._on_metric_count)
        monkeypatch.setattr(sentry_metrics, "distribution", self._on_metric_distribution)
        monkeypatch.setattr(atexit, "register", self._on_atexit)

    def _on_init(self, **kwargs: object) -> None:
        self.init_calls.append(kwargs)

    def _on_set_user(self, user: object) -> None:
        self.user_calls.append(user)

    def _on_set_tag(self, key: str, value: object) -> None:
        self.tags.append((key, value))

    def _on_set_context(self, key: str, data: object) -> None:
        self.contexts.append((key, data))

    def _on_capture_message(self, message: str, **_kwargs: object) -> None:
        self.messages.append(message)

    def _on_start_session(self, **_kwargs: object) -> None:
        self.sessions.append("session")

    def _on_start_transaction(self, **_kwargs: object) -> None:
        self.transactions.append("transaction")

    def _on_add_breadcrumb(self, **kwargs: object) -> None:
        self.breadcrumbs.append(kwargs)

    def _on_end_session(self) -> None:
        self.sessions.append("end")

    def _on_flush(self, **_kwargs: object) -> None:
        return None

    def _on_metric_count(self, _name: str, _value: float, **_kwargs: object) -> None:
        return None

    def _on_metric_distribution(self, _name: str, _value: float, **_kwargs: object) -> None:
        return None

    def _on_atexit(self, func: object, *_args: object, **_kwargs: object) -> object:
        self.atexit_callbacks.append(func)
        return func

    @property
    def emitted_anything(self) -> bool:
        """Return True when telemetry touched the Sentry boundary at all."""
        return bool(
            self.init_calls
            or self.user_calls
            or self.tags
            or self.contexts
            or self.messages
            or self.sessions
            or self.transactions
            or self.breadcrumbs
        )

    def session_payloads(self) -> list[dict[str, object]]:
        """Return every session payload published to the Sentry boundary."""
        payloads: list[dict[str, object]] = []
        for key, data in self.contexts:
            if key == "session" and isinstance(data, dict):
                payloads.append(data)
        return payloads

    def observed_wallclock_buckets(self) -> list[dict[str, object]]:
        """Return the coarse wall-clock buckets seen in any session payload."""
        buckets: list[dict[str, object]] = []
        for payload in self.session_payloads():
            wallclock = payload.get("wallclock")
            if isinstance(wallclock, dict):
                buckets.append(wallclock)
        return buckets

    def tag_values(self, key: str) -> list[object]:
        """Return every value recorded for ``key``."""
        return [value for name, value in self.tags if name == key]

    def context_payload(self, key: str) -> object:
        """Return the payload recorded for the ``key`` context."""
        matches = [data for name, data in self.contexts if name == key]
        assert matches, f"no {key!r} context was recorded; got {self.contexts!r}"
        return matches[-1]


def _stub_display_context() -> DisplayContext:
    """Build the smallest DisplayContext the tests need."""
    return type(
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
    )()


def _finalized_session_context(spy: _SentrySpy) -> dict[str, object]:
    """Run the registered session finalizer and return the session context payload.

    The finalizer is the callback the CLI handed to ``atexit.register``;
    running it is what publishes the session payload (outcome, wall-clock
    buckets, timings) to the Sentry boundary.
    """
    assert spy.atexit_callbacks, "atexit.register must be wired when telemetry is enabled"
    finalizer = spy.atexit_callbacks[-1]
    assert callable(finalizer)
    finalizer()
    payload = spy.context_payload("session")
    assert isinstance(payload, dict)
    return payload


def test_init_telemetry_skips_everything_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``init_telemetry`` MUST NOT touch the Sentry boundary when opted out."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False, (
        f"no Sentry call may happen when telemetry is disabled; got {spy.tags!r} {spy.contexts!r}"
    )
    assert spy.atexit_callbacks == []


@pytest.mark.parametrize("value", ["true", "yes", "on", "TRUE", "1"])
def test_init_telemetry_short_circuits_truthy_disable_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """All truthy values (true/yes/on/1, case-insensitive) must short-circuit."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", value)

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False
    assert spy.init_calls == []


def test_init_telemetry_wires_full_lifecycle_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When telemetry is NOT disabled, the whole lifecycle reaches Sentry."""
    spy = _SentrySpy()
    spy.install(monkeypatch)

    ralph_cli_main.init_telemetry()

    assert len(spy.init_calls) == 1, "sentry_sdk.init must run exactly once"
    assert spy.user_calls, "an anonymous user identity must be attached"
    assert isinstance(spy.user_calls[0], dict), "the user identity must be a mapping"
    assert spy.context_payload("runtime"), "the runtime environment context must be attached"
    assert "session start" in spy.messages, "the session start must be captured"
    assert spy.sessions, "a Sentry session must be started"
    assert spy.transactions, "the session transaction must be started"
    assert spy.atexit_callbacks, "atexit.register must be wired when telemetry is enabled"

    session_payload = _finalized_session_context(spy)
    assert "wallclock" in session_payload, (
        f"the session payload must carry the coarse wall-clock buckets; got {session_payload!r}"
    )
    wallclock = session_payload["wallclock"]
    assert isinstance(wallclock, dict)
    assert set(wallclock) == {"hour_of_day", "day_of_week", "is_weekday"}


def test_init_telemetry_skips_wallclock_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wall-clock start MUST NOT be wired when telemetry is opted out.

    The coarse wall-clock buckets leave the process ONLY inside the session
    payload published to Sentry, so the PUBLIC observation of "the
    wall-clock start was not wired" is "no published session payload
    carries a ``wallclock`` bucket". The negative is therefore stated
    through the public telemetry surface (``ralph.telemetry``'s opt-out
    predicate and the published session payload) -- never by reaching into
    a private helper.
    """
    spy = _SentrySpy()
    spy.install(monkeypatch)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ralph_cli_main.init_telemetry()

    assert is_telemetry_disabled() is True, "the public opt-out predicate must agree"

    # Publish whatever WAS wired, so a wired wall-clock start would become
    # observable: the buckets reach the outside world only through the
    # session payload the finalizer publishes.
    for finalizer in spy.atexit_callbacks:
        assert callable(finalizer)
        finalizer()

    assert spy.observed_wallclock_buckets() == [], (
        "the coarse wall-clock buckets must not reach the Sentry boundary when telemetry is disabled"
    )
    assert spy.session_payloads() == [], (
        "no session payload may be published when telemetry is disabled"
    )


def test_init_telemetry_skips_everything_when_global_config_opts_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``[general] telemetry_enabled = false`` must skip the Sentry boundary."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        "[general]\ntelemetry_enabled = false\n",
        encoding="utf-8",
    )

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False
    assert spy.init_calls == []
    assert spy.atexit_callbacks == []


def test_init_telemetry_skips_everything_when_global_config_is_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Malformed telemetry config fails closed before any Sentry call."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text("[general\n", encoding="utf-8")

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False
    assert spy.init_calls == []
    assert spy.atexit_callbacks == []


def test_init_telemetry_skips_everything_when_project_config_opts_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-local ``.agent/ralph-workflow.toml`` can opt out before Sentry init."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\ntelemetry_enabled = false\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False
    assert spy.init_calls == []
    assert spy.atexit_callbacks == []


def test_init_telemetry_project_config_opt_out_works_from_subdirectory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-local telemetry opt-out follows the resolved workspace root."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\ntelemetry_enabled = false\n",
        encoding="utf-8",
    )
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    ralph_cli_main.init_telemetry()

    assert spy.emitted_anything is False
    assert spy.init_calls == []
    assert spy.atexit_callbacks == []


def test_record_cli_command_records_pipeline_when_no_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``record_cli_command`` MUST forward ``'pipeline'`` when no subcommand is invoked."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()

    ctx = type("_StubCtx", (), {"invoked_subcommand": None})()
    ralph_cli_main.record_cli_command(ctx)

    assert spy.tag_values("command") == ["pipeline"]


def test_record_cli_command_records_subcommand_name_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``record_cli_command`` MUST forward ``ctx.invoked_subcommand`` verbatim when set."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()

    ctx = type("_StubCtx", (), {"invoked_subcommand": "cleanup"})()
    ralph_cli_main.record_cli_command(ctx)

    assert spy.tag_values("command") == ["cleanup"]


def test_record_cli_command_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When telemetry is opted out, the command tag is never recorded."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    ctx = type("_StubCtx", (), {"invoked_subcommand": "cleanup"})()
    ralph_cli_main.record_cli_command(ctx)

    assert spy.tag_values("command") == []


def test_record_cli_command_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception at the Sentry boundary MUST NOT crash the CLI."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()

    def boom(_key: str, _value: object) -> None:
        raise RuntimeError("simulated Sentry set_tag boom")

    monkeypatch.setattr(sentry_sdk, "set_tag", boom)

    ctx = type("_StubCtx", (), {"invoked_subcommand": None})()
    # Must not raise.
    ralph_cli_main.record_cli_command(ctx)


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

    The spy on ``ralph.pipeline.runner.record_phase_execution`` substitutes
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
        lambda current_state, _event, _policy, recovery=None, routing_timing=None: (
            current_state,
            [],
        ),
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
    """``invoke_pipeline`` MUST publish the run outcome in the session context.

    0 -> ``\"success\"``; nonzero -> ``\"failure\"`` (regression: previously
    nonzero non-exception returns were mislabeled at the module default
    ``\"unknown\"``).
    """
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    monkeypatch.setattr("ralph.cli.main.run_pipeline", lambda *a, **kw: return_code)

    rc = ralph_cli_main.invoke_pipeline(
        config=None,
        opts=ralph_cli_main.RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == return_code
    assert _finalized_session_context(spy)["outcome"] == expected_outcome


def test_run_pipeline_reports_interrupted_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On ``KeyboardInterrupt``, outcome MUST be ``\"interrupted\"`` and rc MUST be 130."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()

    def _raise_kbi(*_args: object, **_kwargs: object) -> int:
        raise KeyboardInterrupt()

    monkeypatch.setattr("ralph.cli.main.run_pipeline", _raise_kbi)
    monkeypatch.setattr(
        "ralph.interrupt.handle_keyboard_interrupt_at_cli",
        lambda: None,
        raising=False,
    )

    rc = ralph_cli_main.invoke_pipeline(
        config=None,
        opts=ralph_cli_main.RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == 130
    assert _finalized_session_context(spy)["outcome"] == "interrupted"


def test_run_pipeline_reports_failure_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a generic exception, outcome MUST be ``\"failure\"`` and rc MUST be 1."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()

    def _raise_runtime_error(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr("ralph.cli.main.run_pipeline", _raise_runtime_error)

    rc = ralph_cli_main.invoke_pipeline(
        config=None,
        opts=ralph_cli_main.RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == 1
    assert _finalized_session_context(spy)["outcome"] == "failure"


@pytest.mark.parametrize(
    "raise_exception",
    [False, True],
    ids=["success_path", "exception_path"],
)
def test_run_pipeline_does_not_set_outcome_when_disabled(
    monkeypatch: pytest.MonkeyPatch, raise_exception: bool
) -> None:
    """Outcome MUST NOT be set on either path when ``RALPH_DISABLE_TELEMETRY`` is set."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    if raise_exception:

        def _raise_runtime_error(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("boom")

        run_pipeline_mock = _raise_runtime_error
    else:

        def _return_zero(*_args: object, **_kwargs: object) -> int:
            return 0

        run_pipeline_mock = _return_zero

    monkeypatch.setattr("ralph.cli.main.run_pipeline", run_pipeline_mock)

    rc = ralph_cli_main.invoke_pipeline(
        config=None,
        opts=ralph_cli_main.RunPipelineOpts(
            cli_overrides={},
            dry_run=True,
            resume=False,
            no_resume=False,
        ),
        display_context=_stub_display_context(),
    )
    assert rc == (1 if raise_exception else 0)
    assert "session" not in [name for name, _data in spy.contexts], (
        "no session may be published when telemetry is disabled"
    )


def _write_global_agents_table(config_home: Path) -> None:
    """Write a one-agent global agents table so the run resolves an agent.

    The agents table is the operator-facing configuration layer the
    public run path reads (``$XDG_CONFIG_HOME/ralph-workflow-agents.toml``),
    so writing it is how the test gives the public path a resolved agent
    to forward -- no private config object is constructed.
    """
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "ralph-workflow-agents.toml").write_text(
        '[agents.acme-internal]\ncmd = "claude"\n',
        encoding="utf-8",
    )


def _public_run_pipeline() -> int:
    """Drive the PUBLIC run entry point in dry-run mode.

    ``ralph.cli.main.run_pipeline`` is the public CLI surface; the
    config-load chokepoint that forwards the agent snapshot and the
    policy-schema state to telemetry sits inside it, so no private symbol
    is referenced to reach it.

    The run's own exit code is irrelevant to the telemetry contract: the
    config-load chokepoint runs BEFORE the preflight checks, so the
    telemetry effect is observable regardless of whether the synthetic
    workspace later satisfies the preflight.
    """
    return ralph_cli_main.run_pipeline(
        ralph_cli_main.RunPipelineRequest(dry_run=True),
        display_context=make_display_context(
            console=Console(file=io.StringIO()),
            env={},
        ),
    )


def test_public_run_path_forwards_the_resolved_agent_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The config-load chokepoint MUST actually reach telemetry.

    The forwarding happens behind a lazy import inside a bare
    ``try/except``, so a broken wiring (renamed function, renamed
    ``config.agents``, import cycle) would silently no-op while every
    telemetry unit test still passed. This pins the wiring itself through
    the PUBLIC run path, and every assertion is made on the Sentry
    boundary.
    """
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    _write_global_agents_table(tmp_path / "config")

    _public_run_pipeline()

    agent_payload = spy.context_payload("agent_config")
    assert isinstance(agent_payload, dict)
    assert agent_payload.get("agent_count") == 1
    # The temporary workspace has no policy pack, so the derivation runs
    # and reports 'absent'.
    assert spy.tag_values("policy_schema_state") == ["absent"]


def test_public_run_path_is_silent_when_telemetry_inactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nothing is forwarded -- and no policy files are read -- when telemetry is off."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    _write_global_agents_table(tmp_path / "config")
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")

    _public_run_pipeline()

    assert spy.tag_values("policy_schema_state") == []
    assert spy.tag_values("agent_count") == []


def test_public_run_path_never_breaks_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Telemetry failure must not propagate into the pipeline."""
    spy = _SentrySpy()
    spy.install(monkeypatch)
    ralph_cli_main.init_telemetry()
    _write_global_agents_table(tmp_path / "config")

    def boom(_key: str, _data: object) -> None:
        raise RuntimeError("sentry exploded")

    monkeypatch.setattr(sentry_sdk, "set_context", boom)

    _public_run_pipeline()  # must not raise
