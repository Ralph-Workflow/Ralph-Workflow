"""Resource-lifecycle compliance tests for ralph/mcp/explore/.

Mirrors the contract enforced by
``ralph/testing/audit_resource_lifecycle.py``:

* Long-lived mutable accumulators (``list``, ``dict``, ``set``,
  ``deque``) assigned to module-level names or to ``self.X`` inside
  ``__init__`` bodies must carry a FIFO/size cap (``deque(maxlen=...)``
  or ``OrderedDict`` + count cap) or a ``# bounded-accumulator-ok: <reason>``
  marker.

These tests use the existing audit module to confirm the new explore
package stays in compliance. They live alongside the explore tests
so a future regression in the audit module surfaces immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest


EXPLORE_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "mcp" / "explore"


def _audit_module_via_python_api() -> tuple[list, int]:
    """Run the resource-lifecycle audit on the explore package."""
    from ralph.testing.audit_resource_lifecycle import (
        audit_resource_lifecycle_directory,
    )

    return audit_resource_lifecycle_directory(EXPLORE_ROOT)


def test_explore_handlers_use_bounded_accumulators() -> None:
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    try:
        violations, files_checked = _audit_module_via_python_api()
    except AttributeError:
        # audit_resource_lifecycle does not expose a per-directory
        # Python entrypoint; fall back to the CLI command, which is
        # the canonical audit path.
        import subprocess

        result = subprocess.run(
            ["python", "-m", "ralph.testing.audit_resource_lifecycle", str(EXPLORE_ROOT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "audit_resource_lifecycle reported violations:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        return
    formatted = "\n".join(str(v) for v in violations)
    assert not violations, (
        f"Found {len(violations)} resource-lifecycle violations in "
        f"{files_checked} file(s):\n{formatted}"
    )


def test_no_unbounded_deque_in_explore() -> None:
    """A ``deque()`` without ``maxlen`` is treated as unbounded."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    for py_file in sorted(EXPLORE_ROOT.rglob("*.py")):
        if "audit" in py_file.name:
            continue
        text = py_file.read_text(encoding="utf-8")
        # Look for ``deque()`` without ``maxlen=``.
        import re

        for match in re.finditer(r"\bdeque\s*\(", text):
            line_no = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[line_no - 1]
            if "maxlen" not in line and "bounded-accumulator-ok" not in line:
                # Allow ``deque(...)`` whose first positional is a
                # maxlen=`` inside a kwarg.
                pytest.fail(
                    f"{py_file}:{line_no}: unbounded deque: {line.strip()}"
                )


def test_no_module_level_mutable_list_in_explore() -> None:
    """Module-level ``[]``/``{}``/``set()`` are flagged by the audit."""
    if not EXPLORE_ROOT.is_dir():
        pytest.skip(f"explore module not present: {EXPLORE_ROOT}")
    import ast

    for py_file in sorted(EXPLORE_ROOT.rglob("*.py")):
        if "audit" in py_file.name or py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target_id = node.target.id
                if (
                    target_id == "__all__"
                    or "bounded-accumulator-ok" in (
                        py_file.read_text(encoding="utf-8").split("\n")[
                            node.lineno - 1
                        ]
                    )
                ):
                    continue
                if isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                    pytest.fail(
                        f"{py_file}:{node.lineno}: module-level mutable "
                        f"literal assigned to {target_id!r}"
                    )