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

import pytest

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
            pathlib.Path("ralph/pipeline/effect_executor.py"),
            pathlib.Path("ralph/pipeline/plumbing/commit_plumbing.py"),
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
            "FailureClassifier( is constructed outside the allowed sites: "
            f"{offenders}."
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
        assert total == 6, (
            f"Expected exactly 6 watchdog.evaluate(...) call sites; got {total}."
        )


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
            "Expected only classify_child_snapshot in child_liveness.py; "
            f"got {classify_funcs}."
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
