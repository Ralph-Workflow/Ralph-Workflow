from __future__ import annotations

import ast
import sys
from pathlib import Path

from ralph.testing._audit_scope import build_parent_map, qualified_scope

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    }
)

EXEMPT_CALL_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("ralph/agents/catalog.py", "<module>", "unbounded_accumulator"),
        (
            "ralph/agents/execution_state/opencode_execution_strategy.py",
            "OpenCodeExecutionStrategy.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/idle_watchdog/_subagent_identity.py",
            "SubagentPidRegistry.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/idle_watchdog/idle_watchdog.py",
            "IdleWatchdog.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/idle_watchdog/repetition_tracker.py",
            "RepetitionTracker.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/_bounded_lines_queue.py",
            "BoundedLinesQueue.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/_process_reader.py",
            "ProcessLineReader.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/_pty_line_reader.py",
            "PtyLineReader.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/_shared_awareness_publisher.py",
            "ThrottledSidecarPublisher.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/_subagent_transcript.py",
            "ClaudeSubagentTranscriptTails.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/agents/invoke/_workspace.py", "WorkspaceMonitor", "unbounded_accumulator"),
        ("ralph/agents/invoke/_workspace.py", "WorkspaceMonitor.__init__", "unbounded_accumulator"),
        (
            "ralph/agents/invoke/_workspace_change_coalescer.py",
            "WorkspaceChangeCoalescer.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py",
            "AttemptSupervisor.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/invoke/opencode_subagent_sessions/_probe.py",
            "OpenCodeSubagentSessionProbe.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/agents/parsers/_template.py",
            "ParserTemplateBase.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/agents/parsers/agy.py", "AgyParser.__init__", "unbounded_accumulator"),
        ("ralph/agents/parsers/claude.py", "ClaudeParser.__init__", "unbounded_accumulator"),
        (
            "ralph/agents/parsers/claude_interactive.py",
            "ClaudeInteractiveParser.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/agents/parsers/codex.py", "CodexParser.__init__", "unbounded_accumulator"),
        ("ralph/agents/parsers/codex.py", "_CodexDispatch.__init__", "unbounded_accumulator"),
        ("ralph/agents/parsers/opencode.py", "OpenCodeParser.__init__", "unbounded_accumulator"),
        ("ralph/agents/parsers/pi.py", "PiParser.__init__", "unbounded_accumulator"),
        ("ralph/agents/registry.py", "AgentRegistry.__init__", "unbounded_accumulator"),
        (
            "ralph/agents/subprocess_executor.py",
            "SubprocessAgentExecutor.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/agents/support.py", "<module>", "unbounded_accumulator"),
        ("ralph/config/_toml_fallback.py", "<module>", "unbounded_accumulator"),
        ("ralph/config/agents_file_writer.py", "_publish_backup", "raw_os_fd"),
        ("ralph/config/agents_file_writer.py", "_target_serialization_lock", "raw_os_fd"),
        ("ralph/config/loader.py", "<module>", "unbounded_accumulator"),
        ("ralph/diagnostics/host_pressure.py", "_macos_maxfiles_pressure", "direct_child_spawn"),
        ("ralph/display/_salience.py", "SalienceAllocator.__init__", "unbounded_accumulator"),
        ("ralph/display/_terminal_bg_query.py", "_tty_fd", "raw_os_fd"),
        ("ralph/display/activity_router.py", "<module>", "unbounded_accumulator"),
        ("ralph/display/activity_router.py", "ActivityRouter.__init__", "unbounded_accumulator"),
        (
            "ralph/display/capability_observation_recorder.py",
            "CapabilityObservationRecorder.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/display/parallel_display.py", "ParallelDisplay.__init__", "unbounded_accumulator"),
        ("ralph/display/raw_overflow.py", "<module>", "unbounded_accumulator"),
        ("ralph/display/raw_overflow.py", "_is_live_foreign_capture", "raw_os_fd"),
        ("ralph/display/ring_buffer.py", "RingBuffer.__init__", "unbounded_accumulator"),
        ("ralph/display/subscriber.py", "PipelineSubscriber.__init__", "unbounded_accumulator"),
        ("ralph/display/terminal_restore.py", "_default_tty_stream", "raw_os_fd"),
        ("ralph/git/_git_command_queue_lease.py", "_acquire_lock_file", "raw_os_fd"),
        ("ralph/git/_lock_recovery.py", "_find_lock_holder", "direct_child_spawn"),
        ("ralph/git/_lock_recovery.py", "_probe_lsof_fuser", "direct_child_spawn"),
        ("ralph/git/_worktree_lease.py", "_takeover_lock_file", "raw_os_fd"),
        ("ralph/git/git_command_queue.py", "GitCommandQueue.__init__", "unbounded_accumulator"),
        (
            "ralph/language_detector/signatures.py",
            "DetectionResults.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/mcp/artifacts/_path_file_backend.py",
            "PathFileBackend.sync_directory",
            "raw_os_fd",
        ),
        (
            "ralph/mcp/artifacts/audit_adapter.py",
            "RalphAuditSinkAdapter.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/mcp/artifacts/markdown/_parser.py",
            "_SectionBuilder.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/mcp/artifacts/markdown/registry.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "measure_cold_build", "direct_child_spawn"),
        ("ralph/mcp/explore/_bench_r6_subprocess.py", "run_build_subprocess", "direct_child_spawn"),
        (
            "ralph/mcp/explore/_burst_scheduler.py",
            "BurstDebounceScheduler.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/mcp/explore/_handlers_graph.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/explore/_handlers_reindex.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/explore/_pipeline_writer.py", "ReindexWriter", "unbounded_accumulator"),
        ("ralph/mcp/explore/dirty_paths.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/explore/recovery.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/explore/recovery.py", "try_advisory_lock", "raw_os_fd"),
        ("ralph/mcp/protocol/transport.py", "_default_thread_factory", "non_daemon_thread"),
        ("ralph/mcp/server/_activity_relay.py", "ActivityRelay.__init__", "unbounded_accumulator"),
        (
            "ralph/mcp/server/_pre_serve_overrun_events.py",
            "StageEventRecorder.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/mcp/server/_runtime_hooks.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/server/_start_stage_tracker.py", "_do_record", "raw_os_fd"),
        ("ralph/mcp/server/_wire_ledger.py", "<module>", "unbounded_accumulator"),
        (
            "ralph/mcp/server/_wire_ledger_index.py",
            "WireLedgerIndex.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/mcp/server/factory_impl.py",
            "DynamicBindingMcpServerFactory.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/mcp/server/lifecycle.py",
            "RestartAwareMcpBridge.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/mcp/server/lifecycle.py", "_spawn_process", "raw_os_fd"),
        ("ralph/mcp/tool_contract.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/tools/_exec_jobs.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/tools/_exec_jobs.py", "ExecJobRegistry.__init__", "unbounded_accumulator"),
        (
            "ralph/mcp/tools/_exec_resource_uri.py",
            "ExecResourceResolver.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/mcp/tools/bridge/_tool_bridge.py", "ToolBridge.__init__", "unbounded_accumulator"),
        ("ralph/mcp/transport/codex.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/upstream/tool_catalog_cache.py", "<module>", "unbounded_accumulator"),
        ("ralph/mcp/upstream/tool_catalog_cache.py", "_write_catalog_file", "raw_os_fd"),
        ("ralph/phases/__init__.py", "<module>", "unbounded_accumulator"),
        (
            "ralph/pipeline/_auto_integrate_agent_phase.py",
            "AgentPhaseActivity.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/pipeline/_integration_log_dedupe.py",
            "IntegrationOutcomeLog.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/pipeline/_stop_resolution_entry.py",
            "<module>",
            "unbounded_accumulator",
        ),
        (
            "ralph/pipeline/auto_integrate_boundary_refresh.py",
            "BoundaryRefreshThrottle.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/pipeline/auto_integrate_conflict_budget.py", "<module>", "unbounded_accumulator"),
        (
            "ralph/pipeline/auto_integrate_remote_sync.py",
            "_RemotePullThrottle.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/pipeline/config_file_fingerprint_watcher.py",
            "ConfigFileFingerprintWatcher.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/pipeline/integration_attempt_ledger.py",
            "AttemptLedger._atomic_write",
            "raw_os_fd",
        ),
        ("ralph/pipeline/integration_ladder.py", "<module>", "unbounded_accumulator"),
        ("ralph/pipeline/reducer.py", "<module>", "unbounded_accumulator"),
        ("ralph/policy/loader.py", "<module>", "unbounded_accumulator"),
        ("ralph/pro_support/heartbeat.py", "_default_httpx_client_factory", "bare_http_client"),
        (
            "ralph/process/child_liveness.py",
            "ChildLivenessRegistry.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/process/manager/_liveness_sampler.py",
            "TreeSampler.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/process/manager/_process_manager.py",
            "ProcessManager.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/process/manager/_process_manager_types.py", "<module>", "unbounded_accumulator"),
        ("ralph/process/teardown.py", "<module>", "unbounded_accumulator"),
        ("ralph/project_policy/portfolio.py", "<module>", "unbounded_accumulator"),
        ("ralph/project_policy/validators.py", "<module>", "unbounded_accumulator"),
        ("ralph/prompts/template_engine.py", "<module>", "unbounded_accumulator"),
        ("ralph/prompts/template_engine.py", "TemplateRenderer.__init__", "unbounded_accumulator"),
        (
            "ralph/prompts/template_registry.py",
            "TemplateRegistry.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/prompts/template_registry.py",
            "_PackagedTemplateCache.__init__",
            "unbounded_accumulator",
        ),
        (
            "ralph/recovery/agent_failure_ledger.py",
            "AgentFailureLedger.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/recovery/connectivity.py", "<module>", "unbounded_accumulator"),
        ("ralph/recovery/connectivity.py", "ConnectivityMonitor.__init__", "unbounded_accumulator"),
        ("ralph/recovery/controller.py", "<module>", "unbounded_accumulator"),
        ("ralph/recovery/controller.py", "RecoveryController.__init__", "unbounded_accumulator"),
        ("ralph/recovery/events.py", "FailureEventBus.__init__", "unbounded_accumulator"),
        (
            "ralph/recovery/false_bench_feedback.py",
            "FalseBenchFeedback.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/recovery/testing.py", "FakeConnectivityMonitor.__init__", "unbounded_accumulator"),
        ("ralph/skills/upstream.py", "_open_lock_descriptor", "raw_os_fd"),
        ("ralph/telemetry/_user_identity.py", "_identity_lock", "raw_os_fd"),
        (
            "ralph/workspace/_cross_process_watch_lock.py",
            "CrossProcessWatchLock",
            "unbounded_accumulator",
        ),
        ("ralph/workspace/_shared_awareness.py", "<module>", "unbounded_accumulator"),
        (
            "ralph/workspace/_shared_awareness.py",
            "SharedAwarenessSidecar.__init__",
            "unbounded_accumulator",
        ),
        ("ralph/workspace/awareness.py", "<module>", "unbounded_accumulator"),
        ("ralph/workspace/awareness.py", "WorkspaceAwareness.__init__", "unbounded_accumulator"),
        ("ralph/workspace/memory.py", "MemoryWorkspace.__init__", "unbounded_accumulator"),
        (
            "ralph/workspace/probe.py",
            "DefaultWorkspaceChangeProbe.__init__",
            "unbounded_accumulator",
        ),
    }
)
_THREAD_NAMES: frozenset[str] = frozenset({"threading.Thread", "Thread"})

_HTTP_CLIENT_NAMES: frozenset[str] = frozenset(
    {"httpx.Client", "httpx.AsyncClient", "requests.Session"}
)
_HTTP_ROOTS: frozenset[str] = frozenset({"httpx", "requests"})

_ORDERED_DICT_NAMES: frozenset[str] = frozenset({"OrderedDict", "collections.OrderedDict"})
_DEFAULT_DICT_NAMES: frozenset[str] = frozenset({"defaultdict", "collections.defaultdict"})

_RAW_OS_FD_NAMES: frozenset[str] = frozenset({"os.open", "os.openpty", "os.pipe"})

_RAW_OS_FD_ALLOWLIST_DIRS: tuple[str, ...] = ("process",)

_DIRECT_CHILD_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "os.system",
        "os.posix_spawn",
        "os.fork",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }
)


class ResourceLifecycleViolation:
    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _keyword_truthy(node: ast.Call, name: str) -> bool:
    for kw in node.keywords:
        if kw.arg != name:
            continue
        value: ast.expr = kw.value
        if isinstance(value, ast.Constant):
            return value.value is True
        return False
    return False


def _keyword_present(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _keyword_value_is_positive_int(node: ast.Call, name: str) -> bool:
    for kw in node.keywords:
        if kw.arg != name:
            continue
        value: ast.expr = kw.value
        if isinstance(value, ast.Constant):
            return (
                isinstance(value.value, int)
                and not isinstance(value.value, bool)
                and value.value > 0
            )
        return False
    return False


def _dotted_name(node: ast.Call) -> str | None:
    func: ast.expr = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    else:
        return None
    return ".".join(reversed(parts))


def _collect_import_aliases_if_needed(
    source: str,
    tree: ast.Module,
) -> tuple[dict[str, str], dict[str, str]]:
    if not _source_needs_import_resolution(source):
        return {}, {}
    return _collect_import_aliases(tree)


def _collect_import_aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    module_aliases: dict[str, str] = {}
    from_imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    module_aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                from_imports[local] = f"{node.module}.{alias.name}"
    return module_aliases, from_imports


def _canonical_name(
    name: str | None,
    module_aliases: dict[str, str],
    from_imports: dict[str, str],
) -> str | None:
    if name is None:
        return None
    parts = name.split(".")
    if len(parts) == 1:
        return from_imports.get(parts[0], parts[0])
    head = module_aliases.get(parts[0], parts[0])
    return ".".join([head, *parts[1:]])


def _collect_in_with_calls(tree: ast.Module) -> set[ast.Call]:
    result: set[ast.Call] = set()
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.With, ast.AsyncWith)):
            for item in parent.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    result.add(ctx)
    return result


def _is_static_dict_key(node: ast.expr) -> bool:
    return isinstance(node, (ast.Constant, ast.Name, ast.Attribute, ast.Subscript)) or (
        isinstance(node, ast.Tuple) and all(_is_static_dict_key(element) for element in node.elts)
    )


def _is_unbounded_accumulator_value(
    value: ast.expr,
    module_aliases: dict[str, str],
    from_imports: dict[str, str],
) -> bool:
    if isinstance(value, ast.List):
        return len(value.elts) != 1
    return _classify_unbounded_accumulator_call_or_collection(value, module_aliases, from_imports)


def _classify_unbounded_accumulator_call_or_collection(
    value: ast.expr,
    module_aliases: dict[str, str],
    from_imports: dict[str, str],
) -> bool:
    if isinstance(value, ast.Dict):
        if value.keys is None or not value.keys:
            return True
        keys: list[ast.expr | None] = value.keys
        return not all(_is_static_dict_key(k) for k in keys if k is not None)
    if isinstance(value, ast.Set):
        if not value.elts:
            return True
        return not all(_is_static_dict_key(e) for e in value.elts)
    return _classify_unbounded_accumulator_call(value, module_aliases, from_imports)


def _classify_unbounded_accumulator_call(
    value: ast.expr,
    module_aliases: dict[str, str],
    from_imports: dict[str, str],
) -> bool:
    if not isinstance(value, ast.Call):
        return False
    canonical = _canonical_name(_dotted_name(value), module_aliases, from_imports)
    if canonical == "set":
        return True
    if canonical in {"deque", "collections.deque"}:
        return not _keyword_value_is_positive_int(value, "maxlen")
    if canonical in _ORDERED_DICT_NAMES:
        return True
    if canonical in _DEFAULT_DICT_NAMES:
        return True
    return canonical in {"list", "dict"} and not value.args


def _is_module_level_or_self_init(
    targets: list[ast.expr],
    enclosing_function: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    is_module_level = enclosing_function is None
    is_init = enclosing_function is not None and enclosing_function.name == "__init__"
    if not (is_module_level or is_init):
        return False
    for target in targets:
        if isinstance(target, ast.Name) and is_module_level:
            if target.id == "__all__":
                return False
            continue
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            if target.value.id == "self" and is_init:
                continue
            return False
        return False
    return True


class ResourceLifecycleAuditor(ast.NodeVisitor):
    def __init__(
        self,
        file_path: str,
        rel_path: str,
        registry_path: str,
        *,
        module_aliases: dict[str, str] | None = None,
        from_imports: dict[str, str] | None = None,
    ) -> None:
        self.file_path = file_path
        self.rel_path = rel_path
        self.registry_path = registry_path
        self.tree: ast.Module | None = None
        self.violations: list[ResourceLifecycleViolation] = []
        self._module_aliases = module_aliases or {}
        self._from_imports = from_imports or {}
        self._enclosing_function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self._enclosing_class: ast.ClassDef | None = None
        self._in_with_calls: set[ast.Call] | None = None
        self._parents: dict[ast.AST, ast.AST] = {}

    def _registered(self, node: ast.AST, category: str) -> bool:
        scope = qualified_scope(node, self._parents)
        return (self.registry_path, scope, category) in EXEMPT_CALL_SITES

    def _add(self, node: ast.AST, category: str, detail: str) -> None:
        if self._registered(node, category):
            return
        lineno: int = getattr(node, "lineno", 0)
        self.violations.append(
            ResourceLifecycleViolation(
                file_path=self.file_path,
                line=lineno,
                category=category,
                detail=detail,
            )
        )

    def visit_Module(self, node: ast.Module) -> None:
        self.tree = node
        self._in_with_calls = _collect_in_with_calls(node)
        self._parents = build_parent_map(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_class = self._enclosing_class
        self._enclosing_class = node
        self.generic_visit(node)
        self._enclosing_class = previous_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous_function = self._enclosing_function
        self._enclosing_function = node
        self.generic_visit(node)
        self._enclosing_function = previous_function

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        previous_function = self._enclosing_function
        self._enclosing_function = node
        self.generic_visit(node)
        self._enclosing_function = previous_function

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_accumulator_assignment(node.value, node.targets, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._check_accumulator_assignment(node.value, [node.target], node)
        self.generic_visit(node)

    def _check_accumulator_assignment(
        self,
        value: ast.expr,
        targets: list[ast.expr],
        anchor: ast.AST,
    ) -> None:
        if not _is_unbounded_accumulator_value(value, self._module_aliases, self._from_imports):
            return
        if not _is_module_level_or_self_init(targets, self._enclosing_function):
            return
        rendered_targets = ", ".join(ast.unparse(t) for t in targets)
        rendered_value = ast.unparse(value)
        self._add(
            anchor,
            "unbounded_accumulator",
            f"{rendered_targets} = {rendered_value} — long-lived mutable "
            "accumulator without a FIFO/size cap; use deque(maxlen=...), "
            "OrderedDict + count cap, or register the call site in "
            "EXEMPT_CALL_SITES",
        )

    def visit_Call(self, node: ast.Call) -> None:
        canonical = _canonical_name(_dotted_name(node), self._module_aliases, self._from_imports)

        if canonical in _THREAD_NAMES and not _keyword_truthy(node, "daemon"):
            self._add(
                node,
                "non_daemon_thread",
                f"{canonical}() without daemon=True — non-daemon threads can block process exit",
            )

        if canonical in _HTTP_CLIENT_NAMES:
            tree = self.tree
            assert tree is not None, "visit_Module must run before visit_Call"
            in_with_calls = self._in_with_calls
            assert in_with_calls is not None, (
                "visit_Module must populate _in_with_calls before visit_Call"
            )
            if node not in in_with_calls:
                self._add(
                    node,
                    "bare_http_client",
                    f"{canonical}() constructed outside a `with` "
                    "statement — leaks the HTTP connection pool",
                )

        if canonical in _RAW_OS_FD_NAMES and not self._is_in_raw_fd_allowlist():
            self._add(
                node,
                "raw_os_fd",
                f"{canonical}() outside ralph/process/ — raw fd "
                "creation must be centralized in the process lifecycle "
                "layer (relocate or register the call site in "
                "EXEMPT_CALL_SITES)",
            )

        if (
            canonical in _DIRECT_CHILD_PROCESS_NAMES
            or (canonical is not None and canonical.startswith("os.spawn"))
        ) and not self._is_in_raw_fd_allowlist():
            self._add(
                node,
                "direct_child_spawn",
                f"{canonical}() outside ralph/process/ — child creation must flow "
                "through ProcessManager (relocate or register the call site in "
                "EXEMPT_CALL_SITES)",
            )

        self.generic_visit(node)

    def _is_in_raw_fd_allowlist(self) -> bool:
        rel = self.rel_path.replace("\\", "/")
        for prefix in _RAW_OS_FD_ALLOWLIST_DIRS:
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        return False


_IMPORT_KEYWORDS: tuple[str, ...] = (
    "from threading",
    "import threading",
    "from asyncio",
    "import asyncio",
    "from httpx",
    "import httpx",
    "from requests",
    "import requests",
    "from urllib3",
    "import urllib3",
    "import subprocess",
    "from subprocess",
    "from os import",
    "import os",
    "import threading as",
    "import httpx as",
    "import requests as",
    "import os as",
    "import asyncio as",
)


def _source_needs_import_resolution(source: str) -> bool:
    return any(needle in source for needle in _IMPORT_KEYWORDS)


_AUDIT_SUBSTRINGS: tuple[str, ...] = (
    "Thread(",
    "threading.Thread",
    "from threading",
    "import threading",
    "Client(",
    "AsyncClient(",
    "Session(",
    "httpx",
    "requests",
    "os.open(",
    "os.openpty(",
    "os.pipe(",
    "subprocess",
    "subprocess.run(",
    "subprocess.call(",
    "subprocess.check_call(",
    "subprocess.check_output(",
    "subprocess.Popen(",
    "subprocess.getoutput(",
    "subprocess.getstatusoutput(",
    "os.system(",
    "system(",
    "os.spawn",
    "spawn",
    "os.posix_spawn(",
    "posix_spawn(",
    "os.fork(",
    "fork(",
    "asyncio.create_subprocess_exec(",
    "asyncio.create_subprocess_shell(",
    "deque(",
    "OrderedDict(",
    "defaultdict(",
    "list()",
    "dict()",
    "set()",
    "= []",
    "= {}",
    "= set()",
    "= list()",
    "= dict()",
    "= deque(",
    "= OrderedDict(",
    "= defaultdict(",
)


def _source_has_audit_pattern(source: str) -> bool:
    return any(needle in source for needle in _AUDIT_SUBSTRINGS)


def registry_path(file_path: Path) -> str:
    repository_root = Path(__file__).parent.parent.parent
    try:
        return file_path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


def audit_resource_lifecycle_file(file_path: Path) -> list[ResourceLifecycleViolation]:
    package_root = Path(__file__).parent.parent
    try:
        rel_path = file_path.resolve().relative_to(package_root.resolve()).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()
    exemption_key_path = registry_path(file_path)
    source = file_path.read_text(encoding="utf-8")
    if not _source_has_audit_pattern(source):
        return []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    module_aliases, from_imports = _collect_import_aliases_if_needed(source, tree)
    auditor = ResourceLifecycleAuditor(
        str(file_path),
        rel_path,
        exemption_key_path,
        module_aliases=module_aliases,
        from_imports=from_imports,
    )
    auditor.visit(tree)
    return auditor.violations


def audit_resource_lifecycle_directory(
    root: Path,
) -> tuple[list[ResourceLifecycleViolation], int]:
    all_violations: list[ResourceLifecycleViolation] = []
    files_checked = 0
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(root).parts):
            continue
        all_violations.extend(audit_resource_lifecycle_file(py_file))
        files_checked += 1
    return all_violations, files_checked


def _default_roots() -> list[Path]:
    package_root = Path(__file__).parent.parent
    return [
        package_root / "mcp",
        package_root / "agents",
        package_root / "executor",
        package_root / "process",
        package_root / "pipeline",
        package_root / "runtime",
        package_root / "pro_support",
        package_root / "recovery",
        package_root / "display",
        package_root / "prompts",
        package_root / "diagnostics",
        package_root / "api",
        package_root / "update_check",
        package_root / "contrib",
        package_root / "git",
        package_root / "cli",
        package_root / "telemetry",
        package_root / "policy",
        package_root / "language_detector",
        package_root / "workspace",
        package_root / "phases",
        package_root / "guidelines",
        package_root / "checkpoint",
        package_root / "config",
        package_root / "exit_pause",
        package_root / "files",
        package_root / "platform",
        package_root / "project_policy",
        package_root / "skills",
        package_root / "interrupt",
    ]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    roots = [Path(a) for a in args] if args else _default_roots()

    for root in roots:
        if not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    all_violations: list[ResourceLifecycleViolation] = []
    total_files = 0
    for root in roots:
        print(f"Auditing resource-lifecycle contract in: {root}")
        violations, files_checked = audit_resource_lifecycle_directory(root)
        all_violations.extend(violations)
        total_files += files_checked
    print()

    if all_violations:
        print(
            f"RESOURCE-LIFECYCLE CONTRACT VIOLATIONS: {len(all_violations)} "
            f"in {total_files} file(s)"
        )
        print("=" * 72)
        for v in all_violations:
            print(f"  {v}")
        print()
        print(
            "Production code MUST use daemon=True threads, with-managed HTTP "
            "clients, raw os fd creation only under ralph/process/, and "
            "long-lived mutable accumulators must carry a FIFO/size cap "
            "(deque(maxlen=...), OrderedDict + count cap). Register a call "
            "site in EXEMPT_CALL_SITES as (file, qualified callable, "
            "category) when the lifecycle contract is genuinely bounded."
        )
        return 1

    print(f"No resource-lifecycle violations found in {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
