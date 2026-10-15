"""Static scan: no direct is_waiting_state / recovery_record_retained writers.

Contract: ``is_waiting_state`` may only be written inside
``ralph/pipeline/state.py`` (via ``set_waiting`` / ``clear_waiting``) and
``recovery_record_retained`` only inside ``ralph/pipeline/rebase_state.py``
(via ``set_recovery_record_retained`` / ``clear_recovery_record_retained``).
This test scans ``run_loop.py`` and ``runner.py`` for direct writer sites.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = [
    _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py",
    _REPO_ROOT / "ralph" / "pipeline" / "runner.py",
]


def _offender_lines(path: Path, flag: str) -> list[tuple[int, str]]:
    """(lineno, text) of calls writing ``flag`` via copy_with/model_copy."""
    offenders: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return offenders
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"copy_with", "model_copy"}:
            continue
        window = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if f"{flag}=" in window:
            offenders.append((node.lineno, lines[node.lineno - 1].strip()))
    return offenders


def test_no_direct_is_waiting_state_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "is_waiting_state")
    ]
    assert offenders == []


def test_no_direct_recovery_record_retained_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "recovery_record_retained")
    ]
    assert offenders == []


def test_setter_usage_present_in_run_loop() -> None:
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    assert "set_waiting(WaitReason." in run_loop
    assert ".clear_waiting()" in run_loop
    assert ".set_recovery_record_retained(" in run_loop
