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
_MODE_POSITION: int = 1

_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
        "testing/audit_filesystem_write_consolidation.py",
    }
)

_OPEN_MODE_POSITION = 1
_OPEN_MODE_MIN_ARGS = 2
_GETATTR_NAME_POSITION = 1
_GETATTR_MIN_ARGS = 2

EXEMPT_CALL_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("ralph/_install_copy_tree.py", "copy_install_tree", "raw_copytree"),
        ("ralph/_install_runtime.py", "_fsync_directory", "raw_fsync"),
        ("ralph/_install_runtime.py", "_fsync_directory", "raw_open"),
        ("ralph/_install_runtime.py", "write_dev_launcher", "raw_chmod"),
        ("ralph/_install_runtime.py", "write_dev_launcher", "raw_fdopen_write"),
        ("ralph/_install_runtime.py", "write_dev_launcher", "raw_fsync"),
        ("ralph/_install_runtime.py", "write_dev_launcher", "raw_replace"),
        ("ralph/_install_runtime.py", "write_dev_launcher", "raw_unlink"),
        (
            "ralph/agents/invoke/_runtime_resolvers/__init__.py",
            "CodexRuntimeResolver.resolve._release",
            "raw_rmtree",
        ),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", "_project_agy_auth", "raw_copy2"),
        ("ralph/cli/commands/cleanup.py", "cleanup", "raw_rmtree"),
        ("ralph/cli/commands/run.py", "_clear_generated_pipeline_state", "raw_rmtree"),
        ("ralph/config/_bootstrap_backup.py", "_migrate_legacy_global_config", "raw_copy2"),
        ("ralph/config/agents_file_writer.py", "_publish_backup", "raw_fdopen_write"),
        ("ralph/config/agents_file_writer.py", "_publish_backup", "raw_open"),
        ("ralph/config/agents_file_writer.py", "_target_serialization_lock", "raw_open"),
        ("ralph/config/agents_file_writer.py", "write_agents_toml", "raw_chmod"),
        ("ralph/config/agents_file_writer.py", "write_agents_toml", "raw_fdopen_write"),
        ("ralph/config/agents_file_writer.py", "write_agents_toml", "raw_replace"),
        ("ralph/config/agents_file_writer.py", "write_agents_toml", "raw_unlink"),
        ("ralph/config/bootstrap.py", "_copy_with_backup", "raw_copy2"),
        ("ralph/config/bootstrap.py", "_copy_with_backup", "raw_move"),
        ("ralph/display/_terminal_bg_query.py", "_tty_fd", "raw_open"),
        ("ralph/display/raw_overflow.py", "RawOverflowLog._write", "raw_open_write"),
        ("ralph/display/raw_overflow.py", "_is_live_foreign_capture", "raw_open"),
        ("ralph/display/record_writer.py", "RenderedRecordWriter.flush", "raw_open_write"),
        ("ralph/display/terminal_restore.py", "_default_tty_stream", "raw_fdopen_write"),
        ("ralph/display/terminal_restore.py", "_default_tty_stream", "raw_open"),
        ("ralph/git/_git_command_queue_lease.py", "_acquire_lock_file", "raw_open"),
        ("ralph/git/_lock_recovery.py", "_reap_mutex", "raw_open_write"),
        ("ralph/git/_worktree_lease.py", "_takeover_lock_file", "raw_open"),
        ("ralph/git/hooks.py", "_backup_existing_hook", "raw_copyfile"),
        ("ralph/git/hooks.py", "_remove_hook", "raw_move"),
        ("ralph/git/operations.py", "_atomic_append_text", "raw_write_text"),
        ("ralph/git/operations.py", "create_commit", "raw_unlink"),
        ("ralph/git/operations.py", "create_commit_with_hooks", "raw_write_text"),
        ("ralph/git/proposal_seal.py", "seal_baseline", "raw_copy2"),
        ("ralph/git/proposal_seal.py", "seal_pending", "raw_copy2"),
        ("ralph/git/rebase/rebase_checkpoint.py", "_backup_checkpoint", "raw_copy2"),
        ("ralph/git/rebase/rebase_checkpoint.py", "_backup_checkpoint", "raw_replace"),
        ("ralph/git/rebase/rebase_checkpoint.py", "_backup_checkpoint", "raw_unlink"),
        ("ralph/git/rebase/rebase_checkpoint.py", "acquire_rebase_lock", "raw_write_text"),
        ("ralph/git/rebase/rebase_checkpoint.py", "restore_from_backup", "raw_copy2"),
        ("ralph/git/rebase/rebase_checkpoint.py", "save_rebase_checkpoint", "raw_replace"),
        ("ralph/git/rebase/rebase_checkpoint.py", "save_rebase_checkpoint", "raw_unlink"),
        ("ralph/git/rebase/rebase_checkpoint.py", "save_rebase_checkpoint", "raw_write_text"),
        ("ralph/install_transaction.py", "_remove_tree", "raw_rmtree"),
        ("ralph/install_transaction.py", "install_lock", "raw_fdopen_write"),
        ("ralph/install_transaction.py", "install_lock", "raw_open"),
        (
            "ralph/mcp/explore/_bench_product_baseline.py",
            "_seeded_probe_workspace",
            "raw_write_text",
        ),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_measure_changed_refresh", "raw_rmtree"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_measure_operational", "raw_rmtree"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_measure_operational", "raw_write_text"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_measure_query_latency", "raw_rmtree"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_rebuild_index", "raw_rmtree"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_seed_large_synthetic", "raw_write_text"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_seed_ralph_self_workspace", "raw_copytree"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_seed_small_workspace", "raw_write_text"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "capture_baseline", "raw_write_text"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "measure_cold_build", "raw_rmtree"),
        ("ralph/mcp/explore/_pipeline_staged.py", "_staged_full_reindex", "raw_rmtree"),
        ("ralph/mcp/explore/_store_class.py", "ExploreStore.__init__", "raw_mkdir"),
        ("ralph/mcp/explore/handlers.py", "build_explore_index", "raw_unlink"),
        ("ralph/mcp/explore/recovery.py", "_write_state_file", "raw_write_text"),
        ("ralph/mcp/explore/recovery.py", "run_recovery_action", "raw_rmtree"),
        ("ralph/mcp/explore/recovery.py", "try_advisory_lock", "raw_open"),
        ("ralph/mcp/explore/reindex_bench.py", "_index_snapshot", "raw_rmtree"),
        ("ralph/mcp/explore/reindex_bench.py", "_run_reindex", "raw_rmtree"),
        ("ralph/mcp/explore/reindex_bench.py", "_seed_corpus", "raw_write_text"),
        ("ralph/mcp/explore/reindex_bench.py", "_seeded_workspace", "raw_rmtree"),
        (
            "ralph/mcp/server/_fallback_standalone_server.py",
            "_FallbackStandaloneServer._setup_handover_pause._pause_watcher",
            "raw_write_text",
        ),
        ("ralph/mcp/server/_handover_protocol.py", "request_old_child_pause", "raw_write_text"),
        ("ralph/mcp/server/_handover_protocol.py", "signal_candidate_resume", "raw_write_text"),
        ("ralph/mcp/server/_mcp_health_event.py", "record_restart_decision", "raw_open_write"),
        ("ralph/mcp/server/_snapshots.py", "snapshot_inputs", "raw_copy2"),
        ("ralph/mcp/server/_snapshots.py", "write_snapshot", "raw_open_write"),
        ("ralph/mcp/server/_start_stage_tracker.py", "_do_record", "raw_fsync"),
        ("ralph/mcp/server/_start_stage_tracker.py", "_do_record", "raw_open"),
        ("ralph/mcp/server/_start_stage_tracker.py", "_do_record", "raw_replace"),
        ("ralph/mcp/server/_start_stage_tracker.py", "_do_record", "raw_unlink"),
        ("ralph/mcp/server/_start_stage_tracker.py", "record_overrun._write_record", "raw_fsync"),
        (
            "ralph/mcp/server/_start_stage_tracker.py",
            "record_overrun._write_record",
            "raw_open_write",
        ),
        (
            "ralph/mcp/server/_transport_repetition_tracker.py",
            "TransportRepetitionTracker._observe_with_carried_state",
            "raw_open_write",
        ),
        (
            "ralph/mcp/server/_transport_repetition_tracker.py",
            "TransportRepetitionTracker._write_carried_state",
            "raw_open_write",
        ),
        ("ralph/mcp/server/_wire_ledger.py", "append_wire_record", "raw_open_write"),
        (
            "ralph/mcp/server/lifecycle.py",
            "RestartAwareMcpBridge._record_contention_deferral",
            "raw_open_write",
        ),
        ("ralph/mcp/server/lifecycle.py", "_create_session_file", "raw_fdopen_write"),
        ("ralph/mcp/server/lifecycle.py", "_create_session_file", "raw_unlink"),
        ("ralph/mcp/server/lifecycle.py", "_spawn_process", "raw_open"),
        ("ralph/mcp/tools/_exec_output_spill.py", "spill_output", "raw_fdopen_write"),
        ("ralph/mcp/tools/_exec_output_spill.py", "spill_output", "raw_unlink"),
        ("ralph/mcp/transport/codex.py", "_copy_codex_home_entry", "raw_copy2"),
        ("ralph/mcp/transport/codex.py", "_copy_codex_home_entry", "raw_copytree"),
        ("ralph/mcp/transport/codex.py", "cleanup_codex_homes", "raw_rmtree"),
        ("ralph/mcp/transport/codex.py", "release_codex_home", "raw_rmtree"),
        ("ralph/mcp/transport/cursor.py", "_copy_cursor_home_entry", "raw_copy2"),
        ("ralph/mcp/transport/cursor.py", "_copy_cursor_home_entry", "raw_copytree"),
        ("ralph/mcp/transport/pi.py", "write_pi_mcp_extension.cleanup", "raw_rmtree"),
        (
            "ralph/mcp/transport/private_config_root.py",
            "prepare_private_config_root",
            "raw_write_text",
        ),
        (
            "ralph/mcp/transport/private_config_root.py",
            "prepare_private_config_root.cleanup",
            "raw_rmtree",
        ),
        ("ralph/mcp/upstream/tool_catalog_cache.py", "_write_catalog_file", "raw_open"),
        ("ralph/pipeline/_auto_integrate_recovery_git_state.py", "remove_path", "raw_rmtree"),
        (
            "ralph/pipeline/_integration_ladder_adapters.py",
            "_ProductionEnvironmentProbe._write_failure",
            "raw_open_write",
        ),
        (
            "ralph/pipeline/_integration_ladder_adapters.py",
            "_ProductionFileQuarantine.prune_regenerable_artifacts",
            "raw_rmtree",
        ),
        (
            "ralph/pipeline/_integration_ladder_adapters.py",
            "_ProductionFileQuarantine.rename_into_quarantine",
            "raw_renames",
        ),
        (
            "ralph/pipeline/_integration_ladder_adapters.py",
            "_ProductionGitPlumbing.archive_paths",
            "raw_open_write",
        ),
        (
            "ralph/pipeline/_integration_ladder_adapters.py",
            "_ProductionGitPlumbing.remove_operation_state",
            "raw_rmtree",
        ),
        (
            "ralph/pipeline/auto_integrate_backup_refs.py",
            "create_rebase_backup_ref",
            "raw_write_text",
        ),
        (
            "ralph/pipeline/auto_integrate_backup_refs.py",
            "create_stale_resolution_snapshot",
            "raw_write_text",
        ),
        (
            "ralph/pipeline/auto_integrate_catchup_coordination.py",
            "_write_fetch_after",
            "raw_write_text",
        ),
        (
            "ralph/pipeline/auto_integrate_catchup_coordination.py",
            "remote_sync_transaction",
            "raw_open_write",
        ),
        (
            "ralph/pipeline/auto_integrate_remote_reconcile.py",
            "_cleanup_temporary_worktree",
            "raw_rmtree",
        ),
        (
            "ralph/pipeline/auto_integrate_remote_reconcile.py",
            "_reconcile_in_temporary_workspace",
            "raw_rmtree",
        ),
        (
            "ralph/pipeline/auto_integrate_transaction.py",
            "integration_transaction",
            "raw_open_write",
        ),
        ("ralph/pipeline/effect_executor.py", "_write_agent_retry_prompt", "raw_write_text"),
        ("ralph/pipeline/effect_executor.py", "_write_retry_context_file", "raw_write_text"),
        (
            "ralph/pipeline/integration_attempt_ledger.py",
            "AttemptLedger._atomic_write",
            "raw_fsync",
        ),
        ("ralph/pipeline/integration_attempt_ledger.py", "AttemptLedger._atomic_write", "raw_open"),
        (
            "ralph/pipeline/integration_attempt_ledger.py",
            "AttemptLedger._atomic_write",
            "raw_open_write",
        ),
        ("ralph/pipeline/integration_attempt_ledger.py", "AttemptLedger._locked", "raw_open_write"),
        (
            "ralph/pipeline/plumbing/commit_plumbing.py",
            "_write_commit_prompt_file",
            "raw_write_text",
        ),
        ("ralph/pipeline/plumbing/smoke_plumbing.py", "run_smoke_plumbing", "raw_write_text"),
        ("ralph/pipeline/runner.py", "_write_rejection_hint", "raw_write_text"),
        ("ralph/pipeline/watchdog_fire_record.py", "record_watchdog_fire", "raw_open_write"),
        ("ralph/skills/_installer.py", "_materialize_project_sibling_dir", "raw_copytree"),
        ("ralph/skills/_installer.py", "_materialize_project_sibling_dir", "raw_rmtree"),
        ("ralph/skills/_installer.py", "_mirror_baseline_skills_to_siblings", "raw_copy2"),
        ("ralph/skills/_installer.py", "_mirror_baseline_skills_to_siblings", "raw_rmtree"),
        ("ralph/skills/_installer.py", "_mirror_skill_to_sibling_root", "raw_copytree"),
        ("ralph/skills/_installer.py", "_mirror_skill_to_sibling_root", "raw_rmtree"),
        ("ralph/skills/_prune.py", "_prune_managed_directories", "raw_rmtree"),
        ("ralph/skills/_process_view.py", "SkillsProcessView.__enter__", "raw_mkdir"),
        ("ralph/skills/_upstream_install.py", "_link_skill", "raw_copytree"),
        ("ralph/skills/_upstream_install.py", "discard_tree", "raw_rmtree"),
        ("ralph/skills/_upstream_install.py", "restore_backup", "raw_copytree"),
        ("ralph/skills/upstream.py", "_open_lock_descriptor", "raw_open"),
        ("ralph/skills/upstream.py", "_write_json_atomic", "raw_replace"),
        ("ralph/skills/upstream.py", "_write_json_atomic", "raw_unlink"),
        ("ralph/skills/upstream.py", "_write_json_atomic", "raw_write_text"),
        ("ralph/telemetry/_user_identity.py", "_identity_lock", "raw_open"),
        ("ralph/telemetry/_user_identity.py", "_write_user_id_file", "raw_chmod"),
        ("ralph/telemetry/_user_identity.py", "_write_user_id_file", "raw_fdopen_write"),
        ("ralph/telemetry/_user_identity.py", "_write_user_id_file", "raw_fsync"),
        ("ralph/telemetry/_user_identity.py", "_write_user_id_file", "raw_replace"),
        ("ralph/telemetry/_user_identity.py", "_write_user_id_file", "raw_unlink"),
        ("ralph/test_suites.py", "_run_shards", "raw_write_text"),
        (
            "ralph/workspace/_cross_process_watch_lock.py",
            "CrossProcessWatchLock.try_acquire",
            "raw_open_write",
        ),
        (
            "ralph/workspace/_shared_awareness.py",
            "SharedAwarenessSidecar._write_document",
            "raw_write_text",
        ),
        ("ralph/workspace/agent_dir_retention.py", "_metadata_lock", "raw_open_write"),
        ("ralph/workspace/agent_dir_retention.py", "_sweep_codex_home_dirs", "raw_rmtree"),
        ("ralph/workspace/agent_dir_retention.py", "_sweep_receipt_dirs", "raw_rmtree"),
        ("ralph/workspace/fs.py", "FsWorkspace.append", "raw_mkdir"),
        ("ralph/workspace/fs.py", "FsWorkspace.append", "raw_open_write"),
        ("ralph/workspace/fs.py", "FsWorkspace.copy", "raw_copy2"),
        ("ralph/workspace/fs.py", "FsWorkspace.copy", "raw_copytree"),
        ("ralph/workspace/fs.py", "FsWorkspace.delete", "raw_rmtree"),
        ("ralph/workspace/fs.py", "FsWorkspace.delete", "raw_unlink"),
        ("ralph/workspace/fs.py", "FsWorkspace.move", "raw_move"),
    }
)
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
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
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
            "atomic_write_text_if_changed), or be registered in "
            "EXEMPT_CALL_SITES as (file, qualified callable, kind) naming the "
            "behavioral contract (transient scratch, deliberately timestamped, "
            "append-only stream, etc.)."
        )
        return 1

    print("filesystem write consolidation audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
