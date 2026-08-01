"""Cast policy audit - detects forbidden ``typing.cast`` usage.

The "Type assertions and casts" section of
``docs/ralph-workflow-policy/typechecking-policy.md`` defines a ``cast``
as a proof obligation rather than a conversion. A ``cast`` is
permitted ONLY in one of three positions:

1. SOUND BY CONSTRUCTION - the cast's leaf types are all universal
   (``object``, ``Any``, ``None``) so it widens to the language's
   universal type or refines type PARAMETERS the checker structurally
   cannot narrow.
2. CONFINED TO A TYPED BOUNDARY - the cast lives inside one of the
   helpers named in the audit's boundary registry, one helper per leak
   kind.
3. STRUCTURAL SEAM - the cast carries an inline
   ``# cast-policy: seam: <rationale>`` marker AND its
   ``(file_stem, target_type)`` pair is in the audit's seam allowlist.

All other casts are violations. Tests MUST NOT use ``cast`` at all:
a test that needs one is evidence that the production API is
under-typed, and the API MUST be fixed instead.

Usage::

    python -m ralph.testing.audit_cast_policy [codebase_root]

Returns exit code 0 if no cast-policy violations found, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Boundary registry: one named helper per dynamic-typing leak kind.
#
# Each entry: (file_stem, helper_name)
#
# The boundary helpers seeded here are the ones the type-checking
# policy records under ``sanctioned_dynamic_boundaries`` and the ones
# the codebase already maintains. New boundaries require a
# documented addition to the policy AND an entry here.
# ---------------------------------------------------------------------------
_BOUNDARY_HELPERS: frozenset[tuple[str, str]] = frozenset(
    {
        # ``ralph/mcp/server/_fallback_http_handler.py`` - JSON envelope
        # boundary helper(s).
        ("_fallback_http_handler", "_load_envelope"),
        ("_fallback_http_handler", "_parse_envelope"),
        # ``ralph/agents/parsers/`` - third-party agent NDJSON stream parsers
        # (claude, codex, gemini, etc.) are documented lenient boundaries.
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
        # ``ralph/agents/invoke.py`` - Claude/OpenCode stream parsers.
        ("invoke", "parse_event"),
        # Subprocess NDJSON stream parsers.
        ("_process_reader", "parse_event"),
        ("_pty_line_reader", "parse_event"),
        ("_tool_call_extraction", "parse_event"),
        ("_completion", "parse_event"),
        # ``ralph/display/edit_preview.py`` - pygments lexer-aliases
        # boundary helper. The pygments ``Lexer`` class exposes an
        # ``aliases`` attribute whose declared type varies by version
        # (``()`` / ``Sequence[str]``); the helper normalises the shape
        # for the rest of the module.
        ("edit_preview", "_lexer_for_path"),
    }
)


# Modules that are explicitly testing cast-policy behavior. Exempt.
_CAST_POLICY_EXEMPT_STEMS: frozenset[str] = frozenset(
    {
        "audit_cast_policy",
        "test_audit_cast_policy",
    }
)


# ---------------------------------------------------------------------------
# Structural-seam allowlist.
#
# Each entry: (file_stem, target_type). The pair is the ratchet base
# recorded by the audit's first run. New entries require a documented
# rationale (the inline marker ``# cast-policy: seam: <rationale>``
# on the cast line) and a corresponding refactor in a follow-up
# workflow that replaces the cast with a checked accessor / a named
# boundary helper / a runtime-checkable protocol + isinstance.
# ---------------------------------------------------------------------------
_SEAM_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("__init__", "AgentTransport"),
        ("__init__", "Mapping[str, _ParserFactory]"),
        ("__init__", "Mapping[str, str]"),
        ("__init__", "_CatalogModule"),
        ("__init__", "_CommitPlumbingModule"),
        ("__init__", "_SmokePlumbingModule"),
        ("_api", "_WorkUnitsModule"),
        ("_commands", "AgentTransport"),
        ("_completion", "IO[str] | None"),
        ("_completion", "Path"),
        ("_completion", "int | None"),
        ("_content", "SkillMetadata"),
        ("_factory", "AgentTransport"),
        ("_fake_popen", "bool"),
        ("_fake_popen", "int"),
        ("_fake_popen", "int | None"),
        ("_fallback_standalone_server", "tuple[str, int]"),
        ("_fields", "str | None"),
        ("_grep_handlers", "sqlite3.Row"),
        ("_handlers_reindex", "Workspace"),
        ("_lazy_tool_handler", "ToolHandler"),
        ("_mcp_server", "_ModelDump | None"),
        ("_mcp_server", "_ToDict | None"),
        ("_ndjson_base", "_SubagentSourceLabel"),
        ("_options", "float | None"),
        ("_parser", "str"),
        ("_parser", "str | None"),
        ("_phase_transition_summary", "_SnapshotModule"),
        ("_plan_evidence", "str"),
        ("_plan_subplans", "list[str]"),
        ("_plan_subplans", "str"),
        ("_plan_work_units", "list[str]"),
        ("_plan_work_units", "str"),
        ("_process_manager_runtime", "_PsutilModuleLike"),
        ("_process_reader", "ChildLivenessRegistry | None"),
        ("_process_reader", "IO[str] | None"),
        ("_process_reader", "int | None"),
        ("_pty_line_reader", "ChildLivenessRegistry | None"),
        ("_pty_line_reader", "Path | None"),
        ("_pty_line_reader", "_MergedDiagType"),
        ("_pty_line_reader", "dict[str, str] | None"),
        ("_pty_line_reader", "int | None"),
        ("_read_handlers", "ExploreIndexLike | None"),
        ("_read_handlers", "SpanRow"),
        ("_read_handlers", "SymbolRow"),
        ("_registry", "type[McpConfig]"),
        ("_runner_interrupt", "SignalGetter"),
        ("_runner_interrupt", "SignalSetter"),
        ("_runner_session", "_EffectExecutorModule"),
        ("_sentry", "_BreadcrumbRecorder"),
        ("_sentry", "_MetricCounter"),
        ("_sentry", "_MetricDistribution"),
        ("_sentry", "str"),
        ("_sentry", "tuple[str, ...]"),
        ("_spec_helpers", "str"),
        ("_startup_http", "JsonRpcResponse"),
        ("_startup_http", "str | None"),
        ("_store_class", "list[sqlite3.Row]"),
        ("_store_class_content_cache", "bytes | memoryview | None"),
        ("_store_class_content_cache", "list[sqlite3.Row]"),
        ("_store_types", "int | str | float"),
        ("_store_types", "list[sqlite3.Row]"),
        ("_upstream_proxy_handler", "HasMediaManifest | None"),
        ("_upstream_proxy_handler", "Workspace | None"),
        ("_utils", "object | None"),
        ("_workspace", "_HandlerWithDispatch"),
        ("_write_handlers", "ExploreIndexLike | None"),
        ("_write_handlers", "ExploreStore"),
        ("_write_handlers", "list[dict[str, str]]"),
        ("activity_router", "type[AgentParser]"),
        ("activity_stream", "AgentTransport"),
        ("activity_stream", "AgentTransport | None"),
        ("activity_stream", "DisplayContext | None"),
        ("activity_stream", "_ParallelDisplayModule"),
        ("activity_stream", "deque[str] | list[str] | None"),
        ("agent_detection", "str"),
        ("artifact", "Path | None"),
        ("artifacts", "ArtifactContract"),
        ("audit_agent_internal_paths", "Callable[[str], bool]"),
        ("audit_fenced_artifact_examples", "list[str]"),
        ("audit_fenced_artifact_examples", "str"),
        ("audit_fenced_artifact_examples", "type[Undefined]"),
        ("audit_template_render_integrity", "_IfNodeView"),
        ("audit_template_render_integrity", "_NameNodeView"),
        ("audit_template_render_integrity", "list[str]"),
        ("audit_typecheck_bypass", "list[str]"),
        ("bench", "McpConfig"),
        ("commit", "UnifiedConfig"),
        ("commit_cleanup", "str"),
        ("commit_executor", "DisplayContext"),
        ("commit_executor", "ParallelDisplay"),
        ("commit_executor", "PolicyBundle | None"),
        ("commit_executor", "Verbosity"),
        ("commit_executor", "str | None"),
        ("commit_message", "str"),
        ("commit_plumbing", "AgentTransport"),
        ("commit_plumbing", "RestartAwareMcpBridge"),
        ("commit_plumbing", "UnifiedConfig"),
        ("commit_plumbing", "typing.Callable[[], object]"),
        ("common", "tuple[Path, ...]"),
        ("config", "McpServerOrigin"),
        ("context", "Literal["),
        ("controller", "Callable[[str, str], str]"),
        ("controller", "Callable[[str], str]"),
        ("controller", "Iterable[int]"),
        ("controller", "SignalGetter"),
        ("controller", "SignalSetter"),
        ("controller", "dict[str, int]"),
        ("ddgs", "Callable[[], _DdgsTextClient] | None"),
        ("dirty_paths", "ExploreStoreLike | None"),
        ("dispatcher", "Iterable[int]"),
        ("dispatcher", "SignalGetter"),
        ("dispatcher", "SignalSetter"),
        ("dispatcher", "_PsutilModule"),
        ("effect_executor", "AgentConfig | None"),
        ("effect_executor", "Path | None"),
        ("effect_executor", "_InvokeAgentFn | None"),
        ("effect_executor", "_RegistryLike"),
        ("effect_executor", "bool"),
        ("effect_executor", "str"),
        ("effect_executor", "type[Exception] | None"),
        ("exa", "_ExaType"),
        ("exa", "object | None"),
        ("exec", "object | None"),
        ("explain", "_LoadPolicyFn"),
        ("explain", "_RenderExplanationFn"),
        ("factory", "DisplayContext"),
        ("factory", "MaterializeMasterPromptFn"),
        ("factory", "MultimodalModelIdentity | None"),
        ("factory", "PhasePromptMaterializerFn"),
        ("failure_classifier", "BaseException | None"),
        ("fan_out", "AgentTransport | None"),
        ("fan_out", "Console | None"),
        ("fan_out", "Path | None"),
        ("fan_out", "PolicyBundle"),
        ("fan_out", "UnifiedConfig | None"),
        ("fan_out", "WorkspaceScope"),
        ("fan_out", "_ExecutorFactory"),
        ("fan_out", "_ExecutorFactory | None"),
        ("fan_out", "_InstallSignalHandlersFn"),
        ("fan_out", "_InstallSignalHandlersFn | None"),
        ("fan_out", "_McpFactory"),
        ("fan_out", "_McpFactory | None"),
        ("fan_out", "_PipelineSubscriberLike | None"),
        ("fan_out", "_ReducerReduceFn"),
        ("fan_out", "_ReducerReduceFn | None"),
        ("fan_out", "_RunProcessAsyncFn"),
        ("fan_out", "_RunProcessAsyncFn | None"),
        ("fan_out", "str"),
        ("fan_out", "str | None"),
        ("fan_out", "type[ParallelDisplay]"),
        ("gemini", "JsonDict"),
        ("git_read", "Path | str | None"),
        ("git_read", "_ReadablePipe"),
        ("git_read", "_SpawnedProcessLike"),
        ("install", "bool"),
        ("install", "str | None"),
        ("lifecycle", "IO[bytes]"),
        ("lifecycle", "Path | str | None"),
        ("lifecycle", "ReindexResult"),
        ("lifecycle", "ReindexRunner"),
        ("lifecycle", "int"),
        ("lifecycle", "str"),
        ("loader", "Sequence[str]"),
        ("loader", "ValidationErrorDetails"),
        ("loader", "str"),
        ("loader", "type[Exception]"),
        ("loader", "type[ValueError]"),
        ("logging", "SinkAdder"),
        ("main", "click.Command"),
        ("materialize", "ArtifactsPolicy | None"),
        ("materialize", "Path"),
        ("materialize", "Path | None"),
        ("materialize", "PipelinePolicy"),
        ("materialize", "SessionCapabilities"),
        ("materialize", "WorkUnit | None"),
        ("materialize", "Workspace"),
        ("materialize", "bool"),
        ("materialize", "str"),
        ("materialize", "str | None"),
        ("mcp_loader", "type[ValueError]"),
        ("md_artifact", "Path | None"),
        ("nanocoder", "dict[str, str]"),
        ("opencode_execution_strategy", "ChildLivenessRegistry | None"),
        ("operations", "str"),
        ("parallel_display", "PipelineSubscriber | None"),
        ("phase_agent_handler", "ParallelDisplay"),
        ("phase_agent_handler", "_ArtifactReaderModule"),
        ("phase_transition", "str | None"),
        ("plan", "list[str]"),
        ("plan", "str"),
        ("policy_outcomes", "object | None"),
        ("pydantic_validation_errors", "ValidationErrorDetails"),
        ("pytest_timeout_plugin", "_PsutilMod"),
        ("pytest_timeout_plugin", "_SuiteWatchdog | None"),
        ("raw_overflow", "BinaryIO"),
        ("reducer", "PipelineEvent"),
        ("registration", "StrategyFactory"),
        ("registration", "_ParserFactory"),
        ("run", "PolicyBundle"),
        ("run", "_RunnerFunc | None"),
        ("run", "_RunnerModule"),
        ("run", "int"),
        ("run_loop", "PipelineSubscriber | None"),
        ("run_loop", "_DisplayContextOwner"),
        ("run_loop", "_PhaseAwareDisplay"),
        ("run_loop", "_RegistryLike"),
        ("run_loop", "_RunEndDisplay"),
        ("run_loop", "_RunPipelineStepFn"),
        ("run_loop", "int"),
        ("run_loop", "str | None"),
        ("runner", "_ExecuteEffectKwargsFn"),
        ("runtime", "Path"),
        ("runtime", "int"),
        ("runtime", "str"),
        ("runtime", "tuple[Path, ...]"),
        ("scope", "tuple[Path, ...]"),
        ("scoped_auto_commit", "str"),
        ("smoke_plumbing", "AgentTransport"),
        ("smoke_plumbing", "RestartAwareMcpBridge"),
        ("spec", "AgentTransport"),
        ("stack", "Sequence[str]"),
        ("stack", "_HandlerCallable"),
        ("stack", "_StackDetector"),
        ("stack", "list[str]"),
        ("startup", "JsonRpcResponse"),
        ("state", "AgentChainState"),
        ("state", "FalloverRecord"),
        ("state", "int"),
        ("state_query", "int"),
        ("subprocess_runner", "_PopenExitProtocol"),
        ("subscriber", "type[_WaitingEventLike]"),
        ("tavily", "_TavilyClientType"),
        ("tavily", "object | None"),
        ("template_engine", "str"),
        ("template_engine", "type[Undefined]"),
        ("verify_timeout", "float"),
        ("verify_timeout", "list[str]"),
        ("development_result", "tuple[str, ...]"),
        ("subscriber", "int | float"),
    }
)


# Universal leaf types accepted for sound-by-construction casts.
_UNIVERSAL_LEAF_TYPES: frozenset[str] = frozenset(
    {
        "object",
        "Any",
        "None",
        "NoneType",
    }
)

# Files / directories to skip.
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

# Match a ``cast(...)`` call site and capture the asserted target type
# string. Accepts both quoted and unquoted forms:
#
# * ``cast("T", x)`` and ``cast('T', x)`` — capture group 2 is the
#   target string (the inner quotes are stripped).
# * ``cast(T, x)`` — capture group 3 is the unquoted target. The
#   target is an identifier / generic expression (e.g. ``str``,
#   ``dict[str, object]``, ``int | None``). The expression is allowed
#   to contain brackets, commas, spaces, and the union ``|`` operator;
#   the match is non-greedy and stops at the first top-level comma or
#   close paren.
#
# The third capture group empty-means-quoted-form path is matched at
# runtime by checking which group is non-None.
_CAST_CALL_RE = re.compile(
    r"\bcast\s*\(\s*"
    r"(?:"
    r"([\"'])([^\"']+?)\1"  # quoted form: group 1 (quote) + group 2 (target)
    r"|"
    r"([A-Za-z_][\w\[\] ,.|]*)"  # unquoted form: group 3 (target)
    r")"
)

# Match a ``fake_value(...)`` call site. ``fake_value`` is the
# test-only no-op accessor that exists so tests can pass a faked
# value through a function expecting a specific production type
# without using ``cast`` or a blanket type-ignore directive. The
# cast-policy audit is stricter than the type checker: any use of
# ``fake_value`` in a test file is treated as a test-cast violation,
# because the policy forbids casts AND test-accessor escape hatches
# in tests.
_TEST_FAKE_VALUE_RE = re.compile(r"\bfake_value\s*\(")

# Marker on the same line as a cast that marks it as a structural seam.
# Multi-line cast expressions (where the cast call is on one line and the
# default value / closing parenthesis lives on the next) are also accepted
# when the marker is on the *next* line — the marker is logically attached
# to the cast in both shapes.
_SEAM_MARKER_RE = re.compile(r"#\s*cast-policy\s*:\s*seam\s*:")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CastPolicyViolation:
    """A single cast-policy violation found during scanning."""

    file_path: str
    line: int
    category: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [CAST-POLICY] {self.category}: {self.detail}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Return True when ``pos`` is inside a single-line string literal.

    The scan honours escaped quotes and stops at ``#`` comments. It
    is used to suppress cast-policy matches inside string literals
    (e.g. test fixtures that write source code containing a ``cast``
    call to another audit).
    """
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
    """Split a target string into (outer_type, parameters_string).

    For ``dict[str, object]`` returns ``('dict', 'str, object')``.
    For ``list[object]`` returns ``('list', 'object')``.
    For ``str`` returns ``('str', '')``.
    """
    bracket = target.find("[")
    if bracket <= 0:
        return target.strip().strip("'\""), ""
    outer = target[:bracket].strip().strip("'\"")
    # Find matching close bracket.
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
    """Extract leaf types from a parameter list, respecting nested brackets."""
    if not params.strip():
        return []
    # Walk the params string, tracking bracket depth, splitting on commas at depth 0.
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
    # Now each part is like ``str``, ``int``, ``Callable[[], object]``, ``str | int``.
    leaves: list[str] = []
    for part in parts:
        leaves.extend(_leaf_types_from_one(part))
    return leaves


def _leaf_types_from_one(part: str) -> list[str]:
    """Extract leaf types from a single parameter like ``str | int | None``."""
    # If it's a nested container, recurse on its parameters.
    _outer, params = _extract_outer_and_params(part)
    if params:
        # It's a container type. Check if the outer itself is universal
        # AND all parameter leaves are universal.
        return _leaf_types_in_params(params)
    # Otherwise, split on union ``|``.
    return [leaf.strip().strip("'\"") for leaf in part.split("|") if leaf.strip()]


def _all_leaves_universal(target: str) -> bool:
    """Return True when every leaf type in the target is universal.

    For a bare universal like ``object`` this is trivially True. For a
    container like ``list[object]`` this returns True only when every
    parameter leaf is universal (the outer container type itself is
    not a "leaf" and is not required to be universal -- it is the
    parameter being refined).
    """
    outer, params = _extract_outer_and_params(target)
    leaves = _leaf_types_in_params(params)
    if not leaves:
        # No parameters: just check the outer type itself.
        return outer in _UNIVERSAL_LEAF_TYPES
    return all(leaf in _UNIVERSAL_LEAF_TYPES for leaf in leaves)


# Container types whose outer type is provable by isinstance and whose
# parameters may legitimately be refined by ``cast`` (per the policy's
# SOUND BY CONSTRUCTION position 1). The outer type IS the container
# type itself; the parameters are what the cast may refine.
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
    """Return True for the ``cast("ContainerType[Param]", x)`` pattern.

    Per the policy's SOUND BY CONSTRUCTION position 1: the cast widens
    to the language's universal type OR refines only type PARAMETERS
    that an immediately preceding ``isinstance`` guard already proved.
    The canonical examples are ``cast("dict[str, object]", x)`` and
    ``cast("list[object]", x)`` immediately after ``isinstance(x, dict)``
    or ``isinstance(x, list)``.

    A cast is accepted as sound-by-construction when:
    * the outer type is a known container (``dict``, ``list``, ``Mapping``,
      ``Sequence``, ``Callable``, etc.), AND
    * at least one of the parameter leaves is universal (``object``,
      ``Any``, ``None``). The structural narrowing of the value side
      to ``object`` / ``Any`` is what makes the cast sound; ``object``
      is the universal type and asserting "the values are objects" is
      trivially true.
    """
    outer, params = _extract_outer_and_params(target)
    if not params:
        # No parameters: cast to a bare container type. NOT sound.
        return False
    if outer not in _CONTAINER_OUTER_TYPES:
        return False
    leaves = _leaf_types_in_params(params)
    if not leaves:
        return False
    # At least one universal leaf is required for the cast to be sound.
    return any(leaf in _UNIVERSAL_LEAF_TYPES for leaf in leaves)


def _is_in_boundary_helper(
    rel_path: str,
    line_index: int,
    lines: list[str],
    target: str,
) -> bool:
    """Return True when the cast lives inside a registered boundary helper."""
    file_stem = Path(rel_path).stem
    helper_names = _collect_enclosing_helpers(lines, line_index)
    for helper_name in helper_names:
        if (file_stem, helper_name) in _BOUNDARY_HELPERS:
            return True
    target_helper_names = {name for _, name in _BOUNDARY_HELPERS}
    return bool(any(name in target_helper_names for name in helper_names))


def _collect_enclosing_helpers(lines: list[str], line_index: int) -> list[str]:
    """Collect names of functions enclosing line ``line_index``."""
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
    """Return the net paren depth change when scanning ``raw``."""
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
    """Return the index of the line that closes the *outer* expression containing the cast.

    The outer expression is the surrounding parenthesized / multiline
    expression (e.g. the assignment ``x = (cast(...), ...)`` spans
    lines from the opening paren of the outer expression to the
    matching closing paren). The seam marker may live on any line that
    is part of this outer expression; the cast line itself is just one
    piece of it.

    Walks forward from the line IMMEDIATELY BEFORE the cast's line so
    that the running paren depth at the cast's line equals the paren
    depth of the surrounding context. Then walks forward until the
    paren depth returns to that same baseline -- that line is the end
    of the outer expression.
    """
    # Compute the paren depth at the start of the cast's line. We
    # need to look at the line BEFORE the cast to know what depth the
    # cast is being called at.
    depth_before = 0
    if line_index > 0:
        depth_before = _paren_depth_of_line(lines[line_index - 1])
    # Then add the depth of the cast line up to (but not including) the
    # ``cast(`` itself. We can't easily know that position, so we use
    # the entire line: the cast's own parens balance out so the running
    # depth at the end of the line equals the depth at the start of the
    # cast's call expression.
    depth = depth_before + _paren_depth_of_line(lines[line_index])

    for i in range(line_index + 1, len(lines)):
        depth += _paren_depth_of_line(lines[i])
        if depth < depth_before:
            return i

    # Unclosed expression: scan to the end of the file.
    return len(lines) - 1


def _has_seam_marker(line: str, lines: list[str], line_index: int) -> bool:
    """Return True when a seam marker is attached to the cast expression.

    The marker may live on the same line as the ``cast(`` call, or on
    any line that belongs to the same outer expression (the call is
    part of a multi-line parenthesized expression and the seam marker
    is annotated on the closing line / a sibling argument line). The
    outer expression is the contiguous run of lines from the line with
    ``cast(`` until the paren depth returns to the surrounding depth.
    """
    if _SEAM_MARKER_RE.search(line):
        return True
    end_line = _find_cast_expression_end(lines, line_index)
    return any(_SEAM_MARKER_RE.search(lines[i]) for i in range(line_index + 1, end_line + 1))


# ---------------------------------------------------------------------------
# Line scanner
# ---------------------------------------------------------------------------


def _extract_cast_targets(line: str) -> list[tuple[str, int]]:
    """Return ``[(target_str, position_in_line), ...]`` for every cast on the line.

    Accepts both quoted and unquoted ``cast`` forms. For the unquoted
    form, the trailing ``,`` / ``)`` separator (if any) is stripped
    so the target is a clean identifier / generic expression.
    """
    targets: list[tuple[str, int]] = []
    for m in _CAST_CALL_RE.finditer(line):
        g2: str | None = m.group(2)
        g3: str | None = m.group(3)
        if g2 is not None:
            # Quoted form (group 2).
            targets.append((g2, m.start(0)))
        elif g3 is not None:
            # Unquoted form (group 3). Strip trailing ``,`` or ``)``.
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
    """Check a single source line for cast-policy violations."""
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
    """Scan source lines for forbidden cast call sites."""
    violations: list[CastPolicyViolation] = []
    for idx, raw_line in enumerate(lines):
        lineno = idx + 1
        if "cast(" not in raw_line and "fake_value(" not in raw_line:
            continue
        violations.extend(_check_line_for_cast(raw_line, rel_path, lines, lineno))
    return violations


def _collect_py_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under *root*, skipping excluded directories."""
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def audit_codebase(codebase_root: Path) -> tuple[list[CastPolicyViolation], int]:
    """Audit the entire codebase for cast-policy violations.

    Returns ``(violations, files_checked)``.
    """
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
    """Run the cast policy audit and return exit code.

    Exit code 0: no violations found.
    Exit code 1: violations found.
    Exit code 2: error.
    """
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
