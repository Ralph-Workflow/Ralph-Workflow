"""Static scan: the reducer is the single ``last_decision`` writer.

Contract: ``run_loop.py`` and ``runner.py`` must not write
``RebaseState.last_decision`` directly (assignment or ``model_copy(update=...)``).
The reducer (``ralph/pipeline/reducer.py``) is the only writer; both modules
must instead consult the typed authorities ``next_integration_action`` and
``decide_phase_transition``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = [
    _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py",
    _REPO_ROOT / "ralph" / "pipeline" / "runner.py",
]


def _last_decision_writer_lines(path: Path) -> list[str]:
    """Offending ``last_decision`` writer sites in ``path``."""
    offenders: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return offenders
    for node in ast.walk(tree):
        # model_copy(update={"last_decision": ...}) or copy_with(last_decision=...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"copy_with", "model_copy"}
        ):
            window = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            if "last_decision=" in window:
                offenders.append(f"{path.name}:{node.lineno}")
            for key in _dict_str_keys(node):
                if key == "last_decision":
                    offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def _dict_str_keys(call: ast.Call) -> list[str]:
    keys: list[str] = []
    for kw in call.keywords:
        if kw.arg == "update" and isinstance(kw.value, ast.Dict):
            keys.extend(str(k.value) for k in kw.value.keys if isinstance(k, ast.Constant))
    return keys


def test_no_direct_last_decision_writers() -> None:
    offenders = [line for path in _TARGETS for line in _last_decision_writer_lines(path)]
    assert offenders == []


def test_authority_calls_present() -> None:
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    runner = _TARGETS[1].read_text(encoding="utf-8")
    assert "next_integration_action(" in run_loop
    assert "decide_phase_transition(" in run_loop
    assert "next_integration_action(" in runner
    assert "decide_phase_transition(" in runner
    assert "from ralph.pipeline._integration_action import" in run_loop
    assert "from ralph.pipeline._integration_action import" in runner
    assert "from ralph.pipeline._pipeline_state_machine import" in run_loop
    assert "from ralph.pipeline._pipeline_state_machine import" in runner
