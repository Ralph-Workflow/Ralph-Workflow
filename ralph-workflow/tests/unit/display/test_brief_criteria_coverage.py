from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

from tests.unit.display._criteria_probes import PROBES

if TYPE_CHECKING:
    from collections.abc import Iterator


ALL_IDS: tuple[str, ...] = (
    "A-1",
    "A-2",
    "A-3",
    "A-4",
    "A-6",
    "B-1",
    "B-2",
    "B-4",
    "B-5",
    "B-6",
    "C-1",
    "C-2",
    "C-3",
    "C-4",
    "C-5",
    "C-6",
    "C-7",
    "D-1",
    "D-2",
    "D-3",
    "D-4",
    "E-1",
    "E-2",
    "E-3",
    "E-4",
    "E-5",
    "E-6",
    "E-7",
    "E-8",
    "F-1",
    "F-2",
    "F-3",
    "F-4",
    "G-1",
    "G-2",
    "G-3",
    "G-4",
    "G-5",
    "G-6",
    "G-7",
    "G-8",
    "G-9",
    "G-10",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _all_test_files() -> Iterator[Path]:
    root = _repo_root() / "tests"
    for path in sorted(root.rglob("*.py")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if "__pycache__" in path.parts:
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            yield path


_ID_PATTERN = re.compile(r"^[A-G]-(10|[1-9])$")


def _function_node_is_module_level(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _parse_criteria_marker(node: ast.AST) -> list[str] | None:
    ids: list[str] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        is_criteria = (
            isinstance(func, ast.Attribute)
            and func.attr == "criteria"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "mark"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "pytest"
        )
        if not is_criteria:
            continue
        for arg in decorator.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                ids.append(arg.value)
            elif isinstance(arg, (ast.Tuple, ast.List)):
                ids.extend(
                    elt.value
                    for elt in arg.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                )
    return ids or None


def _collect_claims() -> dict[str, list[tuple[str, str]]]:
    claims: dict[str, list[tuple[str, str]]] = {id_: [] for id_ in ALL_IDS}
    bad_markers: list[tuple[str, str, str]] = []
    for path in _all_test_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not _function_node_is_module_level(node):
                continue
            ids = _parse_criteria_marker(node)
            if ids is None:
                continue
            qualname = node.name
            for id_ in ids:
                if not _ID_PATTERN.match(id_):
                    bad_markers.append((str(path.relative_to(_repo_root())), qualname, id_))
                    continue
                if id_ not in claims:
                    bad_markers.append((str(path.relative_to(_repo_root())), qualname, id_))
                    continue
                claims[id_].append((str(path.relative_to(_repo_root())), qualname))
    _collect_claims.bad_markers = bad_markers
    return claims


_CLAIMS: dict[str, list[tuple[str, str]]] = _collect_claims()


def _reset_probes_caches() -> None:
    from ralph.display import _palette

    _palette.reset_palette_solve_stats()
    try:
        from ralph.display import _markdown_theme

        _markdown_theme._markdown_palette_for_surface_cached.cache_clear()
    except (AttributeError, ImportError):
        pass
    try:
        from ralph.display import theme as _theme

        _theme._preview_foreground_for_surface_cached.cache_clear()
    except (AttributeError, ImportError):
        pass
    try:
        from ralph.display import theme as _theme

        if hasattr(_theme, "_preview_background_for_surface_cached"):
            _theme._preview_background_for_surface_cached.cache_clear()
    except (AttributeError, ImportError):
        pass
    try:
        from ralph.display import theme as _theme

        if hasattr(_theme, "_diff_token_foregrounds_for_surface_cached"):
            _theme._diff_token_foregrounds_for_surface_cached.cache_clear()
    except (AttributeError, ImportError):
        pass
    try:
        from ralph.display import syntax_theme

        if hasattr(syntax_theme, "_syntax_theme_for_surface_cached"):
            syntax_theme._syntax_theme_for_surface_cached.cache_clear()
    except (AttributeError, ImportError):
        pass


ALL_IDS: tuple[str, ...] = (
    "A-1",
    "A-2",
    "A-3",
    "A-4",
    "A-6",
    "B-1",
    "B-2",
    "B-4",
    "B-5",
    "B-6",
    "C-1",
    "C-2",
    "C-3",
    "C-4",
    "C-5",
    "C-6",
    "C-7",
    "D-1",
    "D-2",
    "D-3",
    "D-4",
    "E-1",
    "E-2",
    "E-3",
    "E-4",
    "E-5",
    "E-6",
    "E-7",
    "E-8",
    "F-1",
    "F-2",
    "F-3",
    "F-4",
    "G-1",
    "G-2",
    "G-3",
    "G-4",
    "G-5",
    "G-6",
    "G-7",
    "G-8",
    "G-9",
    "G-10",
)


def test_every_brief_criterion_id_is_claimed_by_exactly_one_collected_test() -> None:
    bad_markers = getattr(_collect_claims, "bad_markers", [])
    if bad_markers:
        kinds = ", ".join(f"{id_}@{file}::{name}" for file, name, id_ in bad_markers)
        raise AssertionError(f"invalid criteria markers: {kinds}")

    missing = sorted(id_ for id_, claims in _CLAIMS.items() if not claims)
    if missing:
        raise AssertionError(f"criteria IDs with no collected claim: {missing}")

    duplicates = sorted(id_ for id_, claims in _CLAIMS.items() if len(claims) > 1)
    if duplicates:
        claims = ", ".join(f"{id_} -> {[(f, n) for f, n in _CLAIMS[id_]]}" for id_ in duplicates)
        raise AssertionError(f"criteria IDs claimed by more than one test: {claims}")


def test_every_brief_criterion_id_has_a_registered_probe() -> None:
    if not PROBES:
        raise AssertionError("PROBES registry is empty")
    missing = sorted(id_ for id_ in ALL_IDS if id_ not in PROBES)
    if missing:
        raise AssertionError(f"criteria IDs without a registered probe: {missing}")
    extra = sorted(id_ for id_ in PROBES if id_ not in ALL_IDS and id_ not in {"A-5", "B-3"})
    if extra:
        raise AssertionError(f"probes registered for IDs outside the brief: {extra}")
