"""Tests for RebaseState typed setters as single writers (G2 + G5).

Contract: ``last_fan_out_decision`` and ``operation_display_state`` may
only be written inside ``ralph/pipeline/rebase_state.py`` via
``set_fan_out_decision`` / ``set_operation_display_state``. This test
static-scans the full ``ralph/`` tree for forbidden writers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.pipeline._fan_out_authority import FanOutDecision
from ralph.pipeline.rebase_state import RebaseState

RALPH_ROOT = Path(__file__).resolve().parents[1] / "ralph"

_FORBIDDEN_FIELDS = ("last_fan_out_decision", "operation_display_state")
_AUTHORITATIVE_FILE = "rebase_state.py"


def _scan_writer_sites(root: Path) -> list[str]:
    """AST/static-scan every ``*.py`` under *root* for forbidden writers.

    A writer is a ``model_copy``/``copy_with`` keyword argument named
    after one of the fields, or a string literal of the field name
    (update-dict key) outside the authoritative file.
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == _AUTHORITATIVE_FILE:
            continue
        raw = path.read_bytes()
        if not any(field.encode() in raw for field in _FORBIDDEN_FIELDS):
            continue
        source = raw.decode(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_writer_call = isinstance(node.func, ast.Attribute) and node.func.attr in {
                "model_copy",
                "copy_with",
            }
            if not is_writer_call:
                continue
            for kw in node.keywords:
                if kw.arg in _FORBIDDEN_FIELDS:
                    offenders.append(f"{path.relative_to(root)}:{kw.lineno} copy-call-keyword")
            # update-dict keys: model_copy(update={"<field>": ...})
            for kw in node.keywords:
                if kw.arg == "update" and isinstance(kw.value, ast.Dict):
                    for key in kw.value.keys:
                        if (
                            isinstance(key, ast.Constant)
                            and isinstance(key.value, str)
                            and key.value in _FORBIDDEN_FIELDS
                        ):
                            offenders.append(
                                f"{path.relative_to(root)}:{key.lineno} update-dict-key"
                            )
    return offenders


def test_set_fan_out_decision_round_trips_each_member() -> None:
    state = RebaseState()
    for decision in FanOutDecision:
        updated = state.set_fan_out_decision(decision)
        assert updated.last_fan_out_decision == decision.value
        # frozen model: the original snapshot is untouched
        assert state.last_fan_out_decision is None


def test_set_fan_out_decision_rejects_non_members() -> None:
    state = RebaseState()
    setter = state.set_fan_out_decision
    with pytest.raises(ValueError, match="invalid fan_out_decision"):
        setter("join_pending")


def test_set_operation_display_state_round_trips_each_member() -> None:
    state = RebaseState()
    for display in OperationDisplayState:
        updated = state.set_operation_display_state(display)
        assert updated.operation_display_state == display.value
        # frozen model: the original snapshot is untouched
        assert state.operation_display_state is None


def test_set_operation_display_state_rejects_non_members_and_legacy() -> None:
    state = RebaseState()
    setter = state.set_operation_display_state
    for bad in (
        "waiting",
        # legacy alias strings must not sneak through
        "integration",
        "publication",
        "intervention",
        "completed",
        "diagnosing",
        "bogus",
    ):
        with pytest.raises(ValueError, match="invalid operation_display_state"):
            setter(bad)


def test_no_raw_writers_outside_rebase_state() -> None:
    offenders = _scan_writer_sites(RALPH_ROOT)
    assert offenders == [], (
        f"raw last_fan_out_decision / operation_display_state writers "
        f"outside {_AUTHORITATIVE_FILE}: {offenders}"
    )


def test_audit_catches_synthetic_offender_but_not_benign_file(
    tmp_path: Path,
) -> None:
    """Black-box regression: a forbidden writer anywhere under ralph/ is caught."""
    ralph_root = tmp_path / "ralph"
    (ralph_root / "subtree_a").mkdir(parents=True)
    (ralph_root / "subtree_b").mkdir(parents=True)
    # Offender in subtree_a (earlier rglob hit); benign file in subtree_b.
    (ralph_root / "subtree_a" / "offender.py").write_text(
        "x = some_state.model_copy(update={'operation_display_state': 'waiting'})\n"
        "y = rebase.copy_with(last_fan_out_decision=d)\n",
        encoding="utf-8",
    )
    (ralph_root / "subtree_b" / "benign.py").write_text(
        "# operation_display_state appears only in a comment\nx = 1\n",
        encoding="utf-8",
    )
    offenders = _scan_writer_sites(ralph_root)
    assert any("offender.py" in entry for entry in offenders), offenders
    assert not any("benign.py" in entry for entry in offenders), offenders
