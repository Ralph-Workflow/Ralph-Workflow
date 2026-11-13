from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_BOUNDARY_HELPERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("_fallback_http_handler", "_load_envelope"),
        ("_fallback_http_handler", "_parse_envelope"),
        ("claude", "parse_event"),
        ("codex", "parse_event"),
        ("gemini", "parse_event"),
        ("pi", "parse_event"),
        ("opencode", "parse_event"),
        ("cursor", "parse_event"),
        ("nanocoder", "parse_event"),
        ("agy", "parse_event"),
        ("generic", "parse_event"),
        ("_ndjson_base", "parse_event"),
        ("_template", "parse_event"),
        ("claude_interactive_transcript_parser", "parse_event"),
        ("claude_interactive", "parse_event"),
        ("interactive_transcript_event", "parse_event"),
        ("base", "parse_event"),
        ("agent_output_line", "parse_event"),
        ("text_accumulator", "parse_event"),
        ("_event_classification", "parse_event"),
        ("invoke", "parse_event"),
        ("_process_reader", "parse_event"),
        ("_pty_line_reader", "parse_event"),
        ("_tool_call_extraction", "parse_event"),
        ("_completion", "parse_event"),
        ("edit_preview", "_lexer_for_path"),
        ("process_termination_issuer", "unverified_issuer"),
    }
)


_CAST_POLICY_EXEMPT_STEMS: frozenset[str] = frozenset(
    {
        "audit_cast_policy",
        "test_audit_cast_policy",
    }
)


_UNIVERSAL_LEAF_TYPES: frozenset[str] = frozenset(
    {
        "object",
        "Any",
        "None",
        "NoneType",
    }
)

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        "tmp",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
    }
)

_CAST_CALL_RE = re.compile(
    r"\bcast\s*\(\s*"
    r"(?:"
    r"([\"'])([^\"']+?)\1"
    r"|"
    r"([A-Za-z_][\w\[\] ,.|]*)"
    r")"
)

_TEST_FAKE_VALUE_RE = re.compile(r"\bfake_value\s*\(")

_SEAM_MARKER_RE = re.compile(r"#\s*cast-policy\s*:\s*seam\s*:")

SEAM_EXEMPT_CALL_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("ralph/agents/builtin_spec.py", "tuple[Field[str], ...]"),
        ("ralph/agents/execution_state/_harness_echo.py", "list[str]"),
        (
            "ralph/agents/execution_state/opencode_execution_strategy.py",
            "ChildLivenessRegistry | None",
        ),
        ("ralph/agents/invoke/_command_builders/__init__.py", "AgentTransport"),
        ("ralph/agents/invoke/_commands.py", "AgentTransport"),
        ("ralph/agents/invoke/_completion.py", "int | None"),
        ("ralph/agents/invoke/_completion_termination.py", "BaseException"),
        ("ralph/agents/invoke/_options.py", "float | None"),
        ("ralph/agents/invoke/_process_reader.py", "ChildLivenessRegistry | None"),
        ("ralph/agents/invoke/_process_reader.py", "IO[str] | None"),
        ("ralph/agents/invoke/_process_reader.py", "Path | None"),
        ("ralph/agents/invoke/_process_reader.py", "TextIO"),
        ("ralph/agents/invoke/_process_reader.py", "int | None"),
        ("ralph/agents/invoke/_pty_line_reader.py", "AttemptSupervisor"),
        ("ralph/agents/invoke/_pty_line_reader.py", "ChildLivenessRegistry | None"),
        ("ralph/agents/invoke/_pty_line_reader.py", "Path | None"),
        ("ralph/agents/invoke/_pty_line_reader.py", "_MergedDiagType"),
        ("ralph/agents/invoke/_pty_line_reader.py", "dict[str, str] | None"),
        ("ralph/agents/invoke/_pty_line_reader.py", "frozenset[str] | None"),
        ("ralph/agents/invoke/_pty_line_reader.py", "int | None"),
        ("tests/agents/invoke/test_pending_subagents_turn_end.py", "ManagedPtyProcess"),
        ("tests/agents/test_subprocess_executor_terminate_await_order.py", "ManagedAsyncProcess"),
        ("tests/agents/test_subprocess_executor_terminate_await_order.py", "ProcessManager"),
        (
            "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py",
            "Callable[[float | None], int] | None",
        ),
        (
            "ralph/agents/invoke/attempt_supervision/attempt_supervisor.py",
            "_SignalFn | None",
        ),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", "Mapping[str, str]"),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", "_InvokeCompatibilitySeam"),
        ("ralph/agents/invoke/_workspace.py", "_HandlerWithDispatch"),
        ("ralph/agents/parsers/__init__.py", "Mapping[str, _ParserFactory]"),
        ("ralph/agents/parsers/__init__.py", "_CatalogModule"),
        ("ralph/agents/parsers/_ndjson_base.py", "_SubagentSourceLabel"),
        ("ralph/agents/parsers/gemini.py", "JsonDict"),
        ("ralph/agents/registration.py", "StrategyFactory"),
        ("ralph/agents/registration.py", "_ParserFactory"),
        ("ralph/agents/spec.py", "AgentTransport"),
        ("ralph/cli/commands/commit.py", "UnifiedConfig"),
        ("ralph/cli/commands/explain.py", "_LoadPolicyFn"),
        ("ralph/cli/commands/explain.py", "_RenderExplanationFn"),
        ("ralph/cli/commands/run.py", "PolicyBundle"),
        ("ralph/cli/commands/run.py", "_RunnerFunc | None"),
        ("ralph/cli/commands/run.py", "_RunnerModule"),
        ("ralph/cli/commands/run.py", "int"),
        ("ralph/cli/main.py", "click.Command"),
        ("ralph/config/agent_detection.py", "str"),
        ("ralph/config/loader.py", "type[ValueError]"),
        ("ralph/display/activity_router.py", "type[AgentParser]"),
        ("ralph/display/context.py", "SignalGetter"),
        ("ralph/display/context.py", "SignalSetter"),
        ("ralph/display/parallel_display.py", "int"),
        ("ralph/display/raw_overflow.py", "BinaryIO"),
        ("ralph/display/subscriber.py", "type[_WaitingEventLike]"),
        ("ralph/display/subscriber.py", "type[_WaitingKindLike]"),
        ("ralph/git/commit_review.py", "_RepoClass"),
        ("ralph/git/conflict_stage_entries.py", "_GitRunner"),
        ("ralph/git/operations.py", "Repo"),
        ("ralph/git/subprocess_runner.py", "_PopenExitProtocol"),
        ("ralph/git/subprocess_runner.py", "_QueuedRunGit"),
        ("ralph/guidelines/stack.py", "Sequence[str]"),
        ("ralph/guidelines/stack.py", "_HandlerCallable"),
        ("ralph/guidelines/stack.py", "_StackDetector"),
        ("ralph/guidelines/stack.py", "list[str]"),
        ("ralph/interrupt/controller.py", "Iterable[int]"),
        ("ralph/interrupt/controller.py", "SignalGetter"),
        ("ralph/interrupt/controller.py", "SignalSetter"),
        ("ralph/interrupt/dispatcher.py", "Iterable[int]"),
        ("ralph/interrupt/dispatcher.py", "SignalGetter"),
        ("ralph/interrupt/dispatcher.py", "SignalSetter"),
        ("ralph/interrupt/dispatcher.py", "_PsutilModule"),
        ("ralph/logging.py", "SinkAdder"),
        ("ralph/mcp/artifacts/commit_message.py", "str"),
        ("ralph/mcp/artifacts/markdown/_parser.py", "str"),
        ("ralph/mcp/artifacts/markdown/_parser.py", "str | None"),
        ("ralph/mcp/artifacts/markdown/specs/_plan_work_units.py", "list[str]"),
        ("ralph/mcp/artifacts/markdown/specs/_plan_work_units.py", "str"),
        ("ralph/mcp/artifacts/policy_outcomes.py", "object | None"),
        ("ralph/mcp/explore/_bench_r6_metrics.py", "_QueryCallable"),
        ("ralph/mcp/explore/_handlers_reindex.py", "Workspace"),
        ("ralph/mcp/explore/_store_class.py", "list[sqlite3.Row]"),
        ("ralph/mcp/explore/_store_class_content_cache.py", "list[sqlite3.Row]"),
        ("ralph/mcp/explore/_store_class_file_rows.py", "Mapping[str, int | str]"),
        ("ralph/mcp/explore/_store_class_file_rows.py", "list[sqlite3.Row]"),
        ("ralph/mcp/explore/_store_types.py", "int | str | float"),
        ("ralph/mcp/explore/_store_types.py", "list[sqlite3.Row]"),
        ("ralph/mcp/explore/bench.py", "McpConfig"),
        ("ralph/mcp/explore/dirty_paths.py", "ExploreStoreLike | None"),
        ("ralph/mcp/explore/graph.py", "dict[str, str]"),
        ("ralph/mcp/explore/handlers.py", "dict[str, str]"),
        ("ralph/mcp/explore/lifecycle.py", "ReindexResult"),
        ("ralph/mcp/explore/lifecycle.py", "ReindexRunner"),
        ("ralph/mcp/protocol/_startup_http.py", "JsonRpcResponse"),
        ("ralph/mcp/protocol/_startup_http.py", "str | None"),
        ("ralph/mcp/protocol/startup.py", "JsonRpcResponse"),
        ("ralph/mcp/server/_fallback_standalone_server.py", "tuple[str, int]"),
        ("ralph/mcp/server/_host_contention_signal.py", "_PsutilModule"),
        ("ralph/mcp/server/_host_contention_signal.py", "_PsutilModuleLike"),
        ("ralph/mcp/server/_http_tools_list.py", "IO[bytes]"),
        ("ralph/mcp/server/_http_tools_list.py", "str"),
        ("ralph/mcp/server/_mcp_server.py", "_ModelDump | None"),
        ("ralph/mcp/server/_mcp_server.py", "_ToDict | None"),
        ("ralph/mcp/server/_process_secrecy.py", "_GetCurrentProcess"),
        ("ralph/mcp/server/_process_secrecy.py", "_NativeFunction"),
        ("ralph/mcp/server/_process_secrecy.py", "_WindowsLibraryLoader"),
        ("ralph/mcp/server/_reserved_socket.py", "int"),
        ("ralph/mcp/server/_reserved_socket.py", "str"),
        ("ralph/mcp/server/_runtime_polling.py", "tuple[Path, ...]"),
        ("ralph/mcp/server/lifecycle.py", "Path | str | None"),
        ("ralph/mcp/server/lifecycle.py", "_RestartFnProtocol"),
        ("ralph/mcp/server/runtime.py", "Path"),
        ("ralph/mcp/server/runtime.py", "int"),
        ("ralph/mcp/server/runtime.py", "str"),
        ("ralph/mcp/server/runtime.py", "str | None"),
        ("ralph/mcp/server/runtime.py", "tuple[Path, ...]"),
        ("ralph/mcp/tools/artifact.py", "Path | None"),
        ("ralph/mcp/tools/bridge/_lazy_tool_handler.py", "ToolHandler"),
        ("ralph/mcp/tools/bridge/_registry.py", "_ReadableWorkspace"),
        ("ralph/mcp/tools/bridge/_registry.py", "type[McpConfig]"),
        ("ralph/mcp/tools/bridge/_spec_helpers.py", "str"),
        ("ralph/mcp/tools/bridge/_upstream_proxy_handler.py", "HasMediaManifest | None"),
        ("ralph/mcp/tools/bridge/_upstream_proxy_handler.py", "Workspace | None"),
        ("ralph/mcp/tools/git_read.py", "Path | str | None"),
        ("ralph/mcp/tools/git_read.py", "_ReadablePipe"),
        ("ralph/mcp/tools/md_artifact.py", "Path | None"),
        ("ralph/mcp/tools/workspace/_read_handlers.py", "ExploreIndexLike | None"),
        ("ralph/mcp/tools/workspace/_read_handlers.py", "SpanRow"),
        ("ralph/mcp/tools/workspace/_read_handlers.py", "SymbolRow"),
        ("ralph/mcp/tools/workspace/_utils.py", "object | None"),
        ("ralph/mcp/tools/workspace/_write_handlers.py", "ExploreIndexLike | None"),
        ("ralph/mcp/tools/workspace/_write_handlers.py", "ExploreStore"),
        ("ralph/mcp/transport/common.py", "tuple[Path, ...]"),
        ("ralph/mcp/transport/nanocoder.py", "dict[str, str]"),
        ("ralph/mcp/upstream/config.py", "McpServerOrigin"),
        ("ralph/mcp/websearch/backends/ddgs.py", "Callable[[], _DdgsTextClient] | None"),
        ("ralph/mcp/websearch/backends/exa.py", "_ExaType"),
        ("ralph/mcp/websearch/backends/exa.py", "object | None"),
        ("ralph/mcp/websearch/backends/tavily.py", "_TavilyClientType"),
        ("ralph/mcp/websearch/backends/tavily.py", "object | None"),
        ("ralph/phases/artifacts.py", "ArtifactContract"),
        ("ralph/pipeline/_commit_executor_artifacts.py", "PolicyBundle | None"),
        ("ralph/pipeline/_commit_executor_artifacts.py", "str | None"),
        ("ralph/pipeline/_phase_transition_summary.py", "_SnapshotModule"),
        ("ralph/pipeline/_runner_interrupt.py", "SignalGetter"),
        ("ralph/pipeline/_runner_interrupt.py", "SignalSetter"),
        ("ralph/pipeline/_runner_session.py", "_EffectExecutorModule"),
        ("ralph/pipeline/activity_stream.py", "AgentTransport"),
        ("ralph/pipeline/activity_stream.py", "AgentTransport | None"),
        ("ralph/pipeline/activity_stream.py", "DisplayContext | None"),
        ("ralph/pipeline/activity_stream.py", "_ParallelDisplayModule"),
        ("ralph/pipeline/activity_stream.py", "deque[str] | list[str] | None"),
        ("ralph/pipeline/auto_integrate_catchup.py", "str"),
        ("ralph/pipeline/commit_executor.py", "DisplayContext"),
        ("ralph/pipeline/commit_executor.py", "ParallelDisplay"),
        ("ralph/pipeline/commit_executor.py", "Verbosity"),
        ("ralph/pipeline/effect_executor.py", "AgentConfig | None"),
        ("ralph/pipeline/effect_executor.py", "InvokeOptions | None"),
        ("ralph/pipeline/effect_executor.py", "Path | None"),
        ("ralph/pipeline/effect_executor.py", "_InvokeAgentFn | None"),
        ("ralph/pipeline/effect_executor.py", "_KwargsMaterializeMasterPromptFn"),
        ("ralph/pipeline/effect_executor.py", "_RegistryLike"),
        ("ralph/pipeline/effect_executor.py", "bool"),
        ("ralph/pipeline/effect_executor.py", "str"),
        ("ralph/pipeline/effect_executor.py", "type[Exception] | None"),
        ("ralph/pipeline/factory.py", "DisplayContext"),
        ("ralph/pipeline/factory.py", "MaterializeMasterPromptFn"),
        ("ralph/pipeline/factory.py", "MultimodalModelIdentity | None"),
        ("ralph/pipeline/factory.py", "PhasePromptMaterializerFn"),
        ("ralph/pipeline/fan_out.py", "AgentTransport | None"),
        ("ralph/pipeline/fan_out.py", "Console | None"),
        ("ralph/pipeline/fan_out.py", "Path | None"),
        ("ralph/pipeline/fan_out.py", "PolicyBundle"),
        ("ralph/pipeline/fan_out.py", "UnifiedConfig | None"),
        ("ralph/pipeline/fan_out.py", "WorkspaceScope"),
        ("ralph/pipeline/fan_out.py", "_ExecutorFactory"),
        ("ralph/pipeline/fan_out.py", "_ExecutorFactory | None"),
        ("ralph/pipeline/fan_out.py", "_InstallSignalHandlersFn"),
        ("ralph/pipeline/fan_out.py", "_InstallSignalHandlersFn | None"),
        ("ralph/pipeline/fan_out.py", "_McpFactory"),
        ("ralph/pipeline/fan_out.py", "_McpFactory | None"),
        ("ralph/pipeline/fan_out.py", "_PipelineSubscriberLike | None"),
        ("ralph/pipeline/fan_out.py", "_ReducerReduceFn"),
        ("ralph/pipeline/fan_out.py", "_ReducerReduceFn | None"),
        ("ralph/pipeline/fan_out.py", "_RunProcessAsyncFn"),
        ("ralph/pipeline/fan_out.py", "_RunProcessAsyncFn | None"),
        ("ralph/pipeline/fan_out.py", "str"),
        ("ralph/pipeline/fan_out.py", "str | None"),
        ("ralph/pipeline/fan_out.py", "type[ParallelDisplay]"),
        ("ralph/pipeline/phase_agent_handler.py", "ParallelDisplay"),
        ("ralph/pipeline/phase_agent_handler.py", "_ArtifactReaderModule"),
        ("ralph/pipeline/phase_transition.py", "str | None"),
        ("ralph/pipeline/plumbing/__init__.py", "_CommitPlumbingModule"),
        ("ralph/pipeline/plumbing/__init__.py", "_SmokePlumbingModule"),
        ("ralph/pipeline/plumbing/commit_plumbing.py", "AgentTransport"),
        ("ralph/pipeline/plumbing/commit_plumbing.py", "RestartAwareMcpBridge"),
        ("ralph/pipeline/plumbing/commit_plumbing.py", "UnifiedConfig"),
        ("ralph/pipeline/plumbing/smoke_plumbing.py", "AgentTransport"),
        ("ralph/pipeline/plumbing/smoke_plumbing.py", "RestartAwareMcpBridge"),
        ("ralph/pipeline/reducer.py", "PipelineEvent"),
        ("ralph/pipeline/run_loop.py", "PipelineSubscriber | None"),
        ("ralph/pipeline/run_loop.py", "_DisplayContextOwner"),
        ("ralph/pipeline/run_loop.py", "_PhaseAwareDisplay"),
        ("ralph/pipeline/run_loop.py", "_RegistryLike"),
        ("ralph/pipeline/run_loop.py", "_RunEndDisplay"),
        ("ralph/pipeline/run_loop.py", "_RunPipelineStepFn"),
        ("ralph/pipeline/run_loop.py", "int"),
        ("ralph/pipeline/run_loop.py", "str | None"),
        ("ralph/pipeline/runner.py", "Event"),
        ("ralph/pipeline/runner.py", "_ExecuteEffectKwargsFn"),
        ("ralph/pipeline/state.py", "AgentChainState"),
        ("ralph/pipeline/state.py", "FalloverRecord"),
        ("ralph/pipeline/state.py", "int"),
        ("ralph/policy/_policy_validation.py", "Mapping[str, PhaseDefinition]"),
        ("ralph/policy/_policy_validation.py", "ValidationErrorDetails"),
        ("ralph/policy/loader.py", "Sequence[str]"),
        ("ralph/policy/loader.py", "str"),
        ("ralph/pro_support/state_query.py", "int"),
        ("ralph/process/manager/__init__.py", "_Prctl"),
        ("ralph/process/manager/_git_termination.py", "_PsutilModuleLike"),
        ("ralph/process/manager/_process_manager_runtime.py", "_PsutilModuleLike"),
        ("ralph/process/manager/_supervisor.py", "_Prctl"),
        ("ralph/project_policy/_prompt_ui.py", "_Questionary"),
        ("ralph/prompts/materialize.py", "ArtifactsPolicy | None"),
        ("ralph/prompts/materialize.py", "Path"),
        ("ralph/prompts/materialize.py", "Path | None"),
        ("ralph/prompts/materialize.py", "PipelinePolicy"),
        ("ralph/prompts/materialize.py", "SessionCapabilities"),
        ("ralph/prompts/materialize.py", "WorkUnit | None"),
        ("ralph/prompts/materialize.py", "Workspace"),
        ("ralph/prompts/materialize.py", "bool"),
        ("ralph/prompts/materialize.py", "str"),
        ("ralph/prompts/materialize.py", "str | None"),
        ("ralph/prompts/template_engine.py", "str"),
        ("ralph/prompts/template_engine.py", "type[Undefined]"),
        ("ralph/pydantic_validation_errors.py", "ValidationErrorDetails"),
        ("ralph/recovery/controller.py", "Callable[[str, str], str]"),
        ("ralph/recovery/controller.py", "_RetryHintPath"),
        ("ralph/recovery/controller.py", "dict[str, int]"),
        ("ralph/recovery/failure_classifier.py", "BaseException | None"),
        ("ralph/telemetry/_sentry.py", "_BreadcrumbRecorder"),
        ("ralph/telemetry/_sentry.py", "_MetricCounter"),
        ("ralph/telemetry/_sentry.py", "_MetricDistribution"),
        ("ralph/telemetry/_sentry.py", "_SentryInit"),
        ("ralph/telemetry/_sentry.py", "_SentrySetUser"),
        ("ralph/telemetry/_sentry.py", "str"),
        ("ralph/telemetry/_sentry.py", "tuple[str, ...]"),
        ("ralph/testing/_fake_popen.py", "bool"),
        ("ralph/testing/_fake_popen.py", "int"),
        ("ralph/testing/_fake_popen.py", "int | None"),
        ("ralph/testing/audit_agent_internal_paths.py", "Callable[[str], bool]"),
        ("ralph/testing/audit_fenced_artifact_examples.py", "list[str]"),
        ("ralph/testing/audit_fenced_artifact_examples.py", "str"),
        ("ralph/testing/audit_fenced_artifact_examples.py", "type[Undefined]"),
        ("ralph/testing/audit_template_render_integrity.py", "_IfNodeView"),
        ("ralph/testing/audit_template_render_integrity.py", "_NameNodeView"),
        ("ralph/testing/audit_template_render_integrity.py", "list[str]"),
        ("ralph/testing/pytest_timeout_plugin.py", "_PsutilMod"),
        ("ralph/testing/pytest_timeout_plugin.py", "_SuiteWatchdog | None"),
        ("ralph/verify_timeout.py", "float"),
        ("ralph/verify_timeout.py", "list[str]"),
        ("ralph/workspace/scope.py", "tuple[Path, ...]"),
    }
)


@dataclass(frozen=True)
class CastPolicyViolation:
    file_path: str
    line: int
    category: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [CAST-POLICY] {self.category}: {self.detail}"


def _is_test_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return len(parts) > 0 and parts[0] == "tests"


def _is_test_audit_file(file_stem: str) -> bool:
    return file_stem in _CAST_POLICY_EXEMPT_STEMS


def _is_inside_triple_quoted(lines: list[str], line_index: int) -> bool:
    in_triple = False
    for i in range(line_index + 1):
        stripped = lines[i].strip()
        count = stripped.count('"""') + stripped.count("'''")
        if count % 2 == 1:
            in_triple = not in_triple
    return in_triple


def _is_inside_string_literal(line: str, pos: int) -> bool:
    in_string: str | None = None
    escaped = False
    for ch in line[:pos]:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string is not None:
            escaped = True
            continue
        if in_string is not None:
            if ch == in_string:
                in_string = None
            continue
        if ch == "#":
            break
        if ch in ('"', "'"):
            in_string = ch
    return in_string is not None


def _extract_outer_and_params(target: str) -> tuple[str, str]:
    bracket = target.find("[")
    if bracket <= 0:
        return target.strip().strip("'\""), ""
    outer = target[:bracket].strip().strip("'\"")
    depth = 0
    end = -1
    for i in range(bracket, len(target)):
        if target[i] == "[":
            depth += 1
        elif target[i] == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return target.strip().strip("'\""), ""
    params = target[bracket + 1 : end]
    return outer, params


def _leaf_types_in_params(params: str) -> list[str]:
    if not params.strip():
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in params:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    leaves: list[str] = []
    for part in parts:
        leaves.extend(_leaf_types_from_one(part))
    return leaves


def _leaf_types_from_one(part: str) -> list[str]:
    _outer, params = _extract_outer_and_params(part)
    if params:
        return _leaf_types_in_params(params)
    return [leaf.strip().strip("'\"") for leaf in part.split("|") if leaf.strip()]


def _all_leaves_universal(target: str) -> bool:
    outer, params = _extract_outer_and_params(target)
    leaves = _leaf_types_in_params(params)
    if not leaves:
        return outer in _UNIVERSAL_LEAF_TYPES
    return all(leaf in _UNIVERSAL_LEAF_TYPES for leaf in leaves)


_CONTAINER_OUTER_TYPES: frozenset[str] = frozenset(
    {
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "Mapping",
        "Sequence",
        "Iterable",
        "Iterator",
        "MutableMapping",
        "MutableSequence",
        "Callable",
        "type",
        "Optional",
        "Union",
    }
)


def _is_sound_dict_widening(target: str) -> bool:
    outer, params = _extract_outer_and_params(target)
    if not params:
        return False
    if outer not in _CONTAINER_OUTER_TYPES:
        return False
    leaves = _leaf_types_in_params(params)
    if not leaves:
        return False
    return any(leaf in _UNIVERSAL_LEAF_TYPES for leaf in leaves)


def _is_in_boundary_helper(
    rel_path: str,
    line_index: int,
    lines: list[str],
    target: str,
) -> bool:
    file_stem = Path(rel_path).stem
    helper_names = _collect_enclosing_helpers(lines, line_index)
    for helper_name in helper_names:
        if (file_stem, helper_name) in _BOUNDARY_HELPERS:
            return True
    target_helper_names = {name for _, name in _BOUNDARY_HELPERS}
    return bool(any(name in target_helper_names for name in helper_names))


def _collect_enclosing_helpers(lines: list[str], line_index: int) -> list[str]:
    helper_names: list[str] = []
    indent_of_current: int | None = None
    for i in range(line_index - 1, -1, -1):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if re.match(r"(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped):
            name_match = re.match(r"(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
            assert name_match is not None
            name = name_match.group(1)
            if indent_of_current is None or indent < indent_of_current:
                helper_names.append(name)
                indent_of_current = indent
                if indent == 0:
                    return helper_names
        if re.match(r"class\s+[A-Za-z_]", stripped):
            return helper_names
    return helper_names


def _paren_depth_of_line(raw: str) -> int:
    depth = 0
    in_string: str | None = None
    in_comment = False
    j = 0
    while j < len(raw):
        ch = raw[j]
        if in_comment:
            break
        if in_string is not None:
            if ch == "\\" and j + 1 < len(raw):
                j += 2
                continue
            if ch == in_string:
                in_string = None
            j += 1
            continue
        if ch == "#":
            in_comment = True
            break
        if ch in ("'", '"'):
            in_string = ch
            j += 1
            continue
        if ch in {"(", "[", "{"}:
            depth += 1
        elif ch in {")", "]", "}"}:
            depth -= 1
        j += 1
    return depth


def _find_cast_expression_end(lines: list[str], line_index: int) -> int:
    depth_before = 0
    if line_index > 0:
        depth_before = _paren_depth_of_line(lines[line_index - 1])
    depth = depth_before + _paren_depth_of_line(lines[line_index])

    for i in range(line_index + 1, len(lines)):
        depth += _paren_depth_of_line(lines[i])
        if depth < depth_before:
            return i

    return len(lines) - 1


def _has_seam_marker(line: str, lines: list[str], line_index: int) -> bool:
    if _SEAM_MARKER_RE.search(line):
        return True
    end_line = _find_cast_expression_end(lines, line_index)
    return any(_SEAM_MARKER_RE.search(lines[i]) for i in range(line_index + 1, end_line + 1))


def _extract_cast_targets(line: str) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    for m in _CAST_CALL_RE.finditer(line):
        g2: str | None = m.group(2)
        g3: str | None = m.group(3)
        if g2 is not None:
            targets.append((g2, m.start(0)))
        elif g3 is not None:
            raw: str = g3.rstrip()
            if raw.endswith(",") or raw.endswith(")"):
                raw = raw[:-1].rstrip()
            if raw:
                targets.append((raw, m.start(0)))
    return targets


def _check_line_for_cast(
    line: str,
    rel_path: str,
    lines: list[str],
    lineno: int,
) -> list[CastPolicyViolation]:
    violations: list[CastPolicyViolation] = []
    file_stem = Path(rel_path).stem

    if _is_test_audit_file(file_stem):
        return violations

    if _is_inside_triple_quoted(lines, lineno - 1):
        return violations

    targets = _extract_cast_targets(line)
    targets = [(target, pos) for target, pos in targets if not _is_inside_string_literal(line, pos)]
    fake_match = _TEST_FAKE_VALUE_RE.search(line)
    fake_value_used = fake_match is not None and not _is_inside_string_literal(
        line, fake_match.start()
    )

    if not targets and not fake_value_used:
        return violations

    if _is_test_file(rel_path):
        if targets:
            for _target, _pos in targets:
                violations.append(
                    CastPolicyViolation(
                        file_path=rel_path,
                        line=lineno,
                        category="test-cast",
                        detail="cast in test file - tests must be fully typed",
                    )
                )
        if fake_value_used:
            violations.append(
                CastPolicyViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="test-cast",
                    detail=(
                        "fake_value(...) in test file - the policy forbids "
                        "test-accessor escape hatches in tests; use a typed "
                        "fixture or a TypeGuard narrowing predicate instead."
                    ),
                )
            )
        return violations

    for target, _pos in targets:
        if _all_leaves_universal(target):
            continue
        if _is_sound_dict_widening(target):
            continue
        if _is_in_boundary_helper(rel_path, lineno - 1, lines, target):
            continue
        if (rel_path, target) in SEAM_EXEMPT_CALL_SITES:
            continue
        if _has_seam_marker(line, lines, lineno - 1):
            continue
        violations.append(
            CastPolicyViolation(
                file_path=rel_path,
                line=lineno,
                category="forbidden-cast",
                detail=(
                    f"cast({target!r}) over external data - replace with "
                    f"ralph.checked_accessors, a narrowing predicate, or "
                    f"a named boundary helper. See "
                    f"docs/ralph-workflow-policy/typechecking-policy.md "
                    f"section 'Type assertions and casts'."
                ),
            )
        )

    return violations


def _find_cast_violations(lines: list[str], rel_path: str) -> list[CastPolicyViolation]:
    violations: list[CastPolicyViolation] = []
    for idx, raw_line in enumerate(lines):
        lineno = idx + 1
        if "cast(" not in raw_line and "fake_value(" not in raw_line:
            continue
        violations.extend(_check_line_for_cast(raw_line, rel_path, lines, lineno))
    return violations


def _is_venv_part(part: str) -> bool:
    if not part.startswith(".venv"):
        return False
    suffix = part[len(".venv") :]
    suffix = suffix.replace("-", "").replace(".", "")
    return suffix == "" or suffix.isdigit()


def _is_skipped_part(part: str) -> bool:
    return part in _SKIP_DIRS or _is_venv_part(part)


def _collect_py_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(_is_skipped_part(part) for part in path.relative_to(root).parts):
            continue
        yield path


def audit_codebase(codebase_root: Path) -> tuple[list[CastPolicyViolation], int]:
    all_violations: list[CastPolicyViolation] = []
    files_checked = 0

    for py_file in sorted(_collect_py_files(codebase_root)):
        files_checked += 1
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        rel_path = str(py_file.relative_to(codebase_root))
        violations = _find_cast_violations(lines, rel_path)
        all_violations.extend(violations)

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    codebase_root = Path(args[0]) if args else Path(__file__).parent.parent.parent

    if not codebase_root.is_dir():
        print(f"Error: directory not found: {codebase_root}", file=sys.stderr)
        return 2

    print(f"Auditing cast policy in: {codebase_root}")
    print()

    violations, files_checked = audit_codebase(codebase_root)

    if violations:
        print(
            f"CAST POLICY VIOLATIONS FOUND: {len(violations)} violation(s) "
            f"in {files_checked} file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print(
            "These casts assert the shape of external data without a runtime check. "
            "Replace them with ralph.checked_accessors, a TypeGuard narrowing "
            "predicate, or move the cast into a registered boundary helper. "
            "See docs/ralph-workflow-policy/typechecking-policy.md section "
            "'Type assertions and casts'."
        )
        return 1

    print(f"No cast-policy violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
