"""Test that public classes and functions in :members: modules have docstrings.

For every public ralph module documented with :members: in docs/sphinx/modules.rst,
every public top-level class and function must have a non-empty docstring so that
the Sphinx-rendered API reference is substantive.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"


def _parse_members_modules(modules_rst_text: str) -> set[str]:
    """Return module names (relative to ralph) documented with :members:.

    Parses the RST source and collects every ``.. automodule:: ralph.X``
    directive whose option block contains ``:members:`` but NOT ``:no-members:``.
    """
    members_modules: set[str] = set()

    # Split into blocks at each automodule directive.
    # Pattern: find ``.. automodule:: NAME`` followed by an indented option block.
    block_re = re.compile(
        r"^\.\. automodule::\s+(\S+)(.*?)(?=^\.\. automodule::|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for match in block_re.finditer(modules_rst_text):
        module_name = match.group(1).strip()
        option_block = match.group(2)

        has_members = bool(re.search(r"^\s+:members:", option_block, re.MULTILINE))
        has_no_members = bool(
            re.search(r"^\s+:no-members:", option_block, re.MULTILINE)
        )

        if has_members and not has_no_members:
            # Normalise to relative form (strip "ralph." prefix)
            if module_name.startswith("ralph."):
                rel = module_name[len("ralph."):]
            elif module_name == "ralph":
                rel = ""
            else:
                rel = module_name
            if rel:
                members_modules.add(rel)

    return members_modules


def _resolve_to_source(rel_name: str, ralph_root: Path) -> Path | None:
    """Map a documented ralph submodule name to its backing source file.

    Returns the leaf ``.py`` path for modules or ``__init__.py`` for packages.
    Returns ``None`` if neither exists.
    """
    parts = rel_name.split(".")
    # Try as leaf module
    leaf = ralph_root.joinpath(*parts[:-1], parts[-1] + ".py")
    if leaf.exists():
        return leaf
    # Try as package
    pkg = ralph_root.joinpath(*parts, "__init__.py")
    if pkg.exists():
        return pkg
    return None


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function is decorated with ``@overload`` or ``@typing.overload``."""
    for deco in node.decorator_list:
        if isinstance(deco, ast.Name) and deco.id == "overload":
            return True
        if (
            isinstance(deco, ast.Attribute)
            and deco.attr == "overload"
            and isinstance(deco.value, ast.Name)
        ):
            return True
    return False


def _public_members_with_missing_docstrings(source_path: Path) -> list[str]:
    """Return names of public top-level classes/functions without docstrings.

    Uses AST inspection — import-free, safe to run against any source file.
    Public names are those not starting with ``_``.
    ``@overload`` decorated stubs are excluded because the real implementation
    always carries the docstring.
    """
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    missing: list[str] = []
    seen: set[str] = set()  # track names so @overload stubs don't hide impl failures
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_overload(node):
            continue  # @overload stubs don't need docstrings; the implementation does
        docstring = ast.get_docstring(node)
        if (not docstring or not docstring.strip()) and node.name not in seen:
            missing.append(node.name)
        seen.add(node.name)

    return missing


def test_members_modules_public_classes_and_functions_have_docstrings() -> None:
    """Every public class/function in :members: documented modules must have a docstring.

    Parses docs/sphinx/modules.rst to find all ``.. automodule::`` directives
    rendered with ``:members:``, then AST-inspects each backing source file and
    reports any public top-level class or function without a non-empty docstring.
    """
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    members_modules = _parse_members_modules(modules_rst_text)

    failures: list[str] = []
    for rel_name in sorted(members_modules):
        source = _resolve_to_source(rel_name, _RALPH_ROOT)
        if source is None:
            # Covered by test_sphinx_modules_coverage; skip silently here.
            continue
        missing = _public_members_with_missing_docstrings(source)
        failures.extend(
            f"  ralph.{rel_name}.{name}  ({source.relative_to(_RALPH_ROOT.parent)})"
            for name in missing
        )

    assert not failures, (
        "The following public classes/functions in :members: documented modules "
        "are missing docstrings:\n"
        + "\n".join(failures)
        + "\n\nAdd docstrings explaining role/behavior, parameters, return values, "
        "and non-obvious side effects or lifecycle constraints."
    )
