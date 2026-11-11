from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout_seconds(5)

_REPO_ROOT = Path(__file__).resolve().parents[1]
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
_FORBIDDEN_BYTES: tuple[bytes, ...] = tuple(f.encode("utf-8") for f in _FORBIDDEN_FIELDS)
_WRITER_METHODS_BYTES: tuple[bytes, ...] = (b"copy_with", b"model_copy")
_AUTHORITATIVE_FILE = "rebase_state.py"


def _scan_writer_sites(*roots: Path) -> list[str]:
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.name == _AUTHORITATIVE_FILE:
                continue
            raw = path.read_bytes()
            if not any(w in raw for w in _WRITER_METHODS_BYTES):
                continue
            if not any(field in raw for field in _FORBIDDEN_BYTES):
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
    ralph_root = tmp_path / "ralph"
    (ralph_root / "subtree_a").mkdir(parents=True)
    (ralph_root / "subtree_b").mkdir(parents=True)
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
