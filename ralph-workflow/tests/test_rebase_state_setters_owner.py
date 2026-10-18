"""Tests for RebaseState typed setters as single writers (G2 + G5).

Contract: ``last_fan_out_decision`` and ``operation_display_state`` may
only be written inside ``ralph/pipeline/rebase_state.py`` via
``set_fan_out_decision`` / ``set_operation_display_state``. This test
static-scans every directory that could plausibly write rebase-state
fields (``ralph/pipeline/`` and ``ralph/display/``) for forbidden writers.
Other ralph/ subtrees (``mcp/``, ``agents/``, ``testing/``, ``install/``,
``process/`` infrastructure, ...) cannot reach ``RebaseState`` so they are
out of scope by construction. Scoping the scan keeps the per-test
wall-clock well under the 1 s per-test budget even under heavy shard load
(see the matching comment in ``test_recovery_record_retained_owner.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Only directories that can reach a ``RebaseState`` instance need to be
# scanned. ``ralph/pipeline/`` owns rebase state; ``ralph/display/`` reads
# ``operation_display_state`` and is the only other subtree that the
# forbidden fields touch. Keeping the scope tight keeps the static scan
# under the 1 s per-test budget under heavy shard load.
_SCAN_ROOTS: tuple[Path, ...] = (
    _REPO_ROOT / "ralph" / "pipeline",
    _REPO_ROOT / "ralph" / "display",
)

_FORBIDDEN_FIELDS = (
    "last_fan_out_decision",
    "operation_display_state",
    "retry_deadline",
    "conflict_strategy_index",
    "conflict_strategies_tried",
    "consecutive_conflicts",
    "resolution_exhausted",
    "resolution_exhaustion_reason",
)
_AUTHORITATIVE_FILE = "rebase_state.py"


def _scan_writer_sites(*roots: Path) -> list[str]:
    """AST/static-scan every ``*.py`` under each *root* for forbidden writers.

    A writer is a ``model_copy``/``copy_with`` keyword argument named
    after one of the fields, or a string literal of the field name
    (update-dict key) outside the authoritative file.
    """
    offenders: list[str] = []
    for root in roots:
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


def test_no_raw_writers_outside_rebase_state() -> None:
    offenders = _scan_writer_sites(*_SCAN_ROOTS)
    assert offenders == [], (
        f"raw forbidden-field writers outside {_AUTHORITATIVE_FILE}: {offenders}"
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
