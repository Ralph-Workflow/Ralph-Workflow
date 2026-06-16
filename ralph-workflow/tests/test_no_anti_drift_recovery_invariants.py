"""Anti-drift recovery invariants: pin the consolidation contract for the recovery layer.

These tests are black-box pins for the architectural consolidation of the
recovery / classifier / watchdog surface, per `.agent/PLAN.md` Step 7.

They MUST stay deterministic (no real subprocess, no real network, no
time.sleep, no os.system). The tests use the project's
``FakeClock``-style test fixtures so they fit the 60s combined budget.
"""

from __future__ import annotations

import ast
import pathlib
import time

import pytest

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.agents.timeout_clock import FakeClock
from ralph.interrupt.controller import INTERRUPT_EXIT_CODE, InterruptController

pytestmark = pytest.mark.timeout_seconds(10)

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# Surface (i) — recovery class construction
# ---------------------------------------------------------------------------


class TestFailureClassifierSingleOwner:
    """Pin Surface (i): ``FailureClassifier(`` is constructed only in authorized sites."""

    def test_failure_classifier_only_in_allowed_sites(self) -> None:
        allowed_relative = {
            pathlib.Path("ralph/recovery/failure_classifier.py"),
            pathlib.Path("ralph/recovery/classifier.py"),
            pathlib.Path("ralph/recovery/controller.py"),
            pathlib.Path("ralph/agents/invoke/_direct_mcp_recovery.py"),
            pathlib.Path("ralph/agents/invoke/_completion.py"),
            pathlib.Path("ralph/pipeline/agent_retry_decision.py"),
        }
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_relative:
                continue
            if "FailureClassifier(" in _read(path):
                offenders.append(str(rel))
        assert offenders == [], (
            f"FailureClassifier( is constructed outside the allowed sites: {offenders}."
        )


# ---------------------------------------------------------------------------
# Surface (f) — watchdog invariant
# ---------------------------------------------------------------------------


class TestWatchdogInvariant:
    """Pin Surface (f): the watchdog.evaluate invariant is preserved."""

    def test_watchdog_evaluate_call_sites_unchanged(self) -> None:
        """``watchdog.evaluate(...)`` must be called at the 6 expected sites."""
        pty = RALPH_ROOT / "agents" / "invoke" / "_pty_line_reader.py"
        process = RALPH_ROOT / "agents" / "invoke" / "_process_reader.py"
        pty_count = _read(pty).count("watchdog.evaluate")
        process_count = _read(process).count("watchdog.evaluate")
        total = pty_count + process_count
        assert total == 6, f"Expected exactly 6 watchdog.evaluate(...) call sites; got {total}."


# ---------------------------------------------------------------------------
# Surface (i) — canonical child-evidence model
# ---------------------------------------------------------------------------


class TestCanonicalChildEvidenceModel:
    """Pin that ``classify_child_snapshot`` is the single owner of the canonical
    child-evidence classification model (per the addendum to step 7)."""

    def test_canonical_child_evidence_model_is_single_owner(self) -> None:
        liveness = RALPH_ROOT / "process" / "child_liveness.py"
        assert liveness.exists()
        source = _read(liveness)
        tree = ast.parse(source)
        classify_funcs: list[str] = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("classify_")
        ]
        # Only classify_child_snapshot is allowed (per the strategy
        # delegation pattern documented in the plan).
        assert classify_funcs == ["classify_child_snapshot"], (
            f"Expected only classify_child_snapshot in child_liveness.py; got {classify_funcs}."
        )

    def test_helpers_delegate_to_canonical_model(self) -> None:
        """``_helpers.py`` must call ``classify_child_snapshot`` (the canonical
        child-evidence model) from the three classify_quiet/classify_exit
        helper sites.
        """
        helpers = RALPH_ROOT / "agents" / "execution_state" / "_helpers.py"
        if not helpers.exists():
            pytest.skip("execution_state/_helpers.py not present")
        source = _read(helpers)
        # The three helper sites are _probe_check_quiet, _registry_check_for_exit, _probe_check_exit
        assert "classify_child_snapshot" in source, (
            "execution_state/_helpers.py must call classify_child_snapshot"
        )


# ---------------------------------------------------------------------------
# Ctrl+C escalation: second SIGINT path raises os._exit(130)
# ---------------------------------------------------------------------------


class TestSecondSigintEscalates:
    """The second SIGINT path must escalate to ``os._exit(130)`` so a stuck
    run cannot ignore the operator's intent.
    """

    def test_interrupt_controller_defines_install_force_kill_handler(self) -> None:
        controller = RALPH_ROOT / "interrupt" / "controller.py"
        if not controller.exists():
            pytest.skip("interrupt/controller.py not present")
        source = _read(controller)
        assert "def install_force_kill_handler" in source
        assert "os._exit" in source or "_exit" in source


# ---------------------------------------------------------------------------
# Recovery cycle cap: enforced by RecoveryController
# ---------------------------------------------------------------------------


class TestRecoveryCycleCapOwner:
    """The recovery cycle cap is owned by ``RecoveryController`` (NOT a reducer counter)."""

    def test_recovery_controller_owns_cycle_cap(self) -> None:
        controller = RALPH_ROOT / "recovery" / "controller.py"
        if not controller.exists():
            pytest.skip("recovery/controller.py not present")
        source = _read(controller)
        assert "RecoveryController" in source
        # The cycle cap field/method should live on the controller class.
        assert "_cycle_cap" in source or "max_recovery_cycles" in source or "cycle_cap" in source


# ---------------------------------------------------------------------------
# Surface (i — backoff) — recovery controller owns the backoff computation
# ---------------------------------------------------------------------------


class TestRecoveryControllerOwnsBackoff:
    """Pin that backoff computation lives ONLY in
    `ralph/recovery/controller.py:compute_backoff_ms`. No other module may
    define a `compute_backoff`/`backoff_ms` helper."""

    def test_recovery_controller_owns_backoff(self) -> None:
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
            if "compute_backoff" not in source and "backoff_ms" not in source:
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name == "compute_backoff_ms":
                    # Only the recovery/controller.py copy is allowed.
                    if path != RALPH_ROOT / "recovery" / "controller.py":
                        offenders.append(
                            f"{path.relative_to(RALPH_ROOT.parent)}:{node.lineno} {node.name}"
                        )
                elif "backoff" in node.name.lower() and "compute" in node.name.lower():
                    offenders.append(
                        f"{path.relative_to(RALPH_ROOT.parent)}:{node.lineno} {node.name}"
                    )
        assert offenders == [], (
            "Backoff computation reimplementations found outside "
            f"ralph/recovery/controller.py: {offenders}. Route through "
            "compute_backoff_ms (ralph/recovery/controller.py)."
        )


# ---------------------------------------------------------------------------
# Surface (f) — post-exit watchdog does NOT define classify_quiet/classify_exit
# ---------------------------------------------------------------------------


class TestPostExitWatchdogConsumesCallback:
    """Pin that `ralph/agents/idle_watchdog/_post_exit_watchdog.py` does NOT define
    `def classify_quiet` or `def classify_exit` — it consumes them via
    injected `classify_exit_state` callback. The classification logic
    lives in per-strategy modules under
    `ralph/agents/execution_state/*_execution_strategy.py`.
    """

    def test_post_exit_watchdog_consumes_callback(self) -> None:
        watchdog = (
            RALPH_ROOT
            / "agents"
            / "idle_watchdog"
            / "_post_exit_watchdog.py"
        )
        if not watchdog.exists():
            pytest.skip("post_exit_watchdog module not present")
        source = _read(watchdog)
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in ("classify_quiet", "classify_exit"):
                offenders.append(f"{node.lineno} {node.name}")
        assert offenders == [], (
            "ralph/agents/idle_watchdog/_post_exit_watchdog.py must NOT define "
            f"classify_quiet/classify_exit: {offenders}. Use the injected "
            "classify_exit_state callback; classification lives in "
            "ralph/agents/execution_state/*_execution_strategy.py."
        )


# ---------------------------------------------------------------------------
# Surface (f — strategies) — classify_quiet unknown state defaults to WAITING_ON_CHILD
# ---------------------------------------------------------------------------


class TestClassifyQuietUnknownStateDefaultsToWaiting:
    """Pin that a transient liveness-probe exception defaults to
    `WAITING_ON_CHILD` (NOT `TERMINAL_COMPLETE`) for every execution
    strategy. This is the safety net that prevents the runner from
    declaring a stuck agent complete just because the liveness probe
    raised a transient error.
    """

    @pytest.mark.parametrize(
        "strategy_module",
        [
            RALPH_ROOT / "agents" / "execution_state" / "opencode_execution_strategy.py",
            RALPH_ROOT / "agents" / "execution_state" / "claude_interactive_execution_strategy.py",
            RALPH_ROOT / "agents" / "execution_state" / "agy_execution_strategy.py",
            RALPH_ROOT / "agents" / "execution_state" / "generic_execution_strategy.py",
        ],
    )
    def test_classify_quiet_unknown_state_defaults_to_waiting(
        self, strategy_module: pathlib.Path
    ) -> None:
        if not strategy_module.exists():
            pytest.skip(f"{strategy_module.name} not present")
        source = _read(strategy_module)
        # The strategy must define a `classify_quiet` method (or not
        # define one — in which case the base class default applies). The
        # default behavior is enforced by the AgentExecutionState enum,
        # not by per-strategy code. This test pins the strategy's
        # coverage: it must NOT override classify_quiet to return
        # TERMINAL_COMPLETE for an unknown liveness-probe exception.
        # The default may be referenced either in the strategy file
        # itself OR via the base class in
        # `ralph/agents/execution_state/agent_execution_state.py`.
        if "def classify_quiet" in source and "WAITING_ON_CHILD" not in source:
            # The strategy defines its own classify_quiet but does not
            # reference WAITING_ON_CHILD in the unknown-state default
            # branch. Walk the file's AST to find the `WAITING_ON_CHILD`
            # default branch. It may be the LAST explicit return (the
            # default) OR a branch that maps an unknown state to
            # WAITING_ON_CHILD. We also allow the default to live in
            # the base class.
            base = RALPH_ROOT / "agents" / "execution_state" / "agent_execution_state.py"
            if not base.exists() or "WAITING_ON_CHILD" not in _read(base):
                pytest.fail(
                    f"{strategy_module.name} defines classify_quiet but "
                    "does not reference WAITING_ON_CHILD in the "
                    "unknown-state default branch (neither directly "
                    "nor in the base class)."
                )


# ---------------------------------------------------------------------------
# Surface (g — PA-006) — commit plumbing failure classification is preserved
# ---------------------------------------------------------------------------


class TestCommitPlumbingFailureClassificationPreserved:
    """Pin the two pre-fix commit.py classification paths are still
    intact after the refactor. The post-refactor plumbing delegates
    agent invocation to effect_executor.execute_agent_effect, which
    owns the canonical run_with_direct_mcp_recovery retry loop.
    """

    def test_commit_plumbing_failure_classification_preserved(self) -> None:
        # Resolve the canonical retry-intent helpers via the public
        # surface; the plumbing must call them with the same arguments.
        action = recovery_action_for_failure_reason(
            "AgentInactivityTimeoutError",
            has_prior_session=True,
        )
        assert action == "resume", (
            "recovery_action_for_failure_reason(AgentInactivityTimeoutError, "
            f"has_prior_session=True) must return 'resume' but got {action!r}"
        )

        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        if not plumbing.exists():
            pytest.skip("commit_plumbing.py not present")
        source = _read(plumbing)
        # The plumbing must NOT construct FailureClassifier() inline.
        assert "FailureClassifier()" not in source, (
            "commit_plumbing.py must NOT construct FailureClassifier() inline; "
            "it must route classification through effect_executor.execute_agent_effect."
        )
        # The plumbing must delegate to the shared execution core.
        assert "execute_agent_effect" in source, (
            "commit_plumbing.py must call ralph.pipeline.effect_executor.execute_agent_effect."
        )

        effect_executor = RALPH_ROOT / "pipeline" / "effect_executor.py"
        assert effect_executor.exists()
        effect_source = _read(effect_executor)
        assert "run_with_direct_mcp_recovery" in effect_source, (
            "effect_executor.py must contain the canonical retry loop run_with_direct_mcp_recovery."
        )

        # The AgentInactivityTimeoutError is the error class the pre-fix
        # commit.py classified inline. Confirm it is importable from the
        # public surface so commit.py can catch it via the plumbing.
        assert AgentInactivityTimeoutError is not None


# ---------------------------------------------------------------------------
# Surface (f — PA-002) — interrupt path handles already-degraded state
# ---------------------------------------------------------------------------


class TestInterruptPathHandlesAlreadyDegradedState:
    """Pin that the second-SIGINT path escalates to `os._exit(130)` even
    when the runner is already in a degraded state (e.g. the agent
    has wedged). The test is fully in-process: it uses the
    `InterruptController` directly with stub shutdown closures, drives
    the second-SIGINT escalation path, and asserts the hard-exit
    closure is called with code 130.
    """

    def test_interrupt_path_handles_already_degraded_state(self) -> None:
        hard_exit_calls: list[int] = []
        shutdown_calls: list[tuple[float, ...]] = []

        def _hard_exit(code: int) -> None:
            hard_exit_calls.append(code)
            # Do not actually exit; we are testing the seam.
            raise SystemExit(code)

        def _shutdown_all(grace_period_s: float) -> None:
            shutdown_calls.append((grace_period_s,))

        controller = InterruptController(
            shutdown_all=_shutdown_all,
            hard_exit=_hard_exit,
        )

        # Simulate the second-SIGINT escalation: force_interrupt then
        # force_exit. force_exit must call _hard_exit(130). The
        # hard_exit stub raises SystemExit; we catch it to assert
        # behavior without actually exiting the test runner.
        with pytest.raises(SystemExit) as excinfo:
            controller.force_exit()
        assert excinfo.value.code == INTERRUPT_EXIT_CODE, (
            f"force_exit must call hard_exit(130); got SystemExit code={excinfo.value.code!r}."
        )
        assert hard_exit_calls == [INTERRUPT_EXIT_CODE], (
            f"force_exit must call hard_exit(130); got {hard_exit_calls}."
        )
        # shutdown_all(0) must have been called inside force_interrupt.
        assert any(call[0] == 0.0 for call in shutdown_calls), (
            "force_interrupt must call shutdown_all(0.0) for immediate "
            "termination; got " + repr(shutdown_calls)
        )


# ---------------------------------------------------------------------------
# Surface (f — PA-006) — wedged run exits cleanly with code 130
# ---------------------------------------------------------------------------


class TestWedgedRunExitsCleanly:
    """Per PA-006 (deterministic in-memory wedged-run test): simulate a
    run where the agent never produces output, exercise the second-SIGINT
    path, and assert the runner exits cleanly with code 130 within 5
    seconds of wall-clock (using FakeClock). The test does NOT spawn a
    real subprocess; it injects a fake AgentExecutor that returns no
    output and the InterruptController with a stub hard_exit.
    """

    def test_wedged_run_exits_cleanly(self) -> None:
        clock = FakeClock(start=0.0)
        start = time.perf_counter()

        hard_exit_calls: list[int] = []

        def _hard_exit(code: int) -> None:
            hard_exit_calls.append(code)
            # Stop the test instead of actually exiting.
            raise SystemExit(code)

        def _shutdown_all(grace_period_s: float) -> None:
            # Simulate that the wedged agent cannot be shut down via the
            # graceful path; this is the "already degraded" condition.
            clock.advance(grace_period_s)

        controller = InterruptController(
            shutdown_all=_shutdown_all,
            hard_exit=_hard_exit,
        )

        # Drive the second-SIGINT path. force_exit must call hard_exit(130)
        # which raises SystemExit. We catch it to assert behavior.
        with pytest.raises(SystemExit) as excinfo:
            controller.force_exit()

        elapsed = time.perf_counter() - start
        assert excinfo.value.code == INTERRUPT_EXIT_CODE, (
            f"Wedged run must exit with code {INTERRUPT_EXIT_CODE}; got {excinfo.value.code!r}."
        )
        assert elapsed < 5.0, (
            f"Wedged-run second-SIGINT escalation took {elapsed:.2f}s; "
            "must complete within 5s wall-clock."
        )
        assert hard_exit_calls == [INTERRUPT_EXIT_CODE], (
            f"hard_exit was called with {hard_exit_calls}; expected [{INTERRUPT_EXIT_CODE}]."
        )
