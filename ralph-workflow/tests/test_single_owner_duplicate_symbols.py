from __future__ import annotations

import ast
import re
from functools import cache
from importlib.util import find_spec
from pathlib import Path

from ralph.pipeline.conflict_resolution.sight import ConflictKind

_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_PIPELINE_ROOT = _RALPH_ROOT / "pipeline"
_CONFLICT_KIND = "ConflictKind"
_CONFLICT_KIND_OWNER = "ralph/pipeline/conflict_resolution/_kind.py"
_AGENT_SELECTION_OWNER = "ralph/recovery/agent_selection.py"
_AGENT_SELECTION_SOURCE = "ralph.recovery.agent_selection"
_AGENT_SELECTION_API: tuple[str, ...] = (
    "AgentAvailability",
    "AgentSelection",
    "agent_availability",
    "format_selection_evidence",
    "select_preferred_agent",
)


def _module_paths(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts))


@cache
def _sources(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(_RALPH_ROOT.parent).as_posix(), path.read_bytes())
        for path in _module_paths(root)
    )


def _defined_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.ClassDef):
        return node.name
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name
    if isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
        return node.name.id
    return None


def _defining_modules(symbol: str) -> tuple[str, ...]:
    defining: list[str] = []
    escaped = re.escape(symbol)
    definition_shape = re.compile(rf"\b(?:class|def)\s+{escaped}\b|\b{escaped}\s*[:=]")
    needle = symbol.encode("utf-8")
    for relative, data in _sources(_RALPH_ROOT):
        if needle not in data:
            continue
        source = data.decode("utf-8")
        if not definition_shape.search(source):
            continue
        tree = ast.parse(source, filename=relative)
        defining.extend(relative for node in ast.walk(tree) if _defined_name(node) == symbol)
    return tuple(sorted(defining))


def _pipeline_agent_selection_reexports() -> tuple[str, ...]:
    reexports: list[str] = []
    needle = _AGENT_SELECTION_SOURCE.encode("utf-8")
    for relative, data in _sources(_PIPELINE_ROOT):
        if needle not in data:
            continue
        source = data.decode("utf-8")
        tree = ast.parse(source, filename=relative)
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or node.module != _AGENT_SELECTION_SOURCE:
                continue
            if any(alias.name in _AGENT_SELECTION_API for alias in node.names):
                reexports.append(relative)
    return tuple(sorted(reexports))


def test_conflict_kind_has_exactly_one_defining_module() -> None:
    defining = _defining_modules(_CONFLICT_KIND)
    assert defining == (_CONFLICT_KIND_OWNER,), (
        f"ConflictKind must be defined by exactly one module; found: {list(defining)}"
    )
    assert ConflictKind.__module__ == "ralph.pipeline.conflict_resolution._kind", (
        "the conflict-resolution sight module must expose the single ConflictKind owner"
    )


def test_agent_selection_api_has_exactly_one_defining_module() -> None:
    for symbol in _AGENT_SELECTION_API:
        defining = _defining_modules(symbol)
        assert defining == (_AGENT_SELECTION_OWNER,), (
            f"'{symbol}' must be defined by exactly one module; found: {list(defining)}"
        )


def test_pipeline_package_exposes_no_agent_selection_reexport_module() -> None:
    reexport_files = tuple(
        relative
        for relative, _source in _sources(_PIPELINE_ROOT)
        if Path(relative).name == "agent_selection.py"
    )
    assert not reexport_files, (
        "the pipeline package must not ship an agent-selection re-export module; found: "
        f"{list(reexport_files)}"
    )
    assert find_spec("ralph.pipeline.agent_selection") is None, (
        "ralph.pipeline.agent_selection must not be importable; "
        f"{_AGENT_SELECTION_OWNER} is the single agent-selection owner"
    )
    reexports = _pipeline_agent_selection_reexports()
    assert not reexports, (
        "no module under ralph/pipeline/ may re-export the agent-selection API; found: "
        f"{list(reexports)}"
    )
