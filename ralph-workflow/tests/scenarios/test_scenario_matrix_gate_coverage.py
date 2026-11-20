from __future__ import annotations

import ast
from pathlib import Path

from ralph.test_suites import REQUIRED_AUTO_INTEGRATE_E2E_FILES

_SCENARIO_DIRECTORY = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCENARIO_DIRECTORY.parents[1]
_REQUIRED_MARKER = "required_auto_integrate_e2e"
_EXCLUDED_MARKERS = frozenset({"live_agy", "smoke", "subprocess_e2e", "verify_budget_real_time"})


def _mark_names(expression: ast.expr) -> frozenset[str]:
    if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        names: set[str] = set()
        for element in expression.elts:
            names |= _mark_names(element)
        return frozenset(names)
    target = expression.func if isinstance(expression, ast.Call) else expression
    if not isinstance(target, ast.Attribute):
        return frozenset()
    owner = target.value
    if not isinstance(owner, ast.Attribute) or owner.attr != "mark":
        return frozenset()
    if not isinstance(owner.value, ast.Name) or owner.value.id != "pytest":
        return frozenset()
    return frozenset({target.attr})


def _module_markers(tree: ast.Module) -> frozenset[str] | None:
    names: frozenset[str] = frozenset()
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        ):
            names = _mark_names(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "pytestmark"
        ):
            names = frozenset() if statement.value is None else _mark_names(statement.value)
        elif (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "pytestmark"
        ):
            return None
    return names


def _decorator_markers(decorators: list[ast.expr]) -> frozenset[str]:
    names: set[str] = set()
    for decorator in decorators:
        names |= _mark_names(decorator)
    return frozenset(names)


def _test_marker_sets(
    tree: ast.Module, module_markers: frozenset[str]
) -> list[frozenset[str]] | None:
    collected: list[frozenset[str]] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name.startswith("test"):
                collected.append(module_markers | _decorator_markers(statement.decorator_list))
            continue
        if not isinstance(statement, ast.ClassDef):
            continue
        if any(
            isinstance(base, ast.Name) and base.id.endswith("TestCase") for base in statement.bases
        ):
            return None
        class_markers: frozenset[str] = frozenset()
        for child in statement.body:
            if isinstance(child, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in child.targets
            ):
                class_markers = _mark_names(child.value)
            elif isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and child.name.startswith("test"):
                collected.append(
                    module_markers | class_markers | _decorator_markers(child.decorator_list)
                )
    return collected


def _file_marker_sets(
    source: str, filename: str
) -> tuple[frozenset[str] | None, list[frozenset[str]] | None]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return None, None
    module_markers = _module_markers(tree)
    if module_markers is None:
        return None, None
    return module_markers, _test_marker_sets(tree, module_markers)


def _per_test_exclusion_reason(relative_path: str, marker_sets: list[frozenset[str]]) -> str | None:
    for markers in marker_sets:
        if _REQUIRED_MARKER in markers or not (markers & _EXCLUDED_MARKERS):
            return None
    return (
        f"{relative_path}: every test is marked only with excluded markers "
        f"({', '.join(sorted(_EXCLUDED_MARKERS))}) and none with {_REQUIRED_MARKER}"
    )


def _exclusion_reason(relative_path: str, source: str) -> str | None:
    if relative_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        return None
    module_markers, marker_sets = _file_marker_sets(source, relative_path)
    if module_markers is None or marker_sets is None:
        return None
    if _REQUIRED_MARKER in module_markers:
        return None
    if marker_sets:
        return _per_test_exclusion_reason(relative_path, marker_sets)
    excluded = sorted(_EXCLUDED_MARKERS.intersection(module_markers))
    if not excluded:
        return None
    return f"{relative_path}: module-level pytestmark carries {', '.join(excluded)}"


def test_every_scenario_matrix_file_runs_in_the_default_verification_lane() -> None:
    scenario_paths = sorted(_SCENARIO_DIRECTORY.glob("test_*.py"))
    assert len(scenario_paths) >= 10, (
        f"expected the scenario matrix to hold at least 10 test files, found {len(scenario_paths)}"
    )
    excluded: list[str] = []
    for scenario_path in scenario_paths:
        relative_path = scenario_path.relative_to(_PROJECT_ROOT).as_posix()
        reason = _exclusion_reason(relative_path, scenario_path.read_text(encoding="utf-8"))
        if reason is not None:
            excluded.append(reason)
    assert not excluded, (
        "every scenario-matrix file must be selected by the default verification lane "
        f"'(not subprocess_e2e and not smoke) or {_REQUIRED_MARKER}'; "
        "excluded files: " + "; ".join(excluded)
    )
