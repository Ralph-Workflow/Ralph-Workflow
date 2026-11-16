from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_filesystem_write_exemptions import EXEMPT_CALL_SITES
from ralph.testing._audit_scope import build_parent_map_from_nodes, qualified_scope
from ralph.testing._filesystem_write_consolidation_rules import (
    _call_root_name,
    _import_aliases,
    _path_variable_names,
    _qualified_violation_message,
    _raw_builtin_open_call,
    _raw_direct_import_mutation_call,
    _raw_fdopen_call,
    _raw_qualified_mutation_call,
    _raw_write_text_call,
    _violation_message,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)
_MODE_POSITION: int = 1

_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
        "testing/audit_filesystem_write_consolidation.py",
    }
)


@dataclass(frozen=True)
class FilesystemWriteViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _coerce_exempt_paths(
    exempt_paths: frozenset[str] | Sequence[str] | None,
) -> frozenset[str]:
    if exempt_paths is None:
        return _DEFAULT_EXEMPT_PATHS
    return frozenset(exempt_paths)


def _coerce_package_roots(
    package_root: Path,
    package_roots: Sequence[str] | None,
) -> list[Path]:
    if package_roots is None:
        if package_root.name == "ralph":
            return [package_root]
        package_roots = _DEFAULT_PACKAGE_ROOTS
    if not package_roots:
        return [package_root]
    return [package_root / str(rel) for rel in package_roots]


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if "__pycache__" in path.parts or "testing" in relative_parts:
            continue
        if not path.is_file():
            continue
        result.append(path)
    return result


def _parse_candidate_module(
    source: str, module_path: Path, rel_path: str
) -> ast.Module | FilesystemWriteViolation:
    try:
        return ast.parse(source, filename=str(module_path))
    except SyntaxError as exc:
        return FilesystemWriteViolation(
            kind="invalid_module",
            file_path=rel_path,
            line=exc.lineno or 0,
            message=(
                "module could not be parsed; restore valid source before the "
                "filesystem-write audit can evaluate it"
            ),
        )


def _read_candidate_source(module_path: Path, rel_path: str) -> str | FilesystemWriteViolation:
    try:
        return module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return FilesystemWriteViolation(
            kind="unreadable_module",
            file_path=rel_path,
            line=0,
            message=(
                "module could not be read; restore readable source and ensure the audit can walk it"
            ),
        )


def _prepare_module(
    module_path: Path, rel_path: str
) -> tuple[ast.Module, str] | list[FilesystemWriteViolation]:
    source_or_violation = _read_candidate_source(module_path, rel_path)
    if isinstance(source_or_violation, FilesystemWriteViolation):
        return [source_or_violation]
    tree_or_violation = _parse_candidate_module(source_or_violation, module_path, rel_path)
    if isinstance(tree_or_violation, FilesystemWriteViolation):
        return [tree_or_violation]
    return tree_or_violation, source_or_violation


def _scan_module(
    module_path: Path,
    rel_path: str,
) -> list[FilesystemWriteViolation]:
    prepared = _prepare_module(module_path, rel_path)
    if isinstance(prepared, list):
        return prepared
    tree, _source = prepared

    nodes = list(ast.walk(tree))
    parents = build_parent_map_from_nodes(nodes)
    (
        module_aliases,
        direct_mutations,
        direct_open_aliases,
        open_module_aliases,
        direct_fdopen_aliases,
        fdopen_module_aliases,
    ) = _import_aliases(nodes)
    path_variables = _path_variable_names(nodes, module_aliases, parents)
    violations: list[tuple[FilesystemWriteViolation, ast.Call]] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        attr = _raw_write_text_call(node)
        if attr is not None:
            violation = FilesystemWriteViolation(
                kind="raw_write_text",
                file_path=rel_path,
                line=node.lineno,
                message=_violation_message(attr),
            )
            violations.append((violation, node))
            continue
        if _raw_fdopen_call(node, direct_fdopen_aliases, fdopen_module_aliases):
            violations.append(
                (
                    FilesystemWriteViolation(
                        kind="raw_fdopen_write",
                        file_path=rel_path,
                        line=node.lineno,
                        message=(
                            "raw os.fdopen() with a write/append mode bypasses the "
                            "filesystem activity guard; route stable content through "
                            "ralph.mcp.artifacts.idempotent_write, or register the call "
                            "site in EXEMPT_CALL_SITES naming the transient or "
                            "append-stream lifecycle contract"
                        ),
                    ),
                    node,
                )
            )
            continue
        if _raw_builtin_open_call(node, direct_open_aliases, open_module_aliases):
            violations.append(
                (
                    FilesystemWriteViolation(
                        kind="raw_open_write",
                        file_path=rel_path,
                        line=node.lineno,
                        message=(
                            "raw builtin open() with a write/append mode bypasses the "
                            "stable-path mutation guard; route through "
                            "ralph.mcp.artifacts.idempotent_write "
                            "(write_text_if_changed / atomic_write_text_if_changed / "
                            "write_bytes_if_changed / atomic_write_bytes_if_changed), "
                            "or register the call site in EXEMPT_CALL_SITES naming the "
                            "behavioral contract (deliberate binary append, "
                            "deliberately timestamped, atomic-create, etc.)"
                        ),
                    ),
                    node,
                )
            )
            continue
        direct_violation = _direct_import_violation(node, direct_mutations, rel_path)
        if direct_violation is not None:
            violations.append((direct_violation, node))
            continue
        qattr = _raw_qualified_mutation_call(node, module_aliases, path_variables, parents)
        if qattr is not None:
            qualifier = _call_receiver_root(node) or "?"
            violations.append(
                (
                    FilesystemWriteViolation(
                        kind=f"raw_{qattr}",
                        file_path=rel_path,
                        line=node.lineno,
                        message=_qualified_violation_message(qualifier, qattr),
                    ),
                    node,
                )
            )
    return _registered_violations(violations, rel_path, parents)


def _registered_violations(
    violations: list[tuple[FilesystemWriteViolation, ast.Call]],
    rel_path: str,
    parents: dict[ast.AST, ast.AST],
) -> list[FilesystemWriteViolation]:
    kept: list[FilesystemWriteViolation] = []
    for violation, node in violations:
        scope = qualified_scope(node, parents)
        if (rel_path, scope, violation.kind) in EXEMPT_CALL_SITES:
            continue
        kept.append(violation)
    return kept


def _call_receiver_root(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Attribute):
        return None
    return _call_root_name(node.func.value)


def _direct_import_violation(
    node: ast.Call,
    direct_mutations: dict[str, str],
    rel_path: str,
) -> FilesystemWriteViolation | None:
    direct_attr = _raw_direct_import_mutation_call(node, direct_mutations)
    if direct_attr is None:
        return None
    return FilesystemWriteViolation(
        kind=f"raw_{direct_attr}",
        file_path=rel_path,
        line=node.lineno,
        message=_qualified_violation_message("import", direct_attr),
    )


def audit_filesystem_write_consolidation(
    package_root: Path,
    *,
    module_paths: Sequence[str] | None = None,
    exempt_paths: frozenset[str] | Sequence[str] | None = None,
    package_roots: Sequence[str] | None = None,
) -> list[FilesystemWriteViolation]:
    if not package_root.is_dir():
        return [
            FilesystemWriteViolation(
                kind="missing_package_root",
                file_path=str(package_root),
                line=0,
                message=(
                    "package root could not be walked; restore the requested production "
                    "root so the filesystem-write audit can fail closed"
                ),
            )
        ]

    exempt = _coerce_exempt_paths(exempt_paths)
    roots = _coerce_package_roots(package_root, package_roots)

    if module_paths is None:
        missing_roots = [root for root in roots if not root.is_dir()]
        if missing_roots:
            return [
                FilesystemWriteViolation(
                    kind="missing_production_root",
                    file_path=root.relative_to(package_root).as_posix(),
                    line=0,
                    message=(
                        "expected production root could not be walked; restore it so "
                        "the filesystem-write audit can fail closed"
                    ),
                )
                for root in missing_roots
            ]
    violations: list[FilesystemWriteViolation] = []
    if module_paths is not None:
        candidates: list[tuple[Path, str]] = []
        resolved_root = package_root.resolve()
        for rel_path in module_paths:
            candidate = package_root / rel_path
            try:
                candidate.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                violations.append(
                    FilesystemWriteViolation(
                        kind="invalid_module_path",
                        file_path=rel_path,
                        line=0,
                        message=(
                            "explicit module path escapes the package root; pass a relative "
                            "production-module path so the filesystem-write audit can fail closed"
                        ),
                    )
                )
                continue
            candidates.append((candidate, rel_path))
    else:
        candidates = []
        for root in roots:
            for path in _collect_python_files(root):
                rel = path.relative_to(package_root).as_posix()
                candidates.append((path, rel))
    for module_path, rel_path in candidates:
        if _is_exempt(rel_path, exempt):
            continue
        violations.extend(_scan_module(module_path, rel_path))
    return violations


def _is_exempt(rel_path: str, exempt: frozenset[str]) -> bool:
    if rel_path in exempt:
        return True
    return any(rel_path == entry or rel_path.endswith("/" + entry) for entry in exempt)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent.parent
    if not package_root.is_dir():
        sys.stderr.write(f"Package root not found: {package_root}\n")
        return 2

    violations = audit_filesystem_write_consolidation(package_root)
    if violations:
        sys.stdout.write(f"FILESYSTEM WRITE CONSOLIDATION VIOLATIONS: {len(violations)}\n")
        sys.stdout.write(str("=" * 72) + "\n")
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        sys.stdout.write("\n")
        sys.stdout.write(
            "Fix the drift: stable full-file writes in ralph/ must route through ralph.mcp.artifacts.idempotent_write (write_text_if_changed or atomic_write_text_if_changed), or be registered in EXEMPT_CALL_SITES as (file, qualified callable, kind) naming the behavioral contract (transient scratch, deliberately timestamped, append-only stream, etc.).\n"
        )
        return 1

    sys.stdout.write("filesystem write consolidation audit: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
