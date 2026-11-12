from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_scope import build_parent_map_from_nodes, qualified_scope

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
    }
)
EXEMPT_CALL_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("ralph/_install_conflicts.py", "resolve_package_file", "raw_path_read_text"),
        (
            "ralph/agents/invoke/_command_builders/__init__.py",
            "_load_prompt_with_master",
            "raw_path_read_text",
        ),
        ("ralph/agents/invoke/_watch_capacity.py", "_count_watchable_directories", "raw_walk"),
        (
            "ralph/agents/invoke/_watch_capacity.py",
            "_read_inotify_watch_limit",
            "raw_path_read_text",
        ),
        (
            "ralph/agents/invoke/_watch_capacity.py",
            "_read_inotify_watch_user_total",
            "raw_path_iterdir",
        ),
        (
            "ralph/cli/commands/smoke_binary_override.py",
            "resolve_smoke_binary_override",
            "raw_path_is_file",
        ),
        ("ralph/config/_toml_fallback.py", "load_optional_toml", "raw_path_read_bytes"),
        ("ralph/files/operations.py", "calculate_checksum", "raw_path_open"),
        ("ralph/files/operations.py", "capture_file_snapshot", "raw_path_exists"),
        ("ralph/files/operations.py", "capture_file_snapshot", "raw_path_stat"),
        ("ralph/git/_lock_liveness.py", "_not_host_pid_namespace", "raw_path_exists"),
        ("ralph/git/_lock_liveness.py", "_not_host_pid_namespace", "raw_path_read_text"),
        ("ralph/git/_lock_liveness.py", "_pid1_started_at_boot", "raw_path_read_text"),
        ("ralph/git/_lock_paths.py", "_walk_locks", "raw_walk"),
        ("ralph/git/_lock_proc_inspect.py", "_git_binary_identity_uncached", "raw_path_stat"),
        ("ralph/git/_worktree_lease.py", "_probe_holder_pid", "raw_path_is_dir"),
        ("ralph/git/rebase/rebase_checkpoint.py", "_backup_checkpoint", "raw_path_exists"),
        ("ralph/git/rebase/rebase_checkpoint.py", "save_rebase_checkpoint", "raw_path_exists"),
        (
            "ralph/mcp/explore/_bench_product_baseline.py",
            "load_product_baseline_limits",
            "raw_path_read_text",
        ),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_count_proc_fds", "raw_path_iterdir"),
        (
            "ralph/mcp/explore/_bench_r6_metrics.py",
            "_snapshot_initial_inotify_fds",
            "raw_path_is_dir",
        ),
        (
            "ralph/mcp/explore/_bench_r6_metrics.py",
            "_snapshot_initial_inotify_fds",
            "raw_path_iterdir",
        ),
        ("ralph/mcp/explore/_store_types.py", "iter_indexable_files", "raw_scandir"),
        ("ralph/mcp/explore/handlers.py", "build_explore_index", "raw_path_exists"),
        (
            "ralph/mcp/server/_fallback_standalone_server.py",
            "_FallbackStandaloneServer._setup_handover_pause._pause_watcher",
            "raw_path_exists",
        ),
        ("ralph/mcp/server/_host_contention_signal.py", "record_psi_pressure", "raw_path_open"),
        ("ralph/mcp/server/_start_stage_tracker.py", "read_overruns", "raw_path_read_text"),
        (
            "ralph/mcp/server/_start_stage_tracker.py",
            "record_overrun._write_record",
            "raw_path_open",
        ),
        ("ralph/mcp/tools/workspace/_media_io.py", "write_durable_media_cache", "raw_path_glob"),
        ("ralph/phases/review.py", "_read_review_baseline", "raw_path_exists"),
        ("ralph/phases/review.py", "_read_review_baseline", "raw_path_read_text"),
        ("ralph/pipeline/_auto_integrate_record_restamp.py", "current_boot_id", "raw_path_exists"),
        (
            "ralph/pipeline/_auto_integrate_record_restamp.py",
            "current_boot_id",
            "raw_path_read_text",
        ),
        ("ralph/pipeline/_integration_ladder_adapters.py", "_walk_lock_files", "raw_walk"),
        (
            "ralph/pipeline/auto_integrate_recovery_lock.py",
            "_lock_holder_is_dead",
            "raw_path_exists",
        ),
        ("ralph/pipeline/auto_integrate_recovery_lock.py", "_lock_holder_is_dead", "raw_path_stat"),
        (
            "ralph/pipeline/auto_integrate_remote_reconcile.py",
            "_reconcile_in_temporary_workspace",
            "raw_path_exists",
        ),
        (
            "ralph/pipeline/config_file_fingerprint.py",
            "compute_config_file_fingerprint",
            "raw_path_read_bytes",
        ),
        (
            "ralph/pipeline/conflict_resolution/rebase_loop.py",
            "_read_rebase_state_sha",
            "raw_path_read_text",
        ),
        ("ralph/pipeline/effect_executor.py", "_write_agent_retry_prompt", "raw_path_exists"),
        ("ralph/pipeline/effect_executor.py", "_write_agent_retry_prompt", "raw_path_read_text"),
        (
            "ralph/pipeline/plumbing/commit_plumbing.py",
            "_read_retry_prompt_text",
            "raw_path_exists",
        ),
        (
            "ralph/pipeline/plumbing/commit_plumbing.py",
            "_read_retry_prompt_text",
            "raw_path_read_text",
        ),
        ("ralph/pipeline/run_loop.py", "_rebase_markers_on_disk", "raw_path_is_file"),
        ("ralph/pipeline/run_loop.py", "_rebase_markers_on_disk", "raw_path_read_text"),
        ("ralph/process/manager/_git_termination.py", "_process_name", "raw_path_read_text"),
        ("ralph/process/manager/_git_termination.py", "group_pids", "raw_path_is_dir"),
        ("ralph/process/manager/_git_termination.py", "group_pids", "raw_path_iterdir"),
        ("ralph/process/manager/_supervisor.py", "_other_group_members_alive", "raw_path_iterdir"),
        ("ralph/project_policy/portfolio.py", "load_portfolio_toml", "raw_path_exists"),
        ("ralph/project_policy/portfolio.py", "load_portfolio_toml", "raw_path_read_text"),
        ("ralph/recovery/controller.py", "_assert_never_exit_invariant", "raw_path_read_text"),
        ("ralph/recovery/controller.py", "_assert_two_state_invariant", "raw_path_read_text"),
        ("ralph/skills/_installer_candidates.py", "_canonical_skill_leaf_names", "raw_walk"),
        ("ralph/skills/_installer_candidates.py", "_lexical_descendants", "raw_walk"),
        ("ralph/skills/_skill_resolver.py", "get_inline_skill_content", "raw_path_glob"),
        ("ralph/skills/_skill_resolver.py", "get_inline_skill_content", "raw_path_is_dir"),
        ("ralph/testing/audit_activity_aware_watchdog.py", "_compute_fingerprint", "raw_walk"),
        ("ralph/testing/audit_activity_aware_watchdog.py", "_iter_py_files", "raw_walk"),
        ("ralph/testing/audit_agent_failure_single_owner.py", "_collect_payloads", "raw_walk"),
        ("ralph/testing/audit_agent_module_state.py", "_collect_py_files", "raw_walk"),
        ("ralph/testing/audit_comments.py", "_slice_files", "raw_path_is_dir"),
        ("ralph/testing/audit_comments.py", "_slice_files", "raw_path_is_file"),
        ("ralph/testing/audit_comments.py", "_slice_files", "raw_path_rglob"),
        ("ralph/testing/audit_opts_key_drift.py", "_parse_module_payload", "raw_path_read_bytes"),
        ("ralph/testing/audit_secret_scan.py", "main", "raw_path_is_dir"),
        (
            "ralph/testing/audit_terminal_escape_containment.py",
            "PackageWideCallSiteInvariant._package_files",
            "raw_walk",
        ),
        ("ralph/testing/audit_test_integrity.py", "_read_diff", "raw_path_read_text"),
        ("ralph/workspace/fs.py", "FsWorkspace.iter_files", "raw_walk"),
    }
)
_RAW_READ_QUALIFIERS: frozenset[str] = frozenset({"os", "os.path", "pathlib"})
_RAW_READ_ATTRS: dict[str, str] = {
    "read_text": (
        "R1/R3 raw full-file read; route through FileBackend.read_text or "
        "exempt the call site via EXEMPT_CALL_SITES"
    ),
    "read_bytes": "R1/R3 raw full-file byte load; route through FileBackend.read_bytes",
    "open": "R1/R3 raw file handle read; route through FileBackend or Workspace",
    "exists": "R4 raw existence probe; use FileBackend.exists",
    "is_file": "R4 raw existence probe; use FileBackend.exists",
    "is_dir": "R4 raw existence probe; use FileBackend.exists",
    "isfile": "R4 raw existence probe; use FileBackend.exists",
    "isdir": "R4 raw existence probe; use FileBackend.exists",
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
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
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
    direct_read_names: dict[str, str],
) -> None:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "pathlib":
                for alias in node.names:
                    if alias.name in _PATHLIBS:
                        path_names.add(alias.asname or alias.name)
            elif node.module in {"os", "os.path", "glob"}:
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if node.module == "os" and alias.name == "path":
                        os_names.add(local_name)
                    elif alias.name in _RAW_READ_ATTRS:
                        direct_read_names[local_name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pathlib":
                    path_names.add(alias.asname or alias.name)
                elif alias.name in {"os", "os.path"}:
                    os_names.add(alias.asname or alias.name)
                elif alias.name == "glob":
                    glob_names.add(alias.asname or alias.name)


def _scope_keys(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[int | None, ...]:
    keys: list[int | None] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            keys.append(id(current))
        current = parents.get(current)
    keys.append(None)
    return tuple(keys)


def _scope_key(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> int | None:
    return _scope_keys(node, parents)[0]


def _assign_target_names(
    value: ast.expr,
    target: ast.expr,
    names: set[tuple[int | None, str]],
    constructor_names: set[str],
    *,
    scope: int | None,
) -> None:
    if not isinstance(value, ast.Call):
        return
    root = _call_root_name(value.func)
    if root is None or (root not in _PATHLIBS and root not in constructor_names):
        return
    if isinstance(target, ast.Name):
        names.add((scope, target.id))


def _collect_read_provenance(
    tree: ast.Module, nodes: Sequence[ast.AST], parents: dict[ast.AST, ast.AST]
) -> tuple[set[tuple[int | None, str]], set[str], set[str], dict[str, str]]:
    imported_path_names: set[str] = set()
    os_names: set[str] = set()
    glob_names: set[str] = set()
    direct_read_names: dict[str, str] = {}
    _import_alias_names(tree, imported_path_names, os_names, glob_names, direct_read_names)
    constructor_names = set(imported_path_names)
    path_names: set[tuple[int | None, str]] = {(None, name) for name in imported_path_names}
    for node in nodes:
        scope = _scope_key(node, parents)
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Name) and node.value.id in constructor_names:
                constructor_names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            for target in node.targets:
                _assign_target_names(node.value, target, path_names, constructor_names, scope=scope)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in constructor_names
                and isinstance(node.target, ast.Name)
            ):
                constructor_names.add(node.target.id)
            _assign_target_names(
                node.value,
                node.target,
                path_names,
                constructor_names,
                scope=scope,
            )
    return path_names, os_names, glob_names, direct_read_names


def _read_call_details(
    node: ast.Call,
    direct_read_names: dict[str, str],
) -> tuple[str, str, bool] | None:
    direct_name = node.func.id if isinstance(node.func, ast.Name) else None
    if direct_name == "open":
        return "open", direct_name, True
    if direct_name in direct_read_names:
        return direct_read_names[direct_name], direct_name, True
    attr = _attr_name(node)
    if attr is None or attr not in _RAW_READ_ATTRS or not isinstance(node.func, ast.Attribute):
        return None
    root = _call_root_name(node.func.value)
    if root is None:
        return None
    return attr, root, False


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_exempt(rel_path: str, exempt: frozenset[str]) -> bool:
    return rel_path in exempt


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
    except (OSError, UnicodeDecodeError) as exc:
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
    nodes = list(ast.walk(tree))
    parents = build_parent_map_from_nodes(nodes)
    path_variables, os_names, glob_names, direct_read_names = _collect_read_provenance(
        tree, nodes, parents
    )

    violations: list[FilesystemReadViolation] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        call_details = _read_call_details(node, direct_read_names)
        if call_details is None:
            continue
        attr, root, is_direct_read = call_details
        line_idx = node.lineno
        is_os_path_read = root in os_names and attr != "open"
        is_os_traversal = attr in _OS_TRAVERSAL_ATTRS and is_os_path_read
        is_glob_traversal = attr in _GLOB_TRAVERSAL_ATTRS and root in glob_names
        is_raw_qualifier = root in _RAW_READ_QUALIFIERS
        is_path_value = any((scope, root) in path_variables for scope in _scope_keys(node, parents))
        is_path_traversal = attr in {"iterdir", "glob", "rglob"} and (
            is_path_value or root in _PATHLIBS
        )
        is_path_open = attr == "open" and (is_path_value or root in _PATHLIBS)
        if (
            is_direct_read
            or (is_raw_qualifier and attr != "open")
            or is_os_path_read
            or is_glob_traversal
        ):
            kind = f"raw_{attr}"
            message = _RAW_READ_ATTRS[attr]
        elif is_path_value or root in _PATHLIBS:
            if attr in _OS_TRAVERSAL_ATTRS and not is_os_traversal:
                continue
            if attr in _GLOB_TRAVERSAL_ATTRS and not (is_path_traversal or is_glob_traversal):
                continue
            if attr in {"iterdir", "glob", "rglob"} and not is_path_traversal:
                continue
            if attr == "open" and not is_path_open:
                continue
            kind = f"raw_path_{attr}"
            message = _RAW_READ_ATTRS[attr]
        else:
            continue
        scope = qualified_scope(node, parents)
        if (rel_path, scope, kind) in EXEMPT_CALL_SITES:
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
    if module_paths is None:
        missing_roots = [root for root in roots if not root.is_dir()]
        if missing_roots:
            return [
                FilesystemReadViolation(
                    kind="missing_production_root",
                    file_path=root.relative_to(package_root).as_posix(),
                    line=0,
                    message=(
                        "expected production root could not be walked; restore it so "
                        "the filesystem-read audit can fail closed"
                    ),
                )
                for root in missing_roots
            ]
    violations: list[FilesystemReadViolation] = []
    if module_paths is not None:
        candidates: list[tuple[Path, str]] = []
        resolved_root = package_root.resolve()
        for rel in module_paths:
            candidate = package_root / rel
            try:
                candidate.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                violations.append(
                    FilesystemReadViolation(
                        kind="invalid_module_path",
                        file_path=rel,
                        line=0,
                        message=(
                            "explicit module path escapes the package root; pass a relative "
                            "production-module path so the filesystem-read audit can fail closed"
                        ),
                    )
                )
                continue
            candidates.append((candidate, rel))
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


def main(argv: Sequence[str] | None = None) -> int:
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
