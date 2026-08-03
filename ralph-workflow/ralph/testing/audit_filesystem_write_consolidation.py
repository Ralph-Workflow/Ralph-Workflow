"""Package-wide filesystem-write consolidation audit.

Provides a fail-closed, package-wide AST walk that rejects every raw
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
  * ``open(path, mode)`` and ``os.fdopen(fd, mode)`` with a write/append/
    extend mode (``"w"``, ``"a"``, ``"x"``, ``"wb"``, ``"ab"``, ``"r+"``,
    ``"w+"``, ``"a+"`` and their ``b``/``t`` variants)
  * ``os.replace`` / ``os.rename`` / ``os.renames`` / ``Path.replace`` /
    ``Path.rename`` (atomic moves)
  * ``Path.unlink`` / ``os.remove`` / ``os.unlink`` / ``Path.rmdir``
    (raw deletes)
  * ``Path.mkdir`` / ``os.mkdir`` / ``os.makedirs`` / ``os.open``
    (raw directory or file creation)
  * ``shutil.rmtree`` / ``shutil.copy`` / ``shutil.copy2`` /
    ``shutil.copyfile`` / ``shutil.copytree`` / ``shutil.copymode`` /
    ``shutil.move``
    (raw copies / moves / tree deletes)
  * ``os.fsync`` / ``os.sync`` (raw durability barriers outside
    the canonical primitive)
  * ``Path.touch`` (raw mtime bumps)
  * ``Path.chmod`` / ``os.chmod`` (raw permission metadata changes)
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
import tokenize
from dataclasses import dataclass
from io import StringIO
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
        # The audit module itself is outside its production target.
        "testing/audit_filesystem_write_consolidation.py",
    }
)

#: Comment marker that suppresses a violation at the call site. The
#: marker must name a behavioral contract (D3 — exceptions are
#: explicit and local). The format is fixed; an empty reason is
#: treated as drift.
_MARKER_TOKEN = "filesystem-write-ok:"
_OPEN_MODE_POSITION = 1
_OPEN_MODE_MIN_ARGS = 2
_GETATTR_NAME_POSITION = 1
_GETATTR_MIN_ARGS = 2

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
    "copy": "raw shutil.copy bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or mark",
    "copy2": "raw shutil.copy2 bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or mark",
    "copyfile": "raw shutil.copyfile bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or mark",
    "copytree": "raw shutil.copytree bypasses the canonical directory-copy boundary; "
    "route through Workspace.copy or mark the explicit user-requested tree contract",
    "copymode": "raw shutil.copymode bypasses the canonical copy primitive; "
    "mark with a reason or route through the canonical primitive",
    "move": "raw shutil.move bypasses the canonical move boundary; "
    "route through an approved persistence primitive appropriate to the move or mark",
    "touch": "raw touch bumps mtime without content change; "
    "annotate with `# filesystem-write-ok: <reason>` or remove the call",
    "chmod": "raw permission metadata mutation bypasses the filesystem activity guard; "
    "retain only under a `# filesystem-write-ok: <reason>` marker naming the permission contract",
    "open": "raw os.open bypasses the canonical file-creation boundary; "
    "route through a shared persistence primitive or mark the local lifecycle reason",
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
        relative_parts = path.relative_to(root).parts
        if "__pycache__" in path.parts or "testing" in relative_parts:
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
    """Return a raw full-file overwrite name, including dynamic lookups.

    Receiver names are not proof of routing: an arbitrary new module can name
    an unguarded writer ``backend`` or ``self``. Likewise, resolving
    ``write_text`` through ``getattr`` must not evade the package-wide audit.
    Only shared primitive modules are exempt, so every other raw overwrite
    fails closed and must route through an idempotent helper or carry a local
    contract marker.
    """
    if isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        return attr if attr in {"write_text", "write_bytes"} else None
    if not isinstance(node.func, ast.Call) or not isinstance(node.func.func, ast.Name):
        return None
    if node.func.func.id != "getattr" or len(node.func.args) < _GETATTR_MIN_ARGS:
        return None
    dynamic_attr = node.func.args[_GETATTR_NAME_POSITION]
    if isinstance(dynamic_attr, ast.Constant) and isinstance(dynamic_attr.value, str):
        return (
            dynamic_attr.value
            if dynamic_attr.value in {"write_text", "write_bytes"}
            else None
        )
    return None


def _raw_fdopen_call(
    node: ast.Call,
    direct_fdopen_aliases: frozenset[str],
    fdopen_module_aliases: frozenset[str],
) -> bool:
    """True if ``node`` is an ``os.fdopen(fd, mode, ...)`` write acquisition.

    Descriptor-backed file streams are still filesystem mutations.  Recognize
    direct and imported aliases so that a temporary-file creation sequence
    cannot evade the same local-contract requirement as ``open``.
    """
    if isinstance(node.func, ast.Name) and node.func.id in direct_fdopen_aliases:
        return _is_write_mode_open(node)
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "fdopen"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"os", *fdopen_module_aliases}
        and _is_write_mode_open(node)
    )


def _raw_builtin_open_call(
    node: ast.Call,
    direct_open_aliases: frozenset[str],
    open_module_aliases: frozenset[str],
) -> bool:
    """True if ``node`` is a builtin ``open(path, mode, ...)`` with write/append mode.

    ``open`` is the only commonly-used write primitive whose
    function is a bare :class:`ast.Name` rather than an
    :class:`ast.Attribute`. Other module-qualified calls (``io.open``,
    ``builtins.open``) are also detected: when the receiver is an
    ``ast.Attribute`` whose deepest name is ``open``, we treat it
    as the builtin too.
    """
    if isinstance(node.func, ast.Name) and node.func.id in {"open", *direct_open_aliases}:
        return _is_write_mode_open(node)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in {
            "builtins",
            "io",
            *open_module_aliases,
        }:
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


def _import_aliases(
    nodes: list[ast.AST],
) -> tuple[
    dict[str, str],
    dict[str, str],
    frozenset[str],
    frozenset[str],
    frozenset[str],
    frozenset[str],
]:
    """Return module, mutation, and mode-sensitive ``open`` aliases from *tree*.

    Import aliases must not turn package-wide enforcement into an easily
    evaded spelling convention: ``import os as filesystem``, ``from os import
    replace as publish``, and ``from io import open as persist`` remain raw
    filesystem mutations. ``open`` aliases are kept separate because their
    mode determines whether the call mutates.
    """
    module_aliases: dict[str, str] = {}
    direct_mutations: dict[str, str] = {}
    direct_open_aliases: set[str] = set()
    open_module_aliases: set[str] = set()
    direct_fdopen_aliases: set[str] = set()
    fdopen_module_aliases: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for imported in node.names:
                root = imported.name.split(".", maxsplit=1)[0]
                if root in _RAW_MUTATION_QUALIFIERS or root == "pathlib":
                    local_name = imported.asname or root
                    module_aliases[local_name] = root
                    if root == "os":
                        fdopen_module_aliases.add(local_name)
                elif imported.name in {"io", "builtins"}:
                    open_module_aliases.add(imported.asname or imported.name)
        elif isinstance(node, ast.ImportFrom):
            for imported in node.names:
                local_name = imported.asname or imported.name
                if node.module in {"io", "builtins"} and imported.name == "open":
                    direct_open_aliases.add(local_name)
                elif node.module == "os" and imported.name == "fdopen":
                    direct_fdopen_aliases.add(local_name)
                elif node.module == "pathlib" and imported.name in {
                    "Path",
                    "PurePath",
                    "PosixPath",
                    "WindowsPath",
                }:
                    module_aliases[local_name] = "Path"
                elif node.module in {"os", "shutil", "pathlib"} and imported.name in _RAW_MUTATION_ATTRS:
                    direct_mutations[local_name] = imported.name
    return (
        module_aliases,
        direct_mutations,
        frozenset(direct_open_aliases),
        frozenset(open_module_aliases),
        frozenset(direct_fdopen_aliases),
        frozenset(fdopen_module_aliases),
    )


def _raw_direct_import_mutation_call(
    node: ast.Call, direct_mutations: dict[str, str]
) -> str | None:
    """Return the raw mutation called through a directly imported alias, if any."""
    if isinstance(node.func, ast.Name):
        return direct_mutations.get(node.func.id)
    return None


def _scope_key(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> int | None:
    """Return the nearest function scope identity, or ``None`` at module scope."""
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return id(current)
        current = parents.get(current)
    return None


def _path_variable_names(
    nodes: list[ast.AST],
    module_aliases: dict[str, str],
    parents: dict[ast.AST, ast.AST],
) -> frozenset[tuple[int | None, str]]:
    """Return function-scoped local names directly initialized from pathlib.

    This deliberately small provenance pass closes common bypasses where a
    ``Path(...)`` value or the canonical workspace path resolvers (``_abs`` /
    ``absolute_path``) are assigned before their mutating method is called. It
    does not guess that arbitrary objects are paths, avoiding false positives
    for domain methods named ``unlink`` or ``replace``. Provenance remains
    scoped to the function containing the assignment so a ``Path`` local in
    one function cannot misclassify a same-named string parameter elsewhere.
    """
    names: set[tuple[int | None, str]] = set()
    pathlib_constructors = {"Path", "PurePath", "PosixPath", "WindowsPath"}
    workspace_path_resolvers = {"_abs", "absolute_path"}
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if not isinstance(node.value, ast.Call):
            continue
        root = _call_root_name(node.value.func)
        is_path_constructor = root is not None and module_aliases.get(root, root) in pathlib_constructors
        is_workspace_resolver = (
            isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in workspace_path_resolvers
        )
        if not is_path_constructor and not is_workspace_resolver:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        scope = _scope_key(node, parents)
        names.update(
            (scope, target_key)
            for target in targets
            if (target_key := _path_target_key(target)) is not None
        )
    return frozenset(names)


def _path_target_key(target: ast.expr) -> str | None:
    """Return a conservative key for a local or ``self``-held path target."""
    if isinstance(target, ast.Name):
        return target.id
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return f"self.{target.attr}"
    return None


def _path_receiver_key(receiver: ast.expr) -> str | None:
    """Return the matching provenance key for a mutation receiver."""
    return _path_target_key(receiver)


def _raw_qualified_mutation_call(
    node: ast.Call,
    module_aliases: dict[str, str],
    path_variables: frozenset[tuple[int | None, str]],
    parents: dict[ast.AST, ast.AST],
) -> str | None:
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
    receiver_key = _path_receiver_key(receiver)
    workspace_resolver_call = _is_workspace_resolver_receiver(receiver)
    if workspace_resolver_call or (
        receiver_key is not None and (_scope_key(node, parents), receiver_key) in path_variables
    ):
        return attr if attr in {
            "replace", "rename", "unlink", "mkdir", "rmdir", "touch", "truncate", "chmod"
        } else None
    root = _call_root_name(receiver)
    if root is None:
        return None
    resolved_root = module_aliases.get(root, root)
    if (
        resolved_root in {"os", "shutil"}
        and root != resolved_root
        and isinstance(receiver, ast.Name)
    ):
        return attr
    return _qualified_mutation_attr(attr, receiver, resolved_root)


def _is_workspace_resolver_receiver(receiver: ast.expr) -> bool:
    """Return whether a receiver derives from a workspace path resolver.

    A resolver result often flows through ``.parent`` before a directory
    mutation. Treat that one attribute chain as path provenance so a direct
    ``self._abs(path).parent.mkdir(...)`` cannot bypass the shared backend.
    """
    if isinstance(receiver, ast.Call):
        return isinstance(receiver.func, ast.Attribute) and receiver.func.attr in {
            "_abs",
            "absolute_path",
        }
    return isinstance(receiver, ast.Attribute) and _is_workspace_resolver_receiver(receiver.value)


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
        "chmod",
        "truncate",
    }
    return attr if root in pathlib_aliases and attr in pathlib_mutations else None


def _violation_message(attr: str) -> str:
    """Build the actionable diagnostic for a raw full-file write."""
    if attr == "write_bytes":
        primitive_hint = "write_bytes_if_changed or atomic_write_bytes_if_changed"
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


def _marker_comment_lines(source: str) -> set[int]:
    """Return lines with a reasoned filesystem-write marker in a Python comment."""
    marker_lines: set[int] = set()
    for token in tokenize.generate_tokens(StringIO(source).readline):
        if token.type != tokenize.COMMENT or _MARKER_TOKEN not in token.string:
            continue
        marker_idx = token.string.find(_MARKER_TOKEN)
        reason = token.string[marker_idx + len(_MARKER_TOKEN) :].strip()
        if reason:
            marker_lines.add(token.start[0])
    return marker_lines


def _has_marker_on_or_before(line_idx: int, marker_lines: set[int]) -> bool:
    """True if any of the lines immediately before *line_idx* carries a marker.

    The marker is accepted on the call line itself (trailing
    comment) or on the immediately preceding source line. We do
    not allow the marker to live arbitrarily far up the file —
    that would weaken D3's "local exception" guarantee.
    """
    return line_idx in marker_lines or (line_idx - 1) in marker_lines


def _parse_candidate_module(
    source: str, module_path: Path, rel_path: str
) -> ast.Module | FilesystemWriteViolation:
    """Parse candidate source or return its fail-closed syntax violation."""
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


def _read_candidate_source(
    module_path: Path, rel_path: str
) -> str | FilesystemWriteViolation:
    """Read one module or return its fail-closed unreadable-source violation."""
    try:
        return module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return FilesystemWriteViolation(
            kind="unreadable_module",
            file_path=rel_path,
            line=0,
            message=(
                "module could not be read; restore readable source and ensure "
                "the audit can walk it"
            ),
        )


def _prepare_module(
    module_path: Path, rel_path: str
) -> tuple[ast.Module, str] | list[FilesystemWriteViolation]:
    """Read and parse every module, preserving fail-closed diagnostics."""
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
    """Run the AST walk against one module and return its violations."""
    prepared = _prepare_module(module_path, rel_path)
    if isinstance(prepared, list):
        return prepared
    tree, source = prepared

    # Pre-compute reasoned comment markers so a string or docstring
    # cannot silently become a D3 exception annotation.
    marker_lines = _marker_comment_lines(source)

    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    (
        module_aliases,
        direct_mutations,
        direct_open_aliases,
        open_module_aliases,
        direct_fdopen_aliases,
        fdopen_module_aliases,
    ) = _import_aliases(nodes)
    path_variables = _path_variable_names(nodes, module_aliases, parents)
    violations: list[FilesystemWriteViolation] = []
    for node in nodes:
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
        # os.fdopen(fd, "w" / "a" / ...) and aliases
        if _raw_fdopen_call(node, direct_fdopen_aliases, fdopen_module_aliases):
            if _has_marker_on_or_before(node.lineno, marker_lines):
                continue
            violations.append(
                FilesystemWriteViolation(
                    kind="raw_fdopen_write",
                    file_path=rel_path,
                    line=node.lineno,
                    message=(
                        "raw os.fdopen() with a write/append mode bypasses the "
                        "filesystem activity guard; route stable content through "
                        "ralph.mcp.artifacts.idempotent_write or annotate the call with "
                        "`# filesystem-write-ok: <reason>` naming the transient or "
                        "append-stream lifecycle contract"
                    ),
                )
            )
            continue
        # builtin / Path.open(path, "w" / "a" / ...)
        if _raw_builtin_open_call(node, direct_open_aliases, open_module_aliases):
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
                        "(write_text_if_changed / atomic_write_text_if_changed / "
                        "write_bytes_if_changed / atomic_write_bytes_if_changed) "
                        "or annotate the call with "
                        "`# filesystem-write-ok: <reason>` naming the "
                        "behavioral contract (deliberate binary append, "
                        "deliberately timestamped, atomic-create, etc.)"
                    ),
                )
            )
            continue
        # ``from os import replace as publish`` and similar direct imports.
        direct_violation = _direct_import_violation(node, direct_mutations, marker_lines, rel_path)
        if direct_violation is not None:
            violations.append(direct_violation)
            continue
        # os.* / shutil.* / pathlib.Path.* raw mutation
        qattr = _raw_qualified_mutation_call(node, module_aliases, path_variables, parents)
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


def _direct_import_violation(
    node: ast.Call,
    direct_mutations: dict[str, str],
    marker_lines: set[int],
    rel_path: str,
) -> FilesystemWriteViolation | None:
    """Return a violation for a directly imported raw mutation, if present."""
    direct_attr = _raw_direct_import_mutation_call(node, direct_mutations)
    if direct_attr is None or _has_marker_on_or_before(node.lineno, marker_lines):
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
