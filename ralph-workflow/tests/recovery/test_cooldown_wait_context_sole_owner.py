from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout_seconds(5)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_cooldown_wait_context_sole_owner_and_run_loop_reexport": (
        "CooldownWaitContext must live in its dedicated owning module; run_loop must "
        "re-export that class and must not declare a duplicate ClassDef that breaks "
        "the single-class-per-module audit and leaves conflict-resolution cooldown "
        "waits on a fork of shared wait state"
    ),
}

_REPO = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO / "ralph" / "pipeline" / "cooldown_wait_context.py"
_FACADE = _REPO / "ralph" / "pipeline" / "run_loop.py"


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _declared_public_classes(tree: ast.Module) -> tuple[str, ...]:
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    )


def _imports_cooldown_wait_context(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "ralph.pipeline.cooldown_wait_context":
            continue
        if any(alias.name == "CooldownWaitContext" for alias in node.names):
            return True
    return False


def test_cooldown_wait_context_sole_owner_and_run_loop_reexport() -> None:
    canonical = _module_tree(_CANONICAL)
    facade = _module_tree(_FACADE)
    assert _declared_public_classes(canonical) == ("CooldownWaitContext",)
    assert "CooldownWaitContext" not in _declared_public_classes(facade)
    assert _imports_cooldown_wait_context(facade)
