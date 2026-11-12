from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "public member docstrings": (
        "the comment-free source policy bans docstrings in source, so the public "
        "members of :members: documented modules must carry none; the Sphinx API "
        "reference renders their signatures"
    ),
}
_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"


def _parse_members_modules(modules_rst_text: str) -> set[str]:
    members_modules: set[str] = set()

    block_re = re.compile(
        r"^\.\. automodule::\s+(\S+)(.*?)(?=^\.\. automodule::|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for match in block_re.finditer(modules_rst_text):
        module_name = match.group(1).strip()
        option_block = match.group(2)

        has_members = bool(re.search(r"^\s+:members:", option_block, re.MULTILINE))
        has_no_members = bool(re.search(r"^\s+:no-members:", option_block, re.MULTILINE))

        if has_members and not has_no_members:
            if module_name.startswith("ralph."):
                rel = module_name[len("ralph.") :]
            elif module_name == "ralph":
                rel = ""
            else:
                rel = module_name
            if rel:
                members_modules.add(rel)

    return members_modules


def _resolve_to_source(rel_name: str, ralph_root: Path) -> Path | None:
    parts = rel_name.split(".")
    leaf = ralph_root.joinpath(*parts[:-1], parts[-1] + ".py")
    if leaf.exists():
        return leaf
    pkg = ralph_root.joinpath(*parts, "__init__.py")
    if pkg.exists():
        return pkg
    return None


@cache
def _public_members_with_docstrings(source_path: Path) -> list[str]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        docstring = ast.get_docstring(node)
        if docstring and docstring.strip():
            found.append(node.name)

    return found


@cache
def _members_modules() -> set[str]:
    return _parse_members_modules(_MODULES_RST.read_text(encoding="utf-8"))


for _rel in sorted(_members_modules()):
    _src = _resolve_to_source(_rel, _RALPH_ROOT)
    if _src is not None:
        _public_members_with_docstrings(_src)


def test_members_modules_public_classes_and_functions_have_no_docstrings() -> None:
    members_modules = _members_modules()

    assert members_modules, "no :members: documented module found in docs/sphinx/modules.rst"

    failures: list[str] = []
    for rel_name in sorted(members_modules):
        source = _resolve_to_source(rel_name, _RALPH_ROOT)
        if source is None:
            continue
        offenders = _public_members_with_docstrings(source)
        failures.extend(
            f"  ralph.{rel_name}.{name}  ({source.relative_to(_RALPH_ROOT.parent)})"
            for name in offenders
        )

    assert not failures, (
        "The following public classes/functions in :members: documented modules "
        "carry a docstring; source files carry no docstrings:\n"
        + "\n".join(failures)
        + "\n\nDelete the docstring; the contract lives in names, type annotations, "
        "and tests."
    )
