from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.testing._audit_scope import build_parent_map_from_nodes, qualified_scope

if TYPE_CHECKING:
    from pathlib import Path


_OPEN_MODE_POSITION = 1
_OPEN_MODE_MIN_ARGS = 2
_GETATTR_NAME_POSITION = 1
_GETATTR_MIN_ARGS = 2
_RAW_MUTATION_QUALIFIERS: frozenset[str] = frozenset(
    {
        "os",
        "shutil",
        "pathlib",
    }
)

_RAW_MUTATION_ATTRS: dict[str, str] = {
    "replace": "raw atomic-replace bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "rename": "raw rename bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "renames": "raw renames bypasses the canonical primitives; "
    "route through ralph.mcp.artifacts.idempotent_write",
    "unlink": "raw unlink bypasses the canonical delete primitive; "
    "delete only through the canonical delete primitive",
    "remove": "raw os.remove bypasses the canonical delete primitive; "
    "delete only through the canonical delete primitive",
    "mkdir": "raw mkdir bypasses the canonical directory creation primitive; "
    "use workspace.fs.mkdirs or exempt the call site via EXEMPT_CALL_SITES",
    "makedirs": "raw makedirs bypasses the canonical directory creation primitive; "
    "use workspace.fs.mkdirs or exempt the call site via EXEMPT_CALL_SITES",
    "rmdir": "raw rmdir bypasses the canonical delete primitive",
    "rmtree": "raw rmtree bypasses the canonical delete primitive",
    "fsync": "raw fsync bypasses the canonical durability primitive; "
    "use ralph.mcp.artifacts.idempotent_write or exempt the call site via EXEMPT_CALL_SITES",
    "sync": "raw os.sync bypasses the canonical durability primitive; "
    "use ralph.mcp.artifacts.idempotent_write or exempt the call site via EXEMPT_CALL_SITES",
    "copy": "raw shutil.copy bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or "
    "exempt the call site via EXEMPT_CALL_SITES",
    "copy2": "raw shutil.copy2 bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or "
    "exempt the call site via EXEMPT_CALL_SITES",
    "copyfile": "raw shutil.copyfile bypasses the canonical copy boundary; "
    "route through an approved persistence primitive appropriate to the copy or "
    "exempt the call site via EXEMPT_CALL_SITES",
    "copytree": "raw shutil.copytree bypasses the canonical directory-copy boundary; "
    "route through Workspace.copy or exempt the call site via EXEMPT_CALL_SITES "
    "for the explicit user-requested tree contract",
    "copymode": "raw shutil.copymode bypasses the canonical copy primitive; "
    "route through the canonical primitive or exempt the call site via EXEMPT_CALL_SITES",
    "move": "raw shutil.move bypasses the canonical move boundary; "
    "route through an approved persistence primitive appropriate to the move or "
    "exempt the call site via EXEMPT_CALL_SITES",
    "touch": "raw touch bumps mtime without content change; "
    "register the call site in EXEMPT_CALL_SITES or remove the call",
    "chmod": "raw permission metadata mutation bypasses the filesystem activity guard; "
    "retain only as a registered call site naming the permission contract",
    "open": "raw os.open bypasses the canonical file-creation boundary; "
    "route through a shared persistence primitive or exempt the call site via "
    "EXEMPT_CALL_SITES for the local lifecycle contract",
    "truncate": "raw truncate bypasses the canonical truncation primitive; "
    "route through the canonical primitive or exempt the call site via EXEMPT_CALL_SITES",
}


@dataclass(frozen=True)
class FilesystemWriteViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _is_write_mode_open(call: ast.Call, *, path_method: bool = False) -> bool:
    mode_position = 0 if path_method else _OPEN_MODE_POSITION
    minimum_args = 1 if path_method else _OPEN_MODE_MIN_ARGS
    mode_arg: ast.expr | None = call.args[mode_position] if len(call.args) >= minimum_args else None
    if mode_arg is None:
        mode_arg = next((keyword.value for keyword in call.keywords if keyword.arg == "mode"), None)
    if mode_arg is None:
        return False
    if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, str):
        return True
    return any(flag in mode_arg.value for flag in ("w", "a", "x", "+"))


def _raw_write_text_call(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        return attr if attr in {"write_text", "write_bytes"} else None
    if not isinstance(node.func, ast.Call) or not isinstance(node.func.func, ast.Name):
        return None
    if node.func.func.id != "getattr" or len(node.func.args) < _GETATTR_MIN_ARGS:
        return None
    dynamic_attr = node.func.args[_GETATTR_NAME_POSITION]
    if isinstance(dynamic_attr, ast.Constant) and isinstance(dynamic_attr.value, str):
        return dynamic_attr.value if dynamic_attr.value in {"write_text", "write_bytes"} else None
    return None


def _raw_fdopen_call(
    node: ast.Call,
    direct_fdopen_aliases: frozenset[str],
    fdopen_module_aliases: frozenset[str],
) -> bool:
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
        if isinstance(receiver, ast.Name) and receiver.id in {"os", "webbrowser"}:
            return False
        return _is_write_mode_open(node, path_method=True)
    return False


def _call_root_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_root_name(node.value)
    if isinstance(node, ast.Call):
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
    module_aliases: dict[str, str] = {}
    direct_mutations: dict[str, str] = {}
    direct_open_aliases: set[str] = set()
    open_module_aliases: set[str] = set()
    direct_fdopen_aliases: set[str] = set()
    fdopen_module_aliases: set[str] = set()
    for node in nodes:
        match True:
            case _ if isinstance(node, ast.Import):
                for imported in node.names:
                    root = imported.name.split(".", maxsplit=1)[0]
                    if root in _RAW_MUTATION_QUALIFIERS or root == "pathlib":
                        local_name = imported.asname or root
                        module_aliases[local_name] = root
                        if root == "os":
                            fdopen_module_aliases.add(local_name)
                    elif imported.name in {"io", "builtins"}:
                        open_module_aliases.add(imported.asname or imported.name)
            case _ if isinstance(node, ast.ImportFrom):
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
                    elif (
                        node.module in {"os", "shutil", "pathlib"}
                        and imported.name in _RAW_MUTATION_ATTRS
                    ):
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
    if isinstance(node.func, ast.Name):
        return direct_mutations.get(node.func.id)
    return None


def _scope_key(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> int | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return id(current)
        current = parents.get(current)
    return None


def _path_variable_names(
    nodes: list[ast.AST],
    module_aliases: dict[str, str],
    parents: dict[ast.AST, ast.AST],
) -> frozenset[tuple[int | None, str]]:
    names: set[tuple[int | None, str]] = set()
    pathlib_constructors = {"Path", "PurePath", "PosixPath", "WindowsPath"}
    workspace_path_resolvers = {"_abs", "absolute_path"}
    for node in nodes:
        if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
            continue
        if not isinstance(node.value, ast.Call):
            continue
        root = _call_root_name(node.value.func)
        is_path_constructor = (
            root is not None and module_aliases.get(root, root) in pathlib_constructors
        )
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
    if isinstance(receiver, ast.Attribute) and receiver.attr == "parent":
        return _path_receiver_key(receiver.value)
    return _path_target_key(receiver)


def _raw_qualified_mutation_call(
    node: ast.Call,
    module_aliases: dict[str, str],
    path_variables: frozenset[tuple[int | None, str]],
    parents: dict[ast.AST, ast.AST],
) -> str | None:
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
        return (
            attr
            if attr
            in {"replace", "rename", "unlink", "mkdir", "rmdir", "touch", "truncate", "chmod"}
            else None
        )
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
    if isinstance(receiver, ast.Call):
        return isinstance(receiver.func, ast.Attribute) and receiver.func.attr in {
            "_abs",
            "absolute_path",
        }
    return isinstance(receiver, ast.Attribute) and _is_workspace_resolver_receiver(receiver.value)


def _qualified_mutation_attr(attr: str, receiver: ast.expr, root: str) -> str | None:
    if root in _RAW_MUTATION_QUALIFIERS:
        is_direct_module_call = isinstance(receiver, ast.Name) and receiver.id == root
        return attr if root not in {"os", "shutil"} or is_direct_module_call else None
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
    if attr == "write_bytes":
        primitive_hint = "write_bytes_if_changed or atomic_write_bytes_if_changed"
    else:
        primitive_hint = "write_text_if_changed or atomic_write_text_if_changed"
    return (
        f"raw {attr} overwrite bypasses the stable-path mutation guard "
        f"(macOS fseventsd amplification); route through "
        f"ralph.mcp.artifacts.idempotent_write:{primitive_hint}, "
        f"or register the call site in EXEMPT_CALL_SITES "
        f"naming the behavioral contract (transient scratch, "
        f"deliberately timestamped, append-only stream, etc.)"
    )


def _qualified_violation_message(qualifier: str, attr: str) -> str:
    guidance = _RAW_MUTATION_ATTRS[attr]
    return (
        f"{qualifier}.{attr}(): {guidance}; "
        f"or register the call site in EXEMPT_CALL_SITES "
        f"naming the behavioral contract (transient scratch, "
        f"deliberately timestamped, atomic-replace boundary, etc.)"
    )


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
    from ralph.testing.audit_filesystem_write_consolidation import EXEMPT_CALL_SITES

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
