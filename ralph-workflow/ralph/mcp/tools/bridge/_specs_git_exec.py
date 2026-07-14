"""Tool specs for git and exec operations."""

from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    EXEC_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    RAW_EXEC_TOOL,
    UNSAFE_EXEC_TOOL,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS

# Short pointer kept in the top-level description (which has a ~500-char budget);
# the full two-meaning guidance lives in the timeout_ms property below.
_TIMEOUT_SEMANTICS = (
    "On timeout you get an is_error result (not a retryable error) — see timeout_ms "
    "before retrying."
)

# A timeout is ambiguous, so the property hint teaches both readings: a
# legitimately long command should raise ``timeout_ms``, but an unexpectedly slow
# one may be genuinely stuck (infinite loop, deadlock, or blocked on input), in
# which case raising the limit only wastes another full budget — fix the command
# instead. Default is interpolated from the one source of truth so the advertised
# hint can never drift from real behavior.
_TIMEOUT_MS_DESCRIPTION = (
    f"Timeout in milliseconds (default: {EXEC_DEFAULT_TIMEOUT_MS}, above the verify "
    "budget; example values: 10000, 60000, 120000). On a timeout the process tree is "
    "killed and the call returns an is_error result, NOT a retryable protocol error — "
    "decide WHY before retrying: if the command is genuinely long-running, raise "
    "timeout_ms; if it is unexpectedly slow it may be stuck in an infinite loop, "
    "deadlocked, or blocked waiting on input, in which case fix the command rather "
    "than just raising the limit."
)


def _timeout_ms_property() -> dict[str, object]:
    """Schema for the ``timeout_ms`` param, with the default derived from the one
    source of truth so the advertised hint can never drift from real behavior."""
    return {
        "type": "number",
        "description": _TIMEOUT_MS_DESCRIPTION,
        "default": EXEC_DEFAULT_TIMEOUT_MS,
    }


def git_exec_specs() -> list[ToolSpec]:
    """Return tool specs for git and exec operations."""
    return [
        ToolSpec(
            metadata=_metadata(
                name=GIT_STATUS_TOOL,
                description=(
                    "Get git status showing modified, staged, and untracked files. "
                    "Optional params: format ('raw'|'compact', default 'raw'). "
                    "Returns the raw `git status` text output by default. "
                    "``format='compact'`` returns a JSON card with ranked "
                    "changed paths, staged/unstaged/untracked counts, and the "
                    "underlying porcelain lines for replay. "
                    'Example: {"format": "compact"} returns ranked cards.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["raw", "compact"],
                            "description": (
                                "Output shape. ``raw`` is the legacy text; "
                                "``compact`` is a ranked JSON summary card."
                            ),
                            "default": "raw",
                        },
                    },
                },
                required_capability=McpCapability.GIT_STATUS_READ.value,
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_DIFF_TOOL,
                description=(
                    "Get git diff showing line-by-line differences in modified files. "
                    "Optional params: args (array of extra git diff arguments as "
                    "strings — read-only subset only), "
                    "format ('raw'|'summary', default 'raw'), max_bytes (cap on the "
                    "diff excerpt, default 50000). "
                    "``format='summary'`` returns a compact JSON card with "
                    "files_changed, insertion/deletion totals, per-file "
                    "added/removed counts, and a diff excerpt capped at max_bytes. "
                    "AC-06: read-only contract. ``args`` MUST be drawn from the "
                    "read-only subset of git diff flags (for example "
                    "``--stat``, ``--name-only``, ``--numstat``, ``--shortstat``, "
                    "``--staged``, ``--unified=N``, ``--diff-filter=...``). "
                    "Output-writing flags (``--output=path``, ``--output path``, "
                    "``-o path``) and external-helper flags (``--ext-diff``, "
                    "``--textconv``, ``--convience-diff``) are rejected at parse "
                    "time so git cannot write to the workspace or invoke an "
                    "external helper. "
                    'Example: {"args": [], "format": "summary", "max_bytes": 5000}.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Array of extra git diff arguments as strings "
                                "(example values: [], ['--stat'], "
                                "['--name-only']). Read-only subset only: "
                                "``--stat``, ``--name-only``, ``--numstat``, "
                                "``--shortstat``, ``--staged``, ``--unified=N``, "
                                "``--diff-filter=...`` are accepted. "
                                "Output-writing flags (``--output=...``, "
                                "``-o ...``) and external-helper flags "
                                "(``--ext-diff``, ``--textconv``, "
                                "``--convience-diff``) are rejected at parse "
                                "time so git cannot write to the workspace "
                                "or invoke an external helper."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": ["raw", "summary"],
                            "description": (
                                "Output shape. ``raw`` is the legacy text; "
                                "``summary`` is a compact JSON card with a "
                                "capped diff excerpt."
                            ),
                            "default": "raw",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": (
                                "Cap on the diff excerpt returned in "
                                "``format='summary'``. Default 50000. "
                                "Strictly bounded to a positive integer "
                                "in [1, 50000] to preserve the bounded-"
                                "excerpt contract."
                            ),
                            "default": 50000,
                            "minimum": 1,
                            "maximum": 50000,
                        },
                    },
                },
                # Matches handle_git_diff's gate (GIT_DIFF_READ_CAPABILITY). Resolves
                # to the git.diff_read capability (every drain holds it via the base
                # set) through the McpCapability.GIT_DIFF_READ mapping.
                required_capability=McpCapability.GIT_DIFF_READ.value,
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_diff",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_LOG_TOOL,
                description=(
                    "Get git commit log (one line per commit, `--oneline` format). "
                    "Optional params: count (number, default 10), "
                    "format ('raw'|'summary', default 'raw'). "
                    "Returns the raw `git log` text by default. "
                    "``format='summary'`` returns a compact JSON envelope "
                    "with one commit per entry (short_sha, sha, subject) "
                    "and ``bytes_in``/``bytes_out`` size counters so agents "
                    "do not pay for unrelated downstream parsing. "
                    'Example: {"count": 5} returns the 5 most recent commits; '
                    '{"count": 5, "format": "summary"} returns the same '
                    "commits in a compact envelope."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "number",
                            "description": (
                                "Number of recent commits to return as an integer "
                                "(default: 10, example values: 5, 20, 100)."
                            ),
                            "default": 10,
                        },
                        "format": {
                            "type": "string",
                            "enum": ["raw", "summary"],
                            "description": (
                                "Output shape. ``raw`` is the legacy one-line-"
                                "per-commit text; ``summary`` is a compact JSON "
                                "envelope with one entry per commit and "
                                "``bytes_in``/``bytes_out`` size counters."
                            ),
                            "default": "raw",
                        },
                    },
                },
                required_capability=McpCapability.GIT_STATUS_READ.value,
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_log",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_SHOW_TOOL,
                description=(
                    "Show a git object (commit, tag, tree, blob) with full details. "
                    "Required param: ref (string, git object reference). "
                    "Optional param: format ('raw'|'summary', default 'raw'). "
                    "Returns the object contents by default. "
                    "``format='summary'`` returns a compact header-only JSON "
                    "envelope with sha, author, subject, parents and a "
                    "``bytes_in``/``bytes_out`` size counters; the patch "
                    "body is omitted. "
                    'Example: {"ref": "HEAD~1"} shows the parent commit; '
                    '{"ref": "v1.0.0", "format": "summary"} shows the tag '
                    "in the compact envelope."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": (
                                "Git object reference as a string such as a commit SHA, branch "
                                "name, or tag "
                                "(example values: 'HEAD~1', 'main', 'v1.0.0', 'abc123def')."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": ["raw", "summary"],
                            "description": (
                                "Output shape. ``raw`` is the legacy full "
                                "show output (header + diff body); "
                                "``summary`` is a compact header-only JSON "
                                "envelope without the patch body."
                            ),
                            "default": "raw",
                        },
                    },
                    "required": ["ref"],
                },
                required_capability=McpCapability.GIT_STATUS_READ.value,
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_show",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EXEC_TOOL,
                description=(
                    "Execute a bounded command in the workspace. Accepts command or "
                    "argv, plus optional args, timeout_ms, and format ('raw' or "
                    "'summary'). Shell operators (|, &&, ;, >, <) in a command STRING "
                    "run through a shell, but the blacklist (sudo, rm -rf /, external "
                    "curl, git/hg/svn — use git_* tools for reads) is "
                    "enforced on every pipeline command. format='summary' "
                    "returns a JSON envelope with a replayable stdout resource id. "
                    "On timeout you get an is_error result, not retryable — decide "
                    "WHY first."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": (
                                "Primary command input. This may be a bare executable "
                                "name, a shell-style command line (pipes/redirections/"
                                "&&/; work when passed as a string), or an argv-style "
                                "string array — array items are always literal argv and "
                                "are never shell-interpreted (example values: 'ls', "
                                "'grep -r foo . | wc -l', 'python -m pytest "
                                "tests/test_tool_exec.py', ['python', '-m', 'pytest'])."
                            ),
                        },
                        "argv": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": (
                                "Fallback alias for callers that prefer argv-style input. Used "
                                "when 'command' is omitted. Accepts the same forms as 'command'."
                            ),
                        },
                        "args": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ],
                            "description": (
                                "Optional command arguments. Pass either an array of strings "
                                "or a shell-style string "
                                "(example values: ['-la'], '--help', ['.', '--max-depth', '2'])."
                            ),
                        },
                        "timeout_ms": _timeout_ms_property(),
                        "format": {
                            "type": "string",
                            "enum": ["raw", "summary"],
                            "description": (
                                "Output shape. ``raw`` is the legacy text/head-tail "
                                "shape; ``summary`` returns a JSON envelope with a "
                                "replayable ``stdout_resource_id`` handle and the "
                                "spill path when output is oversized."
                            ),
                            "default": "raw",
                        },
                    },
                    # The handler accepts command OR argv (see description); reflect
                    # that here instead of falsely requiring only command.
                    "anyOf": [
                        {"required": ["command"]},
                        {"required": ["argv"]},
                    ],
                },
                required_capability=McpCapability.PROCESS_EXEC_BOUNDED.value,
            ),
            module_name="ralph.mcp.tools.exec",
            handler_name="handle_exec_command",
        ),
        ToolSpec(
            metadata=_metadata(
                name=UNSAFE_EXEC_TOOL,
                description=(
                    "DANGEROUS: Execute an unrestricted shell command in the real repository "
                    "directory. All shell operators (|, &&, ||, ;, &, >, >>, <, <<) work. "
                    "Only version control commands (git, hg, svn) are blocked. "
                    "Required param: command (string, the full shell command). "
                    "Optional param: timeout_ms. "
                    "Returns stdout, stderr, and exit_code. "
                    'Example: {"command": "make build && npm test"}. ' + _TIMEOUT_SEMANTICS
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "Full shell command string. Shell operators (|, &&, ||, ;) work as "
                                "normal. Version control commands (git, hg, svn) are blocked. "
                                '(example values: "make build", "npm test && npm lint").'
                            ),
                        },
                        "timeout_ms": _timeout_ms_property(),
                    },
                    "required": ["command"],
                },
                required_capability=McpCapability.PROCESS_EXEC_UNBOUNDED.value,
            ),
            module_name="ralph.mcp.tools.unsafe_exec",
            handler_name="handle_unsafe_exec",
        ),
        ToolSpec(
            metadata=_metadata(
                name=RAW_EXEC_TOOL,
                description=(
                    "DANGEROUS: Alias for unsafe_exec. Execute an unrestricted shell command "
                    "in the real repository directory. All shell operators (|, &&, ||, ;, "
                    "&, >, >>, <, <<) work. Only version control commands (git, hg, svn) "
                    "are blocked. Required param: command (string, the full shell command). "
                    "Optional param: timeout_ms. "
                    "Returns stdout, stderr, and exit_code. "
                    'Example: {"command": "make build && npm test"}. ' + _TIMEOUT_SEMANTICS
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "Full shell command string. Shell operators (|, &&, ||, ;) work as "
                                "normal. Version control commands (git, hg, svn) are blocked. "
                                '(example values: "make build", "npm test && npm lint").'
                            ),
                        },
                        "timeout_ms": _timeout_ms_property(),
                    },
                    "required": ["command"],
                },
                required_capability=McpCapability.PROCESS_EXEC_UNBOUNDED.value,
            ),
            module_name="ralph.mcp.tools.unsafe_exec",
            handler_name="handle_unsafe_exec",
        ),
    ]
