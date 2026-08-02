"""Package-wide filesystem-read and traversal consolidation audit (R1-R5).

New production modules fail closed when they use raw content reads, probes, or
traversals instead of the typed FileBackend and Workspace observation seams.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)
_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
        "testing/audit_filesystem_read_consolidation.py",
        "testing/audit_filesystem_write_consolidation.py",
        "prompts/template_registry.py",
        "prompts/master_prompt.py",
        "prompts/render/jinja.py",
        "agents/invoke/_workspace.py",
    }
)
_MARKER_TOKEN: str = "filesystem-read-ok:"
_RAW_READ_QUALIFIERS: frozenset[str] = frozenset({"os", "os.path", "pathlib"})
_RAW_READ_ATTRS: dict[str, str] = {
    "read_text": "R1/R3 raw full-file read; route through FileBackend.read_text or annotate",
    "read_bytes": "R1/R3 raw full-file byte load; route through FileBackend.read_bytes",
    "exists": "R4 raw existence probe; use FileBackend.exists",
    "is_file": "R4 raw existence probe; use FileBackend.exists",
    "is_dir": "R4 raw existence probe; use FileBackend.exists",
    "lexists": "R4 raw existence probe; use FileBackend.exists",
    "stat": "R4 raw stat probe; use FileBackend.stat",
    "lstat": "R4 raw stat probe; use FileBackend.stat",
    "iterdir": "R2 raw directory traversal; use Workspace.list_dir",
    "glob": "R2 raw directory traversal; use Workspace.iter_files",
    "rglob": "R2 raw recursive traversal; use Workspace.iter_files",
    "walk": "R2 raw recursive traversal; use Workspace.iter_files",
    "scandir": "R2 raw directory traversal; use Workspace.list_dir",
    "iglob": "R2 raw recursive traversal; use Workspace.iter_files",
}
_PATHLIBS: frozenset[str] = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "pathlib"})
_OS_TRAVERSAL_ATTRS: frozenset[str] = frozenset({"walk", "scandir"})
_GLOB_TRAVERSAL_ATTRS: frozenset[str] = frozenset({"glob", "iglob"})


@dataclass(frozen=True)
class FilesystemReadViolation:
    """One filesystem-read consolidation violation surfaced by the audit.

    Attributes:
        kind: Short violation tag (e.g. ``raw_path_read_text``). Drives
            remediation guidance and stable diagnostics.
        file_path: POSIX path of the offending module relative to the
            package root passed to :func:`audit_filesystem_read_consolidation`.
        line: 1-based source line of the flagged call. ``0`` when the
            violation refers to the package root itself (missing or
            unreadable).
        message: Human-readable description naming the violated
            criterion and the approved replacement seam (D2).
    """

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        """Return the canonical ``path:line: kind: message`` diagnostic line."""
        return f"{self.file_path}:{self.line}: {self.kind}: {self.message}"


def _attr_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_root_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _call_root_name(node.func)
    if isinstance(node, ast.Attribute):
        return _call_root_name(node.value)
    return None


def _import_alias_names(
    tree: ast.Module,
    path_names: set[str],
    os_names: set[str],
    glob_names: set[str],
) -> None:
    """Record pathlib constructors and filesystem-module aliases (in-place)."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for alias in node.names:
                if alias.name in _PATHLIBS:
                    path_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pathlib":
                    path_names.add(alias.asname or alias.name)
                elif alias.name == "os":
                    os_names.add(alias.asname or alias.name)
                elif alias.name == "glob":
                    glob_names.add(alias.asname or alias.name)


def _assign_target_names(value: ast.expr, target: ast.expr, names: set[str]) -> None:
    """Record an ast.Name target when the right-hand side is a path constructor."""
    if not isinstance(value, ast.Call):
        return
    root = _call_root_name(value.func)
    if root is None or root not in _PATHLIBS:
        return
    if isinstance(target, ast.Name):
        names.add(target.id)


def _collect_read_provenance(tree: ast.Module) -> tuple[set[str], set[str], set[str]]:
    """Return known pathlib values and filesystem-module aliases for a module."""
    path_names: set[str] = set()
    os_names: set[str] = set()
    glob_names: set[str] = set()
    _import_alias_names(tree, path_names, os_names, glob_names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _assign_target_names(node.value, target, path_names)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.expr):
            _assign_target_names(node.value, node.target, path_names)
    return path_names, os_names, glob_names


def _marker_line_indices(source_lines: list[str]) -> set[int]:
    indices: set[int] = set()
    for idx, line in enumerate(source_lines):
        marker_idx = line.find(_MARKER_TOKEN)
        if marker_idx < 0:
            continue
        reason = line[marker_idx + len(_MARKER_TOKEN) :].strip()
        if reason:
            indices.add(idx)
    return indices


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_exempt(rel_path: str, exempt: frozenset[str]) -> bool:
    if rel_path in exempt:
        return True
    return any(rel_path == entry or rel_path.endswith("/" + entry) for entry in exempt)


def _coerce_exempt_paths(exempt_paths: frozenset[str] | Sequence[str] | None) -> frozenset[str]:
    if exempt_paths is None:
        return _DEFAULT_EXEMPT_PATHS
    if isinstance(exempt_paths, frozenset):
        return exempt_paths
    return frozenset(exempt_paths)


def _coerce_package_roots(
    package_root: Path,
    package_roots: Sequence[str] | None,
) -> tuple[Path, ...]:
    if package_roots is None:
        package_roots = _DEFAULT_PACKAGE_ROOTS
    return tuple(package_root / rel for rel in package_roots)


def _scan_module(module_path: Path, rel_path: str) -> list[FilesystemReadViolation]:
    try:
        source = module_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            FilesystemReadViolation(
                kind="unreadable_module",
                file_path=rel_path,
                line=0,
                message=f"module could not be read ({exc}); unreadable modules fail closed",
            )
        ]
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            FilesystemReadViolation(
                kind="invalid_module",
                file_path=rel_path,
                line=exc.lineno or 1,
                message=f"module could not be parsed ({exc.msg}); invalid modules fail closed",
            )
        ]
    source_lines = source.splitlines()
    marker_lines = _marker_line_indices(source_lines)
    path_variables, os_names, glob_names = _collect_read_provenance(tree)

    violations: list[FilesystemReadViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = _attr_name(node)
        if attr is None or attr not in _RAW_READ_ATTRS:
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        root = _call_root_name(node.func.value)
        if root is None:
            continue
        line_idx = node.lineno
        zero_based = line_idx - 1
        if zero_based in marker_lines or (zero_based - 1) in marker_lines:
            continue
        is_os_traversal = attr in _OS_TRAVERSAL_ATTRS and root in os_names
        is_glob_traversal = attr in _GLOB_TRAVERSAL_ATTRS and root in glob_names
        is_raw_qualifier = root in _RAW_READ_QUALIFIERS
        is_path_traversal = attr in {"iterdir", "glob", "rglob"} and (
            root in path_variables or root in _PATHLIBS
        )
        if is_raw_qualifier or is_os_traversal or is_glob_traversal:
            kind = f"raw_{attr}"
            message = _RAW_READ_ATTRS[attr]
        elif root in path_variables or root in _PATHLIBS:
            if attr in _OS_TRAVERSAL_ATTRS and not is_os_traversal:
                continue
            if attr in _GLOB_TRAVERSAL_ATTRS and not (is_path_traversal or is_glob_traversal):
                continue
            if attr in {"iterdir", "glob", "rglob"} and not is_path_traversal:
                continue
            kind = f"raw_path_{attr}"
            message = _RAW_READ_ATTRS[attr]
        else:
            continue
        violations.append(
            FilesystemReadViolation(
                kind=kind,
                file_path=rel_path,
                line=line_idx,
                message=message,
            )
        )
    return violations


def audit_filesystem_read_consolidation(
    package_root: Path,
    *,
    module_paths: Sequence[str] | None = None,
    exempt_paths: frozenset[str] | Sequence[str] | None = None,
    package_roots: Sequence[str] | None = None,
) -> list[FilesystemReadViolation]:
    """Walk ``package_root`` and report raw filesystem-read call sites.

    The audit is AST + ``Path.read_text`` only — no subprocess, no
    ``sleep``, no real I/O beyond the one targeted source read per
    module. Every newly-added Python module under ``package_root``
    participates automatically (D1); there is no module allowlist to
    maintain. Diagnostics name the violated criterion (R1/R3/R4) and
    the approved replacement seam (D2).

    Args:
        package_root: Workspace directory whose ``ralph/`` subtree is
            walked. The first path that is not a directory produces a
            single ``missing_package_root`` violation so the caller can
            fail closed without raising.
        module_paths: Optional explicit list of relative POSIX paths
            restricting the walk. When ``None`` every ``.py`` file
            under ``package_roots`` is scanned.
        exempt_paths: Optional explicit POSIX path set whose violations
            are suppressed. Defaults to
            :data:`_DEFAULT_EXEMPT_PATHS` (canonical FileBackend,
            registry/render modules, and the audit module itself).
        package_roots: Optional relative subpath list whose
            ``.py`` descendants are scanned. Defaults to
            :data:`_DEFAULT_PACKAGE_ROOTS` (single ``"ralph"`` entry).

    Returns:
        A list of :class:`FilesystemReadViolation` records. Empty list
        means the scanned tree satisfies the read consolidation
        contract.
    """
    if not package_root.is_dir():
        return [
            FilesystemReadViolation(
                kind="missing_package_root",
                file_path=str(package_root),
                line=0,
                message="package root could not be walked",
            )
        ]
    exempt = _coerce_exempt_paths(exempt_paths)
    roots = _coerce_package_roots(package_root, package_roots)
    if module_paths is not None:
        candidates = [(package_root / rel, rel) for rel in module_paths]
    else:
        candidates = []
        for root in roots:
            for path in _collect_python_files(root):
                rel = path.relative_to(package_root).as_posix()
                candidates.append((path, rel))
    violations: list[FilesystemReadViolation] = []
    for module_path, rel_path in candidates:
        if _is_exempt(rel_path, exempt):
            continue
        violations.extend(_scan_module(module_path, rel_path))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the filesystem-read consolidation audit.

    Args:
        argv: Optional positional list whose first element overrides
            the default package root (``<repo>/ralph-workflow``).
            Empty ``argv`` walks the package that contains this
            module.

    Returns:
        ``0`` when the scanned tree satisfies the consolidation
        contract, ``1`` when one or more violations were reported
        (the violations are printed to ``stdout``), or ``2`` when
        the resolved package root is not a directory.
    """
    if argv is None:
        argv = sys.argv[1:]
    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent.parent
    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2
    violations = audit_filesystem_read_consolidation(package_root)
    if violations:
        print(f"FILESYSTEM READ CONSOLIDATION VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("filesystem read consolidation audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
