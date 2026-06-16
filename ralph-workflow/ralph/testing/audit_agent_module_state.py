"""Static audit to prevent re-introduction of module-level agent state.

After the wt-016-consolidate-agent refactor, :class:`ralph.agents.catalog.AgentCatalog`
is the single source of truth for all agent-registration state (parsers,
custom-command registry, strategy dispatch).  This audit scans the
``ralph.agents`` subtree for any module-level dict that would re-introduce
a parallel state source, and fails CI when such a dict is found.

A module-level dict assignment is a violation when:

1. The assigned name starts with ``_PARSER_REGISTRY``,
   ``_CUSTOM_COMMAND_REGISTRY``, or ``_STRATEGY_DISPATCH`` (case-sensitive);
2. OR the assigned name contains both ``registry`` and one of
   (``agent``, ``parser``) substrings (case-sensitive);
3. AND the value is a dict literal (not a class declaration, not a
   ``MappingProxyType(...)`` view, not a frozenset/tuple/list);
4. AND the assignment is at module level (not inside a function or class);
5. AND the module is not in :data:`AUDIT_MODULE_STATE_ALLOWLIST`.

Usage:
    python -m ralph.testing.audit_agent_module_state [codebase_root]

Returns exit code 0 if no violations found, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from ralph.testing.audit_agent_registry_sync import RegistrySyncViolation

# Allowlist of (file_path, line) tuples that are explicitly permitted to
# carry module-level dict state.  Empty by default — populate by adding a
# documented justification in
# ``tests/test_audit_agent_module_state.py`` and an entry here.
AUDIT_MODULE_STATE_ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


# Subtrees of ralph-workflow/ that the audit scans.  Defined explicitly so
# the audit does not accidentally scan the full ralph-workflow/ tree (and
# the path filter keeps the audit well under the 1s budget cap).
_AUDIT_SUBTREES: tuple[str, ...] = (
    "ralph/agents",
    "ralph/agents/parsers",
    "ralph/agents/execution_state",
)


# Names that, when assigned at module level to a dict, are flagged by
# the audit.  These are the historical module-level state names that
# the refactor consolidated into :class:`AgentCatalog`.
_FORBIDDEN_NAME_PREFIXES: tuple[str, ...] = (
    "_PARSER_REGISTRY",
    "_CUSTOM_COMMAND_REGISTRY",
    "_STRATEGY_DISPATCH",
)


def _is_module_level_dict_assignment(
    node: ast.Assign | ast.AnnAssign,
) -> tuple[str, ast.expr] | None:
    """Return ``(name, value)`` if ``node`` is a module-level dict assignment.

    Handles both plain assignments (``_FOO = {...}``) and annotated
    assignments (``_FOO: dict[str, object] = {...}``).

    Returns ``None`` for class definitions, ``MappingProxyType(...)`` views,
    frozenset / tuple / list assignments, or non-dict assignments.
    """
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = list(node.targets)
        value: ast.expr | None = node.value
    else:
        targets = [node.target]
        value = node.value
    if value is None:
        return None
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if not isinstance(value, ast.Dict):
            continue
        return name, value
    return None


def _name_violates(name: str) -> bool:
    """Return True when ``name`` matches the forbidden patterns."""
    if any(name.startswith(prefix) for prefix in _FORBIDDEN_NAME_PREFIXES):
        return True
    lower = name.lower()
    return "registry" in lower and ("agent" in lower or "parser" in lower)


def _scan_file(content: str, rel_path: str) -> list[RegistrySyncViolation]:
    """Scan a single file for module-level dict violations."""
    violations: list[RegistrySyncViolation] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        violations.append(
            RegistrySyncViolation(rel_path, e.lineno or 1, "syntax_error", str(e))
        )
        return violations

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        result = _is_module_level_dict_assignment(node)
        if result is None:
            continue
        name, _value = result
        if not _name_violates(name):
            continue
        if (rel_path, node.lineno) in AUDIT_MODULE_STATE_ALLOWLIST:
            continue
        violations.append(
            RegistrySyncViolation(
                rel_path,
                node.lineno,
                "module_level_state",
                (
                    f"Module-level dict {name!r} re-introduces agent-registration "
                    f"state outside AgentCatalog. Use AgentCatalog instead."
                ),
            )
        )
    return violations


def _collect_py_files(root: Path) -> list[Path]:
    """Return all ``*.py`` files under any of the audit subtrees."""
    seen: set[Path] = set()
    files: list[Path] = []
    for subtree in _AUDIT_SUBTREES:
        base = root / subtree
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
    return sorted(files)


def run_audit(package_root: Path) -> list[RegistrySyncViolation]:
    """Run the audit and return the list of violations found.

    File-read failures are reported as ``file_read_error`` violations
    (NOT silently skipped) so the audit cannot be defeated by an
    unreadable file hiding a forbidden module-level dict reintroduction.
    The audit's role is prevention — a fail-closed stance on file reads
    preserves that contract.
    """
    violations: list[RegistrySyncViolation] = []
    for py_file in _collect_py_files(package_root):
        rel_path = str(py_file.relative_to(package_root))
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            violations.append(
                RegistrySyncViolation(
                    rel_path,
                    0,
                    "file_read_error",
                    f"Cannot read {py_file} for audit scan: {e}. "
                    f"An unreadable file could hide a forbidden module-level "
                    f"dict reintroduction, so the audit fails closed.",
                )
            )
            continue
        violations.extend(_scan_file(content, rel_path))
    return violations


def main() -> int:
    package_root = Path(__file__).parent.parent
    violations = run_audit(package_root)
    if violations:
        print("AGENT-MODULE-STATE Violations Found:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("AGENT-MODULE-STATE Audit: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
