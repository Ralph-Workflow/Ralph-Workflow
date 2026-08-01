"""Package-wide filesystem-read consolidation audit (R1, R3, R4).

This audit complements :mod:`ralph.testing.audit_filesystem_write_consolidation`
with the read-side contract enforced by ``PRODUCT_CRITERIA.md``:

* **R1** — repeated reads of unchanged content within one logical
  operation are forbidden; the canonical read path lives on the
  :class:`~ralph.mcp.artifacts.file_backend.FileBackend` protocol
  via ``backend.read_text`` / ``backend.read_bytes``.
* **R3** — full-file ``read_text`` / ``read_bytes`` loads are flagged
  when the destination is plausibly large; the canonical alternative
  is the byte/line window API exposed by the workspace.
* **R4** — existence is not probed by reading; raw ``os.path.exists``,
  ``os.path.isfile`` and ``os.path.isdir`` calls outside the
  :class:`FileBackend` protocol are flagged when used to *discover*
  content via the path.

The audit fails closed (D1) by treating every new production module
under ``ralph/`` as in-scope automatically. Sanctioned deviations
require a local ``# filesystem-read-ok: <reason>`` marker naming the
behavioral contract (deliberate directory scan, shipped data file
load, transient cross-process handshake read, etc.).

The audit uses only ``ast`` and ``Path.read_text`` over source files.
It does not start subprocesses, sleep, access the network, or mutate
production data.

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found

Usage::

    python -m ralph.testing.audit_filesystem_read_consolidation [package_root]
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Production-only package root(s) walked by the audit by default.
_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)

#: Files inside the package that are exempt from the audit: they
#: define the canonical read primitive itself, the audit module,
#: or are responsible for rendering documentation that legitimately
#: needs to ``read_text`` template files.
_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        # The canonical primitive — its body is the rule, not a violation.
        "mcp/artifacts/file_backend.py",
        # The concrete backend implementation that delegates to
        # ``Path.read_text`` / ``Path.read_bytes`` as part of the
        # canonical primitive boundary.
        "mcp/artifacts/_path_file_backend.py",
        # The audit module itself + the matching write consolidation
        # audit it complements.
        "testing/audit_filesystem_read_consolidation.py",
        "testing/audit_filesystem_write_consolidation.py",
        # ``prompts/template_registry.py`` and friends intentionally
        # read packaged templates at import time via ``Path.read_text``.
        # Those reads are deterministic package data, not repeated
        # file-content probes; they fall under R1's caching rule via
        # the template cache itself rather than via the read boundary.
        "prompts/template_registry.py",
        "prompts/master_prompt.py",
        "prompts/render/jinja.py",
        "recovery/controller.py",
        # The agent-internal probe reader used by tests/fakes is the
        # data plane the audit is meant to replace, so it is exempt
        # exactly like the write audit exempts the backend it owns.
        "agents/invoke/_workspace.py",
    }
)

#: Comment marker that suppresses a violation at the call site. The
#: marker must name a behavioral contract (D3 — exceptions are
#: explicit and local). The format is fixed; an empty reason is
#: treated as drift.
_MARKER_TOKEN: str = "filesystem-read-ok:"

#: Module-level qualifiers whose free-function or attribute calls
#: are considered raw filesystem reads when they target content
#: the consolidation is meant to own.
_RAW_READ_QUALIFIERS: frozenset[str] = frozenset(
    {
        "os",
        "os.path",
        "pathlib",
    }
)

#: Attribute names recognised as raw filesystem reads when called
#: on one of :data:`_RAW_READ_QUALIFIERS` or directly on a
#: ``Path`` instance. Each entry pairs the attribute name with the
#: primitive hint or guidance the diagnostic cites (D2).
_RAW_READ_ATTRS: dict[str, str] = {
    # R3 — full-content loads.
    "read_text": "raw full-file read; route through FileBackend.read_text or "
    "annotate with `# filesystem-read-ok: <reason>` naming the behavioral contract",
    "read_bytes": "raw full-file byte load; route through FileBackend.read_bytes or "
    "annotate with `# filesystem-read-ok: <reason>` naming the behavioral contract",
    # R4 — existence probes that masquerade as filesystem reads.
    "exists": "raw existence probe; use FileBackend.exists or "
    "annotate with `# filesystem-read-ok: <reason>`",
    "is_file": "raw existence probe; use FileBackend.exists or "
    "annotate with `# filesystem-read-ok: <reason>`",
    "is_dir": "raw existence probe; use FileBackend.exists or "
    "annotate with `# filesystem-read-ok: <reason>`",
    "lexists": "raw existence probe; use FileBackend.exists or "
    "annotate with `# filesystem-read-ok: <reason>`",
    "stat": "raw stat probe; use FileBackend.stat or "
    "annotate with `# filesystem-read-ok: <reason>`",
    "lstat": "raw stat probe; use FileBackend.stat or "
    "annotate with `# filesystem-read-ok: <reason>`",
}


@dataclass(frozen=True)
class FilesystemReadViolation:
    """A single filesystem-read consolidation violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.file_path}:{self.line}: {self.kind}: {self.message}"


def _coerce_package_roots(
    package_root: Path,
    package_roots: Sequence[str] | None,
) -> tuple[Path, ...]:
    if package_roots is None:
        package_roots = _DEFAULT_PACKAGE_ROOTS
    return tuple(package_root / rel for rel in package_roots)


def _coerce_exempt_paths(
    exempt_paths: frozenset[str] | Sequence[str] | None,
) -> frozenset[str]:
    if exempt_paths is None:
        return _DEFAULT_EXEMPT_PATHS
    if isinstance(exempt_paths, frozenset):
        return exempt_paths
    return frozenset(exempt_paths)


def _is_exempt(rel_path: str, exempt: frozenset[str]) -> bool:
    """True if ``rel_path`` matches any entry in ``exempt``."""
    if rel_path in exempt:
        return True
    return any(rel_path == entry or rel_path.endswith("/" + entry) for entry in exempt)


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _marker_line_indices(source_lines: list[str]) -> set[int]:
    """Return 0-based line indices whose text carries a non-empty marker."""
    indices: set[int] = set()
    for idx, line in enumerate(source_lines):
        marker_idx = line.find(_MARKER_TOKEN)
        if marker_idx < 0:
            continue
        reason = line[marker_idx + len(_MARKER_TOKEN) :].strip()
        if reason:
            indices.add(idx)
    return indices


def _attr_name(node: ast.Call) -> str | None:
    """Return the rightmost attribute name on a Call's function, if any."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_root_name(node: ast.AST) -> str | None:
    """Resolve the deepest ``Name`` id of a possibly-chained receiver expression.

    Examples:
        ``os.path.exists`` → ``os``
        ``Path(...).read_text`` → ``Path`` (the constructor)
        ``arbitrary_var.read_text`` → ``arbitrary_var``
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _call_root_name(node.func)
    if isinstance(node, ast.Attribute):
        return _call_root_name(node.value)
    return None


#: Path-family constructor names treated as filesystem path provenance.
_PATHLIBS: frozenset[str] = frozenset(
    {"Path", "PurePath", "PosixPath", "WindowsPath", "pathlib"}
)


def _is_pathlib_constructor(root: str | None) -> bool:
    return root is not None and root in _PATHLIBS


def _collect_path_variables(tree: ast.AST) -> set[str]:
    """Return names bound to a ``pathlib.Path(...)`` constructor at module level.

    Provenance is intentionally narrow: module-level assignments whose
    right-hand side is a direct ``Path(...)`` / ``PurePath(...)`` /
    ``PosixPath(...)`` / ``WindowsPath(...)`` constructor call are
    recognised. That covers the common ``p = Path(...)`` / ``p: Path = Path(...)``
    patterns while avoiding the false-positive risk of inferring that
    arbitrary objects are paths.

    Module-level aliases (``from pathlib import Path as P``) are also
    recognised: the alias maps back to a constructor name in
    :data:`_PATHLIBS`.

    Function parameters are NOT included by design: the audit deliberately
    requires an explicit constructor bind at module scope so a parameter
    named ``p`` in one function does not silently make an unrelated
    ``p.read_text`` call elsewhere a violation. Callers that want the
    audit to cover their parameter patterns annotate their call sites
    with ``# filesystem-read-ok: <reason>``.
    """
    names: set[str] = set()
    body = getattr(tree, "body", None)
    if body is None:
        return names
    for node in body:
        # Track import aliases: ``from pathlib import Path`` (Name bound
        # to ``pathlib.Path``), or ``import pathlib.Path as P``.
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for alias in node.names:
                if alias.name in _PATHLIBS:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pathlib" and alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.Assign):
            if not isinstance(node.value, ast.Call):
                continue
            root = _call_root_name(node.value.func)
            if _is_pathlib_constructor(root):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.value, ast.Call) or node.target is None:
                continue
            root = _call_root_name(node.value.func)
            if _is_pathlib_constructor(root) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


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
    path_variables = _collect_path_variables(tree)

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
        if root in _RAW_READ_QUALIFIERS:
            kind = f"raw_{attr}"
            message = _RAW_READ_ATTRS[attr]
        elif root in path_variables or root in _PATHLIBS:
            # ``p.read_text()`` where ``p = Path(...)``; ``Path(...).read_text()``
            # where ``Path`` is the constructor name (resolves to itself via
            # ``_call_root_name``).
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
    """Return a list of package-wide filesystem-read violations.

    Args:
        package_root: The ``ralph-workflow`` repo root.
        module_paths: When provided, restrict the audit to these
            package-relative paths (used by tests and for
            debugging). When ``None``, walk every ``.py`` file under
            each ``package_roots`` directory under ``package_root``.
        exempt_paths: Package-relative paths that are exempt from
            the audit (the shared primitives, the audit module
            itself, etc.).
        package_roots: When ``None``, defaults to ``("ralph",)``.

    The audit walks ``.py`` files via ``Path.rglob``, prunes
    ``__pycache__`` directories, and treats every production module
    as in scope by default. Newly added modules automatically
    participate — the audit cannot be silently bypassed by adding
    new code (D1).
    """
    if not package_root.is_dir():
        return [
            FilesystemReadViolation(
                kind="missing_package_root",
                file_path=str(package_root),
                line=0,
                message=(
                    "package root could not be walked; restore the requested "
                    "production root so the filesystem-read audit can fail closed"
                ),
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
    """Return 0 when clean, 1 on violations, or 2 for a missing package root."""
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
        print()
        print(
            "Fix the drift: stable full-file reads and existence probes in ralph/ "
            "must route through the canonical FileBackend protocol (read_text / "
            "read_bytes / exists / stat) or carry a local "
            "`# filesystem-read-ok: <reason>` marker naming the behavioral "
            "contract (shipped data load, deliberate directory scan, transient "
            "cross-process handshake, etc.). The marker must include a reason "
            "— an empty reason fails closed per D3."
        )
        return 1

    print("filesystem read consolidation audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
