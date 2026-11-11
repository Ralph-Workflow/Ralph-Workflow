from __future__ import annotations

import ast
import pathlib
import time

import pytest

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.agents.timeout_clock import FakeClock
from ralph.interrupt.controller import INTERRUPT_EXIT_CODE, InterruptController

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    import os

    results: list[pathlib.Path] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        if "__pycache__" in dirnames:
            dirnames.remove("__pycache__")
        for fn in filenames:
            if fn.endswith(".py"):
                results.append(pathlib.Path(dirpath) / fn)
    return results


def _find_failure_classifier_construction_sites() -> list[pathlib.Path]:
    hits: list[pathlib.Path] = []
    for path in _walk_python_files(RALPH_ROOT):
        if b"FailureClassifier(" in path.read_bytes():
            hits.append(path)
    return hits


_FAILURE_CLASSIFIER_CONSTRUCTION_SITES: tuple[pathlib.Path, ...] = tuple(
    _find_failure_classifier_construction_sites()
)


class TestFailureClassifierSingleOwner:
    def test_failure_classifier_only_in_allowed_sites(self) -> None:
        allowed_relative = {
            pathlib.Path("ralph/recovery/failure_classifier.py"),
            pathlib.Path("ralph/recovery/classifier.py"),
            pathlib.Path("ralph/recovery/controller.py"),
            pathlib.Path("ralph/agents/invoke/_direct_mcp_recovery.py"),
            pathlib.Path("ralph/agents/invoke/_completion.py"),
            pathlib.Path("ralph/pipeline/agent_retry_decision.py"),
            pathlib.Path("ralph/testing/audit_agent_failure_single_owner.py"),
            pathlib.Path("ralph/testing/_single_owner_rules.py"),
        }
        offenders: list[str] = []
        for path in _FAILURE_CLASSIFIER_CONSTRUCTION_SITES:
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel not in allowed_relative:
                offenders.append(str(rel))
        assert offenders == [], (
            f"FailureClassifier( is constructed outside the allowed sites: {offenders}."
        )


class TestCanonicalChildEvidenceModel:
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
        assert classify_funcs == ["classify_child_snapshot"], (
            f"Expected only classify_child_snapshot in child_liveness.py; got {classify_funcs}."
        )

    def test_helpers_delegate_to_canonical_model(self) -> None:
        helpers = RALPH_ROOT / "agents" / "execution_state" / "_helpers.py"
        source = _read(helpers)
        assert "classify_child_snapshot" in source, (
            "execution_state/_helpers.py must call classify_child_snapshot"
        )


class TestSecondSigintEscalates:
    def test_interrupt_controller_defines_install_force_kill_handler(self) -> None:
        controller = RALPH_ROOT / "interrupt" / "controller.py"
        source = _read(controller)
        assert "def install_force_kill_handler" in source
        assert "os._exit" in source or "_exit" in source


class TestRecoveryCycleCapOwner:
    def test_recovery_controller_owns_cycle_cap(self) -> None:
        controller = RALPH_ROOT / "recovery" / "controller.py"
        source = _read(controller)
        assert "RecoveryController" in source
        assert "_cycle_cap" in source or "max_recovery_cycles" in source or "cycle_cap" in source


def _find_backoff_candidate_files() -> list[tuple[pathlib.Path, ast.AST]]:
    hits: list[tuple[pathlib.Path, ast.AST]] = []
    for path in _walk_python_files(RALPH_ROOT):
        raw = path.read_bytes()
        if b"compute_backoff" in raw or b"backoff_ms" in raw:
            hits.append((path, ast.parse(raw, filename=str(path))))
    return hits


_RECOVERY_BACKOFF_CANDIDATE_FILES: tuple[tuple[pathlib.Path, ast.AST], ...] = tuple(
    _find_backoff_candidate_files()
)


class TestRecoveryControllerOwnsBackoff:
    def test_recovery_controller_owns_backoff(self) -> None:
        offenders: list[str] = []
        for path, tree in _RECOVERY_BACKOFF_CANDIDATE_FILES:
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name == "compute_backoff_ms":
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


class TestPostExitWatchdogConsumesCallback:
    def test_post_exit_watchdog_consumes_callback(self) -> None:
        watchdog = RALPH_ROOT / "agents" / "idle_watchdog" / "_post_exit_watchdog.py"
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


class TestClassifyQuietUnknownStateDefaultsToWaiting:
    @pytest.mark.parametrize(
        "strategy_module",
        [
            RALPH_ROOT / "agents" / "execution_state" / "opencode_execution_strategy.py",
            RALPH_ROOT / "agents" / "execution_state" / "claude_interactive_execution_strategy.py",
            RALPH_ROOT / "agents" / "execution_state" / "generic_execution_strategy.py",
        ],
    )
    def test_classify_quiet_unknown_state_defaults_to_waiting(
        self, strategy_module: pathlib.Path
    ) -> None:
        source = _read(strategy_module)
        if "def classify_quiet" in source and "WAITING_ON_CHILD" not in source:
            base = RALPH_ROOT / "agents" / "execution_state" / "agent_execution_state.py"
            if not base.exists() or "WAITING_ON_CHILD" not in _read(base):
                pytest.fail(
                    f"{strategy_module.name} defines classify_quiet but "
                    "does not reference WAITING_ON_CHILD in the "
                    "unknown-state default branch (neither directly "
                    "nor in the base class)."
                )


class TestCommitPlumbingFailureClassificationPreserved:
    def test_commit_plumbing_failure_classification_preserved(self) -> None:
        action = recovery_action_for_failure_reason(
            "AgentInactivityTimeoutError",
            has_prior_session=True,
        )
        assert action == "resume", (
            "recovery_action_for_failure_reason(AgentInactivityTimeoutError, "
            f"has_prior_session=True) must return 'resume' but got {action!r}"
        )

        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        source = _read(plumbing)
        assert "FailureClassifier()" not in source, (
            "commit_plumbing.py must NOT construct FailureClassifier() inline; "
            "it must route classification through effect_executor.execute_agent_effect."
        )
        assert "execute_agent_effect" in source, (
            "commit_plumbing.py must call ralph.pipeline.effect_executor.execute_agent_effect."
        )

        effect_executor = RALPH_ROOT / "pipeline" / "effect_executor.py"
        assert effect_executor.exists()
        effect_source = _read(effect_executor)
        assert "run_with_direct_mcp_recovery" in effect_source, (
            "effect_executor.py must contain the canonical retry loop run_with_direct_mcp_recovery."
        )

        assert AgentInactivityTimeoutError is not None


class TestInterruptPathHandlesAlreadyDegradedState:
    def test_interrupt_path_handles_already_degraded_state(self) -> None:
        hard_exit_calls: list[int] = []
        shutdown_calls: list[tuple[float, ...]] = []

        def _hard_exit(code: int) -> None:
            hard_exit_calls.append(code)
            raise SystemExit(code)

        def _shutdown_all(grace_period_s: float) -> None:
            shutdown_calls.append((grace_period_s,))

        controller = InterruptController(
            shutdown_all=_shutdown_all,
            hard_exit=_hard_exit,
        )

        with pytest.raises(SystemExit) as excinfo:
            controller.force_exit()
        assert excinfo.value.code == INTERRUPT_EXIT_CODE, (
            f"force_exit must call hard_exit(130); got SystemExit code={excinfo.value.code!r}."
        )
        assert hard_exit_calls == [INTERRUPT_EXIT_CODE], (
            f"force_exit must call hard_exit(130); got {hard_exit_calls}."
        )
        assert any(call[0] == 0.0 for call in shutdown_calls), (
            "force_interrupt must call shutdown_all(0.0) for immediate "
            "termination; got " + repr(shutdown_calls)
        )


class TestWedgedRunExitsCleanly:
    def test_wedged_run_exits_cleanly(self) -> None:
        clock = FakeClock(start=0.0)
        start = time.perf_counter()

        hard_exit_calls: list[int] = []

        def _hard_exit(code: int) -> None:
            hard_exit_calls.append(code)
            raise SystemExit(code)

        def _shutdown_all(grace_period_s: float) -> None:
            clock.advance(grace_period_s)

        controller = InterruptController(
            shutdown_all=_shutdown_all,
            hard_exit=_hard_exit,
        )

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
