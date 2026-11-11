from __future__ import annotations

import ast
import pathlib

import pytest

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"

_FORBIDDEN_MODULE_LEVEL_NAMES: frozenset[str] = frozenset(
    {
        "console",
        "global_console",
        "default_console",
    }
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _module_level_console_globals(source: str) -> list[tuple[int, str, ast.expr]]:
    tree = ast.parse(source)
    offenders: list[tuple[int, str, ast.expr]] = []

    def _consider_target(lineno: int, target: ast.expr, value: ast.expr | None) -> None:
        if value is None:
            return
        if not isinstance(target, ast.Name):
            return
        if target.id not in _FORBIDDEN_MODULE_LEVEL_NAMES:
            return
        if _is_console_construction(value):
            offenders.append((lineno, target.id, value))

    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            _consider_target(node.lineno, node.target, node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _consider_target(node.lineno, target, node.value)
    return offenders


def _is_console_construction(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Console"
    return isinstance(func, ast.Attribute) and func.attr == "Console"


@pytest.mark.timeout_seconds(5)
def test_no_module_level_console_globals_in_ralph_source() -> None:
    offenders: list[str] = []
    for path in _walk_python_files(RALPH_ROOT):
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if "Console(" not in source:
            continue
        for lineno, target, value in _module_level_console_globals(source):
            offenders.append(
                f"{path.relative_to(RALPH_ROOT.parent)}:{lineno} {target} = {ast.unparse(value)}"
            )
    assert offenders == [], (
        "Module-level Console construction found in ralph-workflow/ralph/; "
        "delete the offending module-level Console and inject it via "
        "ParallelDisplay instead: " + str(offenders)
    )


@pytest.mark.timeout_seconds(5)
def test_module_level_console_walk_finds_expected_files() -> None:
    files = _walk_python_files(RALPH_ROOT)
    assert len(files) > 0, "RALPH_ROOT should contain at least one .py file"
    for path in files:
        _ = _read(path)


def test_forbidden_names_match_existing_pin_vocabulary() -> None:
    existing = {"console", "global_console", "default_console"}
    assert existing == _FORBIDDEN_MODULE_LEVEL_NAMES, (
        f"_FORBIDDEN_MODULE_LEVEL_NAMES={set(_FORBIDDEN_MODULE_LEVEL_NAMES)!r} "
        f"drifted from the existing pin vocabulary {existing!r}"
    )


@pytest.mark.parametrize(
    "name",
    sorted(_FORBIDDEN_MODULE_LEVEL_NAMES),
)
def test_individual_forbidden_name_is_pinned(name: str) -> None:
    source = f"import builtins\n{name} = Console()\n"
    offenders = _module_level_console_globals(source)
    assert offenders, (
        f"expected a module-level `{name} = Console()` assignment to be flagged; "
        f"the helper missed it"
    )
    assert offenders[0][1] == name
