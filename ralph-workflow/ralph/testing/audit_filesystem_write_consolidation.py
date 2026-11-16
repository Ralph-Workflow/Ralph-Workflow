from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_filesystem_write_exemptions import EXEMPT_CALL_SITES
from ralph.testing._filesystem_write_scan import FilesystemWriteViolation, _scan_module

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "EXEMPT_CALL_SITES",
    "FilesystemWriteViolation",
    "audit_filesystem_write_consolidation",
    "main",
]

_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)

_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
        "testing/audit_filesystem_write_consolidation.py",
    }
)


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
