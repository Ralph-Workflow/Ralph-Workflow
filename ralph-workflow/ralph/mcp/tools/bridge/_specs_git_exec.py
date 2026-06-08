"""Tool specs for git and exec operations."""

from __future__ import annotations

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

# A timeout is ambiguous, so the hint must teach both readings up front: a
# legitimately long command should raise ``timeout_ms``, but an unexpectedly slow
# one may be genuinely stuck (infinite loop, deadlock, or blocked on input), in
# which case raising the limit only wastes another full budget — fix the command
# instead. Shared by the exec family so the guidance cannot drift between them.
_TIMEOUT_SEMANTICS = (
    f"Default timeout_ms is {EXEC_DEFAULT_TIMEOUT_MS} (above the verify budget). "
    "On a timeout the process tree is killed and you get an is_error result, not a "
    "retryable protocol error: decide WHY it timed out before retrying. If the "
    "command is genuinely long-running, raise timeout_ms; if it is unexpectedly "
    "slow it may be stuck in an infinite loop, deadlocked, or blocked waiting on "
    "input — fix the command rather than just raising the limit."
)


def _timeout_ms_property() -> dict[str, object]:
    """Schema for the ``timeout_ms`` param, with the default derived from the one
    source of truth so the advertised hint can never drift from real behavior."""
    return {
        "type": "number",
        "description": (
            f"Timeout in milliseconds as a number (default: {EXEC_DEFAULT_TIMEOUT_MS}, "
            "example values: 10000, 60000, 120000)."
        ),
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
                    "No parameters required. Returns a status object with lists of modified, "
                    "staged, and untracked files. "
                    "Example: {} returns git status output."
                ),
                input_schema={"type": "object", "properties": {}},
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_status",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_DIFF_TOOL,
                description=(
                    "Get git diff showing line-by-line differences in modified files. "
                    "Optional param: args (array of extra git diff arguments as strings). "
                    "Returns diff output with line changes. "
                    'Example: {"args": []} shows full diff; '
                    '{"args": ["--stat"]} shows summary only.'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Array of extra git diff arguments as strings "
                                "(example values: [], ['--stat'], ['--name-only'])."
                            ),
                        },
                    },
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_diff",
        ),
        ToolSpec(
            metadata=_metadata(
                name=GIT_LOG_TOOL,
                description=(
                    "Get git commit log with hash, author, date, and message. "
                    "Optional param: count (number, default 10). "
                    "Returns an array of commit objects. "
                    'Example: {"count": 5} returns the 5 most recent commits.'
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
                    },
                },
                required_capability="GitStatusRead",
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
                    "Returns the object contents. "
                    'Example: {"ref": "HEAD~1"} shows the parent commit; '
                    '{"ref": "v1.0.0"} shows the tag details.'
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
                    },
                    "required": ["ref"],
                },
                required_capability="GitStatusRead",
            ),
            module_name="ralph.mcp.tools.git_read",
            handler_name="handle_git_show",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EXEC_TOOL,
                description=(
                    "Execute a bounded subprocess in the workspace. Accepts command or "
                    "argv as a string or string array, plus optional args and timeout_ms. "
                    "Shell-style strings are tokenized; the command blacklist is the "
                    "security boundary. Returns stdout, stderr, "
                    'and exit_code. Example: {"command": "python -m pytest", '
                    '"args": ["-q"], "timeout_ms": 60000}. '
                    "Some commands may still be blacklisted; prefer structured tools "
                    "when available. " + _TIMEOUT_SEMANTICS
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
                                "name, a shell-style command line without shell control "
                                "operators, or an argv-style string array (example values: "
                                "'ls', 'python --version', 'python -m pytest "
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
                    },
                    "required": ["command"],
                },
                required_capability="ProcessExecBounded",
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
                required_capability="ProcessExecUnbounded",
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
                required_capability="ProcessExecUnbounded",
            ),
            module_name="ralph.mcp.tools.unsafe_exec",
            handler_name="handle_unsafe_exec",
        ),
    ]
