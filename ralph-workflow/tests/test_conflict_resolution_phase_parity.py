from __future__ import annotations

import ast
import inspect
from pathlib import Path

from ralph.pipeline.conflict_resolution import session as conflict_session

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_session_has_no_finalize_resolution_attempt_failure": (
        "phase-parity module previously depended on removed "
        "finalize_resolution_attempt_failure; behavioral parity lives in "
        "tests/test_conflict_resolution_phase_parity_cooldown.py and "
        "tests/scenarios/test_integration_parity_matrix.py"
    ),
}


def test_session_has_no_finalize_resolution_attempt_failure() -> None:
    source = Path(inspect.getsourcefile(conflict_session) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "finalize_resolution_attempt_failure" not in names
    assert "finalize_resolution_attempt_failure" not in conflict_session.__all__
