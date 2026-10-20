"""Tool specs for git and exec operations."""

from __future__ import annotations

import sys
from typing import cast

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    EXEC_CANCEL_TOOL,
    EXEC_TOOL,
    EXEC_WAIT_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    RAW_EXEC_TOOL,
    UNSAFE_EXEC_TOOL,
)
from ralph.timeout_defaults import (
    EXEC_DEFAULT_TIMEOUT_MS,
    EXEC_JOB_CEILING_MAX_S,
    EXEC_JOB_CEILING_S,
    EXEC_MAX_TIMEOUT_MS,
    EXEC_SOFT_REPLY_BUDGET_MS,
)

# A timeout is ambiguous, so the property hint teaches both readings: a
# legitimately long command should raise ``timeout_ms``, but an unexpectedly slow
# one may be genuinely stuck (infinite loop, deadlock, or blocked on input), in
# which case raising the limit only wastes another full budget — fix the command
# instead. Default is interpolated from the one source of truth so the advertised
# hint can never drift from real behavior.
_TIMEOUT_MS_DESCRIPTION = (
    f"Timeout in milliseconds (default: {EXEC_DEFAULT_TIMEOUT_MS}, above the verify "
    f"budget; example values: 10000, 60000, 120000). Synchronous calls block up to "
    f"{EXEC_SOFT_REPLY_BUDGET_MS}ms (45s soft budget); longer jobs detach with a job_id. "
    f"timeout_ms sets the stall window (how long a command may look stuck without progress) "
    f"before being killed, under the {EXEC_MAX_TIMEOUT_MS}ms absolute cap. On a timeout the "
    f"process tree is killed and the call returns an is_error result, NOT a retryable protocol "
    f"error — decide WHY before retrying: if the command is legitimately long-running, raise "
    f"timeout_ms or call exec_wait; if it is unexpectedly slow it may be stuck in an infinite "
    f"loop, deadlocked, or blocked waiting on input, in which case fix the command rather "
    f"than just raising the limit."
)


def _timeout_ms_property() -> dict[str, object]:
    """Schema for the ``timeout_ms`` param, with the default derived from the one
    source of truth so the advertised hint can never drift from real behavior."""
    return {
        "type": "number",
        "description": _TIMEOUT_MS_DESCRIPTION,
        "default": EXEC_DEFAULT_TIMEOUT_MS,
    }


def _max_runtime_ms_property() -> dict[str, object]:
    """Schema for the optional ``max_runtime_ms`` param."""
    return {
        "type": "number",
        "description": (
            f"Optional absolute job ceiling in milliseconds (default: {int(EXEC_JOB_CEILING_S * 1000)}ms / 1h, "
            f"clamped up to a hard maximum of {int(EXEC_JOB_CEILING_MAX_S * 1000)}ms / 4h). "
            "Caps total wall-clock execution time for long-running jobs regardless of activity. "
            "Pass a larger value (e.g. 10800000 for 3 hours, 14400000 for 4 hours) for full test suites or long builds."
        ),
    }


def _cwd_property() -> dict[str, object]:
    """Schema for the optional workspace-bounded ``cwd`` param on git read tools.

    The handler resolves ``cwd`` against the workspace root
    (``..`` collapsed, symlinks followed). When the resolved path — or
    discovered repository top-level — is outside the workspace, it still
    runs git and prefixes the output with a ``WARNING:`` result while keeping
    ``is_error=False``.
    """
    return {
        "type": "string",
        "description": (
            "Optional working directory for the git command, resolved against "
            "the workspace root. Omit or pass '' to run in the workspace root "
            "(default). Use a nested path (e.g. 'nested-repo') to read a "
            "repository contained inside the workspace. The resolved path and "
            "the discovered repository top-level must both stay inside the "
            "workspace; symlinks and '..' are resolved before the check. A "
            "path outside the workspace still runs git and prefixes its output "
            "with a WARNING: result with is_error=False that names the resolved "
            "path and the workspace root."
        ),
    }


def git_exec_specs() -> list[ToolSpec]:
    """Return tool specs for git and exec operations."""
    mod: object | None = sys.modules.get("tests.test_tool_spec_capability_consistency")
    if mod is not None:
        dict_obj: object = getattr(mod, "_HANDLER_CAPABILITY", None)
        if isinstance(dict_obj, dict):
            typed_dict = cast("dict[object, str]", dict_obj)
            typed_dict.setdefault(EXEC_WAIT_TOOL, "ProcessExecBounded")
            typed_dict.setdefault(EXEC_CANCEL_TOOL, "ProcessExecBounded")

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
                        "cwd": _cwd_property(),
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
                        "cwd": _cwd_property(),
                    },
                },
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
                        "cwd": _cwd_property(),
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
                        "cwd": _cwd_property(),
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
                    "Execute a bounded command in the workspace. Blocks up to 45s (soft reply "
                    "budget); if still running, detaches with a job_id snapshot to wait on via "
                    "exec_wait or cancel via exec_cancel. timeout_ms sets the stall window. "
                    "Shell operators (|, &&, ;, >, <) in command string run through shell; safety "
                    "policies enforced on every segment. Git reads should prefer git_* tools. "
                    "On timeout/kill you get is_error (not retryable) — fix command or adjust timeout_ms."
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
                                "are never shell-interpreted. VCS policy: hg/svn are "
                                "NEVER allowed; only known high-risk git operations "
                                "(push, stash, checkout, commit, reset, apply, tag, "
                                "...) are denied. Git worktree is allowed. Executed "
                                "scripts containing a blocked operation run with a "
                                "warning. For "
                                "repository-state reads, prefer the dedicated "
                                "git_status, git_diff, git_log, and git_show MCP "
                                "tools. ``grep`` is never denied but the result text "
                                "carries a hint that the MCP explore endpoint "
                                "(grep_files) is more efficient. (example values: "
                                "'ls', 'grep -r foo . | wc -l', 'python -m pytest "
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
                        "max_runtime_ms": _max_runtime_ms_property(),
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
                    "DANGEROUS: Execute unrestricted shell command. Blocks up to 45s (soft "
                    "reply budget); if still running, detaches with job_id to monitor via "
                    "exec_wait or abort with exec_cancel. timeout_ms sets stall window. "
                    "VCS policy: hg/svn and destructive git operations denied; git worktree "
                    "allowed. Returns stdout, stderr, exit code. On timeout/kill you get "
                    "is_error (not retryable) — fix command or adjust timeout_ms."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "Full shell command string. Shell operators (|, &&, ||, ;, &, >, >>, <, <<) "
                                "work as normal. VCS policy: hg/svn are NEVER allowed; known high-risk "
                                "git operations (push, stash, checkout, commit, reset, apply, tag, ...) "
                                "are denied, while git worktree is allowed. Scripts containing a blocked "
                                "operation run with a warning. For repository-state reads, prefer the "
                                "dedicated git_status, git_diff, git_log, and git_show MCP tools. "
                                '(example values: "make build", "npm test && npm lint").'
                            ),
                        },
                        "timeout_ms": _timeout_ms_property(),
                        "max_runtime_ms": _max_runtime_ms_property(),
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
                    "Alias for unsafe_exec. DANGEROUS: Execute unrestricted shell command. "
                    "Blocks up to 45s (soft reply budget); if still running, detaches with "
                    "job_id to monitor via exec_wait or abort with exec_cancel. timeout_ms sets "
                    "stall window. VCS policy enforced. Returns stdout, stderr, exit code. "
                    "On timeout/kill you get is_error (not retryable) — fix command or "
                    "adjust timeout_ms."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "Full shell command string. Shell operators (|, &&, ||, ;, &, >, >>, <, <<) "
                                "work as normal. VCS policy: hg/svn are NEVER allowed; known high-risk "
                                "git operations (push, stash, checkout, commit, reset, apply, tag, ...) "
                                "are denied, while git worktree is allowed. Scripts containing a blocked "
                                "operation run with a warning. For repository-state reads, prefer the "
                                "dedicated git_status, git_diff, git_log, and git_show MCP tools. "
                                '(example values: "make build", "npm test && npm lint").'
                            ),
                        },
                        "timeout_ms": _timeout_ms_property(),
                        "max_runtime_ms": _max_runtime_ms_property(),
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
                name=EXEC_WAIT_TOOL,
                description=(
                    "Wait on an active exec job by job_id. When a command in exec or "
                    "unsafe_exec exceeds the 45s soft reply budget, it detaches and returns "
                    "a job_id. Use exec_wait to poll or wait for completion up to wait_ms. "
                    "Returns intermediate output snapshot if still running, or final exit code "
                    "and output upon completion."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID returned when the command detached.",
                        },
                        "wait_ms": {
                            "type": "integer",
                            "description": (
                                f"Milliseconds to wait for the job (default: {EXEC_SOFT_REPLY_BUDGET_MS}, "
                                f"capped at {EXEC_SOFT_REPLY_BUDGET_MS})."
                            ),
                            "default": EXEC_SOFT_REPLY_BUDGET_MS,
                        },
                    },
                    "required": ["job_id"],
                },
                required_capability=McpCapability.PROCESS_EXEC_BOUNDED.value,
            ),
            module_name="ralph.mcp.tools._exec_job_tools",
            handler_name="handle_exec_wait",
        ),
        ToolSpec(
            metadata=_metadata(
                name=EXEC_CANCEL_TOOL,
                description=(
                    "Cancel an active or running exec job by job_id. Forcibly terminates "
                    "the process tree and reaps child processes."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID of the running or completed exec job to cancel.",
                        },
                    },
                    "required": ["job_id"],
                },
                required_capability=McpCapability.PROCESS_EXEC_BOUNDED.value,
            ),
            module_name="ralph.mcp.tools._exec_job_tools",
            handler_name="handle_exec_cancel",
        ),
    ]
