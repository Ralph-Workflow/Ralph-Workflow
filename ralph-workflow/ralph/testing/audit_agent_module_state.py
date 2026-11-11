from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

from ralph.testing.audit_agent_registry_sync import RegistrySyncViolation

AUDIT_MODULE_STATE_ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


_AUDIT_SUBTREES: tuple[str, ...] = (
    "ralph/agents",
    "ralph/agents/parsers",
    "ralph/agents/execution_state",
)


_FORBIDDEN_NAME_PREFIXES: tuple[str, ...] = (
    "_PARSER_REGISTRY",
    "_CUSTOM_COMMAND_REGISTRY",
    "_STRATEGY_DISPATCH",
)


def _is_module_level_dict_assignment(
    node: ast.Assign | ast.AnnAssign,
) -> tuple[str, ast.expr] | None:
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
    if any(name.startswith(prefix) for prefix in _FORBIDDEN_NAME_PREFIXES):
        return True
    lower = name.lower()
    return "registry" in lower and ("agent" in lower or "parser" in lower)


_CANDIDATE_NAME_RE = re.compile(
    r"_PARSER_REGISTRY|_CUSTOM_COMMAND_REGISTRY|_STRATEGY_DISPATCH|"
    r"registry[_\w]*agent|agent[_\w]*registry|"
    r"registry[_\w]*parser|parser[_\w]*registry",
    re.IGNORECASE,
)


def _has_candidate_name(content: str) -> bool:
    return _CANDIDATE_NAME_RE.search(content) is not None


def _scan_file(content: str, rel_path: str) -> list[RegistrySyncViolation]:
    if not _has_candidate_name(content):
        return []
    violations: list[RegistrySyncViolation] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        violations.append(RegistrySyncViolation(rel_path, e.lineno or 1, "syntax_error", str(e)))
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
    seen: set[Path] = set()
    files: list[Path] = []
    for subtree in _AUDIT_SUBTREES:
        base = root / subtree
        if not base.is_dir():
            continue
        for parent, dirs, fs in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in fs:
                if not name.endswith(".py"):
                    continue
                path = Path(parent) / name
                if path in seen:
                    continue
                seen.add(path)
                files.append(path)
    return sorted(files)


def run_audit(package_root: Path) -> list[RegistrySyncViolation]:
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
