"""Package-wide filesystem-write consolidation audit.

Replaces the curated allowlist of ``audit_idempotent_write_adoption.py``
with a fail-closed, package-wide AST walk that rejects every raw
filesystem mutation under ``ralph/`` unless the call site carries
an explicit ``# filesystem-write-ok: <reason>`` marker naming the
behavioral contract (transient scratch, deliberately timestamped,
genuine append-only stream, etc.).

The audit walks every ``.py`` file under ``package_root`` by
default, prunes only the audit / testing / vendor roots, and treats
newly added production modules as in scope automatically. Sanctioned
deviations require a local marker; no blanket path allowlists.

The audit detects the following raw mutation classes:

  * ``Path.write_text`` / ``Path.write_bytes`` (raw full-file overwrite)
  * ``open(path, mode)`` with a write/append/extend mode
    (``"w"``, ``"a"``, ``"x"``, ``"wb"``, ``"ab"``, ``"r+"``, ``"w+"``,
    ``"a+"`` and their ``b``/``t`` variants)
  * ``os.replace`` / ``os.rename`` / ``os.renames`` / ``Path.replace`` /
    ``Path.rename`` (atomic moves)
  * ``Path.unlink`` / ``os.remove`` / ``os.unlink`` / ``Path.rmdir``
    (raw deletes)
  * ``Path.mkdir`` / ``os.mkdir`` / ``os.makedirs`` (raw directory
    creation)
  * ``shutil.rmtree`` / ``shutil.copy`` / ``shutil.copy2`` /
    ``shutil.copyfile`` / ``shutil.copymode`` / ``shutil.move``
    (raw copies / moves / tree deletes)
  * ``os.fsync`` / ``os.sync`` (raw durability barriers outside
    the canonical primitive)
  * ``Path.touch`` (raw mtime bumps)
  * ``truncate`` (raw truncation)

The audit uses only ``ast`` and ``Path.read_text`` over source
files. It does not start subprocesses, sleep, access the network,
or mutate production data.

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found

Usage::

    python -m ralph.testing.audit_filesystem_write_consolidation [package_root]

References:
    docs/agents/filesystem-lifecycle.md for the consolidation contract.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Module roots that always participate in the package-wide walk.
#: These are the production-only roots; we deliberately exclude
#: testing / audit / vendor / generated paths.
_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)
_MODE_POSITION: int = 1

#: Files inside the package that are exempt from the audit: they
#: define the shared primitive itself, or are responsible for
#: rendering the consolidation's own documentation and config.
#: Each entry is a path relative to the package root that is being
#: walked, in POSIX form (e.g. ``"mcp/artifacts/idempotent_write.py"``).
_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        # The shared primitives — they contain ``write_text`` calls
        # by design (they wrap the lower-level ``backend.write_text``
        # / ``backend.replace`` interface) and are the approved
        # destination for all consolidated writes.
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        # The concrete backend implementation that uses ``os.fsync``,
        # ``Path.replace``, ``Path.unlink`` etc. as part of the
        # canonical primitive boundary. Anything inside
        # ``_path_file_backend.py`` IS the abstraction.
        "mcp/artifacts/_path_file_backend.py",
        # The audit module itself + the curated audit module that
        # this one replaces.
        "testing/audit_filesystem_write_consolidation.py",
        "testing/audit_idempotent_write_adoption.py",
    }
)

#: Comment marker that suppresses a violation at the call site. The
#: marker must name a behavioral contract (D3 — exceptions are
#: explicit and local). The format is fixed; an empty reason is
#: treated as drift.
_MARKER_TOKEN = "filesystem-write-ok:"
_OPEN_MODE_POSITION = 1
_OPEN_MODE_MIN_ARGS = 2

#: Attribute names whose receiver is already routed through the canonical
#: ``FileBackend`` abstraction. When the audit sees ``backend.write_text(...)``
#: or ``self.write_text(...)`` (where ``self`` is a ``FileBackend`` subclass),
#: the write is already inside the abstraction boundary — the canonical
#: primitive lives in ``mcp/artifacts/idempotent_write.py`` and the call
#: itself is not a raw bypass. The audit must not flag these.
_ALREADY_ROUTED_RECEIVERS: frozenset[str] = frozenset(
    {
        "backend",
        "self",
        "cls",
    }
)

#: Module-level qualifiers (typically ``shutil``, ``os``, ``pathlib``)
#: whose free-function calls are considered raw filesystem mutation
#: when they target writes/mutations we want the consolidation to
#: own. These qualifiers are matched against the AST ``ast.Call``
#: whose function is an ``ast.Attribute`` whose value is an
#: ``ast.Name`` whose ``id`` is in this set.
_RAW_MUTATION_QUALIFIERS: frozenset[str] = frozenset(
    {
        "os",
        "shutil",
        "pathlib",
    }
)

#: Attribute names that are recognised as raw filesystem mutation
#: when called on one of :data:`_RAW_MUTATION_QUALIFIERS`. Each entry
#: pairs the attribute name with the primitive hint or guidance the
#: diagnostic cites (D2 — actionable, not merely "a check failed").
#:
#: ``open`` is special: only flag when the first argument is a
#: write-mode constant; see :func:`_is_write_mode_open`.
_RAW_MUTATION_ATTRS: dict[str, str] = {
    "replace": "raw atomic-replace bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "rename": "raw rename bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "renames": "raw renames bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "unlink": "raw unlink bypasses the canonical delete primitive; "
    "delete only under a `# filesystem-write-ok: <reason>` marker",
    "remove": "raw os.remove bypasses the canonical delete primitive; "
    "delete only under a `# filesystem-write-ok: <reason>` marker",
    "mkdir": "raw mkdir bypasses the canonical directory creation primitive; "
    "use workspace.fs.mkdirs or annotate the call",
    "makedirs": "raw makedirs bypasses the canonical directory creation primitive; "
    "use workspace.fs.mkdirs or annotate the call",
    "rmdir": "raw rmdir bypasses the canonical delete primitive",
    "rmtree": "raw rmtree bypasses the canonical delete primitive",
    "fsync": "raw fsync bypasses the canonical durability primitive; "
    "use ralph.mcp.artifacts.idempotent_write or mark with a reason",
    "sync": "raw os.sync bypasses the canonical durability primitive; "
    "use ralph.mcp.artifacts.idempotent_write or mark with a reason",
    "copy": "raw shutil.copy bypasses the canonical copy primitive; "
    "use ralph.mcp.artifacts.idempotent_write.copy_file_if_changed or mark",
    "copy2": "raw shutil.copy2 bypasses the canonical copy primitive; "
    "use ralph.mcp.artifacts.idempotent_write.copy_file_if_changed or mark",
    "copyfile": "raw shutil.copyfile bypasses the canonical copy primitive; "
    "use ralph.mcp.artifacts.idempotent_write.copy_file_if_changed or mark",
    "copymode": "raw shutil.copymode bypasses the canonical copy primitive; "
    "mark with a reason or route through the canonical primitive",
    "move": "raw shutil.move bypasses the canonical move primitive; "
    "use ralph.mcp.artifacts.idempotent_write.replace_if_changed or mark",
    "touch": "raw touch bumps mtime without content change; "
    "annotate with `# filesystem-write-ok: <reason>` or remove the call",
    "truncate": "raw truncate bypasses the canonical truncation primitive; "
    "mark with a reason or route through the canonical primitive",
}


@dataclass(frozen=True)
class FilesystemWriteViolation:
    """A single filesystem-write consolidation audit violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _coerce_exempt_paths(
    exempt_paths: frozenset[str] | Sequence[str] | None,
) -> frozenset[str]:
    """Coerce a caller-supplied exempt set into the canonical frozenset."""
    if exempt_paths is None:
        return _DEFAULT_EXEMPT_PATHS
    return frozenset(exempt_paths)


def _coerce_package_roots(
    package_root: Path,
    package_roots: Sequence[str] | None,
) -> list[Path]:
    """Resolve the package-relative roots to absolute Paths under package_root.

    When ``package_roots`` is empty, the package_root itself is the
    walk target — the audit walks every ``.py`` file directly under
    ``package_root``. When ``package_roots`` is supplied, the walk
    targets each ``package_root / rel`` directory.
    """
    if package_roots is None:
        # ``main(["ralph"])`` receives the package directory itself;
        # whereas callers scanning a repository root provide a sibling
        # ``ralph/`` directory. Support both public invocation forms.
        if package_root.name == "ralph":
            return [package_root]
        package_roots = _DEFAULT_PACKAGE_ROOTS
    if not package_roots:
        return [package_root]
    return [package_root / str(rel) for rel in package_roots]


def _collect_python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under ``root`` (recursive), sorted.

    ``__pycache__`` directories are pruned so stale ``.pyc`` files
    don't pollute the walk.
    """
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if not path.is_file():
            continue
        result.append(path)
    return result


def _is_write_mode_open(call: ast.Call, *, path_method: bool = False) -> bool:
    """True if ``call`` opens a file in a write/append/update mode.

    Builtin ``open(path, mode)`` takes its mode as the second positional
    argument; ``Path.open(mode)`` takes it as the first.  When the mode is
    dynamic the audit fails closed, while omitted modes preserve Python's
    read-only default.
    """
    mode_position = 0 if path_method else _OPEN_MODE_POSITION
    minimum_args = 1 if path_method else _OPEN_MODE_MIN_ARGS
    mode_arg: ast.expr | None = call.args[mode_position] if len(call.args) >= minimum_args else None
    if mode_arg is None:
        mode_arg = next((keyword.value for keyword in call.keywords if keyword.arg == "mode"), None)
    if mode_arg is None:
        return False  # builtin open() defaults to read mode.
    if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, str):
        return True  # Unknown mode fails closed.
    return any(flag in mode_arg.value for flag in ("w", "a", "x", "+"))


def _raw_write_text_call(node: ast.Call) -> str | None:
    """Return ``"write_text"`` / ``"write_bytes"`` if *node* is a raw full-file overwrite.

    Receivers whose name is in :data:`_ALREADY_ROUTED_RECEIVERS`
    (typically a ``FileBackend`` parameter or a method's ``self``)
    are not flagged, because those calls already pass through the
    canonical abstraction.
    """
    if not isinstance(node.func, ast.Attribute):
        return None
    attr = node.func.attr
    if attr not in {"write_text", "write_bytes"}:
        return None
    receiver = node.func.value
    if isinstance(receiver, ast.Name) and receiver.id in _ALREADY_ROUTED_RECEIVERS:
        return None
    return attr


def _raw_builtin_open_call(node: ast.Call) -> bool:
    """True if ``node`` is a builtin ``open(path, mode, ...)`` with write/append mode.

    ``open`` is the only commonly-used write primitive whose
    function is a bare :class:`ast.Name` rather than an
    :class:`ast.Attribute`. Other module-qualified calls (``io.open``,
    ``builtins.open``) are also detected: when the receiver is an
    ``ast.Attribute`` whose deepest name is ``open``, we treat it
    as the builtin too.
    """
    if isinstance(node.func, ast.Name) and node.func.id == "open":
        return _is_write_mode_open(node)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in {"builtins", "io"}:
            return _is_write_mode_open(node)
        # `webbrowser.open(...)` is not a filesystem operation.  The audit
        # matches calls by method name, so exclude this standard-library API
        # explicitly while retaining fail-closed handling for every other
        # mutating `.open(...)` form.
        if isinstance(receiver, ast.Name) and receiver.id in {"os", "webbrowser"}:
            return False
        # Path.open(...) is another raw file-writing entry point.  Its
        # receiver may be a Path variable, a Path(...) call, or a chained
        # pathlib expression, so treating every non-builtin ``.open`` call
        # with a mutating mode as raw is the fail-closed policy.
        return _is_write_mode_open(node, path_method=True)
    return False


def _call_root_name(node: ast.AST) -> str | None:
    """Return the deepest name on the call chain rooted at *node*.

    Used to detect patterns like ``Path(...).unlink()``,
    ``pathlib.Path(...).mkdir()``, ``os.path.join(...).replace(...)``
    (the last form is uncommon for filesystem mutations but the
    resolver is general enough to handle it). Returns ``None`` when
    the chain does not terminate in a name we recognise.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_root_name(node.value)
    if isinstance(node, ast.Call):
        # ``Path(p)`` — the call's function name is the root.
        return _call_root_name(node.func)
    return None


def _raw_qualified_mutation_call(node: ast.Call) -> str | None:
    """Return the attribute name if *node* is a raw qualified mutation.

    Matches ``os.replace``, ``shutil.copy``, ``pathlib.Path.unlink``,
    ``Path(...).unlink()`` (a call returning a Path with a
    subsequent method call), etc. against :data:`_RAW_MUTATION_QUALIFIERS`
    and :data:`_RAW_MUTATION_ATTRS`.

    The receiver chain is resolved to its deepest name so that:

    * ``os.replace(src, dst)`` — ``os`` is the root.
    * ``shutil.copy2(src, dst)`` — ``shutil`` is the root.
    * ``pathlib.Path(p).unlink()`` — ``pathlib`` is the root.
    * ``Path(p).unlink()`` (after ``from pathlib import Path``) —
      the receiver is a ``Call`` to ``Path``; the root name is
      ``Path`` which is recognised as a pathlib primitive alias.

    Returns:
        The matched attribute name (``"replace"``, ``"copy"``,
        ``"unlink"``, ...) when the call is a flagged raw mutation,
        ``None`` otherwise.
    """
    if not isinstance(node.func, ast.Attribute):
        return None
    attr = node.func.attr
    if attr not in _RAW_MUTATION_ATTRS:
        return None
    receiver = node.func.value
    root = _call_root_name(receiver)
    if root is None:
        return None
    return _qualified_mutation_attr(attr, receiver, root)


def _qualified_mutation_attr(attr: str, receiver: ast.expr, root: str) -> str | None:
    """Return a flagged mutation attribute when its receiver is a known filesystem API."""
    if root in _RAW_MUTATION_QUALIFIERS:
        # ``os.environ.copy()`` and similar attribute-chain calls are not
        # filesystem mutations. ``os``/``shutil`` primitives must be called
        # directly on the imported module; pathlib path construction is
        # deliberately allowed to be chained (``pathlib.Path(p).unlink()``).
        is_direct_module_call = isinstance(receiver, ast.Name) and receiver.id == root
        return attr if root not in {"os", "shutil"} or is_direct_module_call else None
    # ``Path`` / ``PurePath`` / ``PosixPath`` / ``WindowsPath`` are
    # typically imported from ``pathlib``; treat them as pathlib aliases.
    pathlib_aliases = {"Path", "PurePath", "PosixPath", "WindowsPath"}
    pathlib_mutations = {
        "write_text",
        "write_bytes",
        "replace",
        "rename",
        "unlink",
        "mkdir",
        "rmdir",
        "touch",
        "truncate",
    }
    return attr if root in pathlib_aliases and attr in pathlib_mutations else None


def _violation_message(attr: str) -> str:
    """Build the actionable diagnostic for a raw full-file write."""
    if attr == "write_bytes":
        primitive_hint = "write_bytes_if_changed"
    else:
        primitive_hint = "write_text_if_changed or atomic_write_text_if_changed"
    return (
        f"raw {attr} overwrite bypasses the stable-path mutation guard "
        f"(macOS fseventsd amplification); route through "
        f"ralph.mcp.artifacts.idempotent_write:{primitive_hint}, "
        f"or annotate the call with `# filesystem-write-ok: <reason>` "
        f"naming the behavioral contract (transient scratch, "
        f"deliberately timestamped, append-only stream, etc.)"
    )


def _qualified_violation_message(qualifier: str, attr: str) -> str:
    """Build the actionable diagnostic for a raw qualified mutation."""
    guidance = _RAW_MUTATION_ATTRS[attr]
    return (
        f"{qualifier}.{attr}(): {guidance}; "
        f"or annotate the call with `# filesystem-write-ok: <reason>` "
        f"naming the behavioral contract (transient scratch, "
        f"deliberately timestamped, atomic-replace boundary, etc.)"
    )


def _has_marker_on_or_before(line_idx: int, marker_lines: set[int]) -> bool:
    """True if any of the lines immediately before *line_idx* carries a marker.

    The marker is accepted on the call line itself (trailing
    comment) or on the immediately preceding source line. We do
    not allow the marker to live arbitrarily far up the file —
    that would weaken D3's "local exception" guarantee.
    """
    return line_idx in marker_lines or (line_idx - 1) in marker_lines


def _scan_module(
    module_path: Path,
    rel_path: str,
) -> list[FilesystemWriteViolation]:
    """Run the AST walk against one module and return its violations."""
    try:
        source = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # We can't read the file — surface as a structural violation
        # so a future maintainer can't silently exclude modules by
        # removing read permissions.
        return [
            FilesystemWriteViolation(
                kind="unreadable_module",
                file_path=rel_path,
                line=0,
                message=(
                    "module could not be read; restore readable source and ensure "
                    "the audit can walk it"
                ),
            )
        ]

    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError as exc:
        return [
            FilesystemWriteViolation(
                kind="invalid_module",
                file_path=rel_path,
                line=exc.lineno or 0,
                message=(
                    "module could not be parsed; restore valid source before the "
                    "filesystem-write audit can evaluate it"
                ),
            )
        ]

    # Pre-compute the lines that carry a filesystem-write-ok marker
    # so we can answer "is this call suppressed?" in O(1). The
    # marker MUST carry a non-empty reason — an empty reason fails
    # closed (D3).
    marker_lines: set[int] = set()
    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        if _MARKER_TOKEN not in line:
            continue
        marker_idx = line.find(_MARKER_TOKEN)
        reason = line[marker_idx + len(_MARKER_TOKEN) :].strip()
        if reason:
            marker_lines.add(idx)

    violations: list[FilesystemWriteViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Path.write_text / Path.write_bytes
        attr = _raw_write_text_call(node)
        if attr is not None:
            if _has_marker_on_or_before(node.lineno, marker_lines):
                continue
            violations.append(
                FilesystemWriteViolation(
                    kind="raw_write_text",
                    file_path=rel_path,
                    line=node.lineno,
                    message=_violation_message(attr),
                )
            )
            continue
        # builtin / Path.open(path, "w" / "a" / ...)
        if _raw_builtin_open_call(node):
            if _has_marker_on_or_before(node.lineno, marker_lines):
                continue
            violations.append(
                FilesystemWriteViolation(
                    kind="raw_open_write",
                    file_path=rel_path,
                    line=node.lineno,
                    message=(
                        "raw builtin open() with a write/append mode bypasses the "
                        "stable-path mutation guard; route through "
                        "ralph.mcp.artifacts.idempotent_write "
                        "(write_text_if_changed / write_bytes_if_changed) "
                        "or annotate the call with "
                        "`# filesystem-write-ok: <reason>` naming the "
                        "behavioral contract (deliberate binary append, "
                        "deliberately timestamped, atomic-create, etc.)"
                    ),
                )
            )
            continue
        # os.* / shutil.* / pathlib.Path.* raw mutation
        qattr = _raw_qualified_mutation_call(node)
        if qattr is not None:
            if _has_marker_on_or_before(node.lineno, marker_lines):
                continue
            qualifier = _call_receiver_root(node) or "?"
            violations.append(
                FilesystemWriteViolation(
                    kind=f"raw_{qattr}",
                    file_path=rel_path,
                    line=node.lineno,
                    message=_qualified_violation_message(qualifier, qattr),
                )
            )
    return violations


def _call_receiver_root(node: ast.Call) -> str | None:
    """Return the root receiver name of an attribute call."""
    if not isinstance(node.func, ast.Attribute):
        return None
    return _call_root_name(node.func.value)


def _has_named_marker_for_line(source: str, line_idx: int, marker_lines: set[int]) -> bool:
    """True if the marker on/above ``line_idx`` carries a non-empty reason.

    An empty reason (``# filesystem-write-ok:``) is treated as drift
    per D3; the audit fails closed on those sites by reporting a
    violation even though a token is present.
    """
    lines = source.splitlines()
    candidates: list[int] = []
    if line_idx in marker_lines:
        candidates.append(line_idx)
    if line_idx - 1 in marker_lines:
        candidates.append(line_idx - 1)
    for idx in candidates:
        text = lines[idx - 1] if 1 <= idx <= len(lines) else ""
        marker_idx = text.find(_MARKER_TOKEN)
        if marker_idx == -1:
            continue
        reason = text[marker_idx + len(_MARKER_TOKEN) :].strip()
        if reason:
            return True
    return False


def audit_filesystem_write_consolidation(
    package_root: Path,
    *,
    module_paths: Sequence[str] | None = None,
    exempt_paths: frozenset[str] | Sequence[str] | None = None,
    package_roots: Sequence[str] | None = None,
) -> list[FilesystemWriteViolation]:
    """Return a list of package-wide filesystem-write violations.

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

    if module_paths is not None:
        candidates = [(package_root / rel, rel) for rel in module_paths]
    else:
        candidates = []
        for root in roots:
            for path in _collect_python_files(root):
                rel = path.relative_to(package_root).as_posix()
                candidates.append((path, rel))

    violations: list[FilesystemWriteViolation] = []
    for module_path, rel_path in candidates:
        if _is_exempt(rel_path, exempt):
            continue
        violations.extend(_scan_module(module_path, rel_path))
    return violations


def _is_exempt(rel_path: str, exempt: frozenset[str]) -> bool:
    """True if ``rel_path`` matches any entry in ``exempt``.

    Exempt entries may be expressed as either the full path
    (``ralph/mcp/artifacts/idempotent_write.py``) or the path
    relative to the walked root (``mcp/artifacts/idempotent_write.py``
    when the walk target is ``ralph/``). Matching against the full
    path keeps the canonical form; matching the suffix lets the
    default exempt set work whether the caller supplied
    ``package_roots=("ralph",)`` or walked ``package_root`` directly.
    """
    if rel_path in exempt:
        return True
    return any(rel_path == entry or rel_path.endswith("/" + entry) for entry in exempt)


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when clean, 1 on violations, or 2 for a missing package root."""
    if argv is None:
        argv = sys.argv[1:]

    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent.parent
    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations = audit_filesystem_write_consolidation(package_root)
    if violations:
        print(f"FILESYSTEM WRITE CONSOLIDATION VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            "Fix the drift: stable full-file writes in ralph/ must route through "
            "ralph.mcp.artifacts.idempotent_write (write_text_if_changed or "
            "atomic_write_text_if_changed), or carry a local "
            "`# filesystem-write-ok: <reason>` marker naming the behavioral "
            "contract (transient scratch, deliberately timestamped, append-only "
            "stream, etc.). The marker must include a reason — an empty reason "
            "fails closed per D3."
        )
        return 1

    print("filesystem write consolidation audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
