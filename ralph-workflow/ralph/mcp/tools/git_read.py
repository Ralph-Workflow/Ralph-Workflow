"""MCP Git read tool handlers.

Ports the Rust MCP Git read tools so agents can inspect repository state
through bounded read-only git commands from the workspace root.

Exported surface:

- ``handle_git_status`` — runs ``git status`` in the workspace root.
  Capability: ``GitStatusRead``.
- ``handle_git_diff`` — runs ``git diff [args]`` in the workspace root.
  The handler uses the *lenient* runner so a non-zero exit (which can
  happen when there is nothing to diff, or when a path filter matches
  no files) is surfaced as stdout/stderr rather than an exception.
  Capability: ``GitDiffRead``.
- ``handle_git_log`` — runs ``git log -<count> --oneline`` (default
  count = ``DEFAULT_LOG_COUNT`` = 10). Capability: ``GitStatusRead``.
- ``handle_git_show`` — runs ``git show <ref>`` for a single object.
  Capability: ``GitStatusRead``.
- ``parse_git_diff_params`` / ``parse_git_log_params`` /
  ``parse_git_show_params`` — the parameter parsers used by the
  handlers above (string-only args, bounded count, ref validation).
- ``run_git_command`` / ``run_git_command_lenient`` — the two
  subprocess runners. Both require a successful ``git`` exit code
  unless the lenient variant is used. They are the only call sites of
  the internal ``_run_git_subprocess`` helper, which always carries
  the fixed ``_GIT_READ_TIMEOUT_SECONDS = 30.0`` bound.

Trust boundary: every handler is gated on a ``McpCapability`` and runs
through a process spawned by ``ralph.process.manager``. The 30-second
hard timeout is the bounded-subprocess contract — a hung ``git status``
over a large ``vendor/`` submodule or a held ``.git`` lock cannot hang
the MCP server thread.

Side effects: spawns a ``git`` subprocess under the workspace root
(registered with the global ``ProcessManager``) and reads its
stdout/stderr. No write to the workspace, no network call. Timeouts
are converted into a non-retryable ``is_error`` result that names the
likely cause (vendor/ submodule or held lock) and tells the agent
*not* to retry unchanged.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

from ralph.mcp.explore.dirty_paths import (
    ExploreIndexLike,
    ExploreStoreLike,
    resolve_explore_index,
)
from ralph.mcp.tools._git_diff_params import GitDiffParams
from ralph.mcp.tools._git_execution_error import ExecutionError
from ralph.mcp.tools._git_log_params import GitLogParams
from ralph.mcp.tools._git_show_params import GitShowParams
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.process.manager import SpawnOptions, get_process_manager

GIT_STATUS_READ_CAPABILITY = "GitStatusRead"
GIT_DIFF_READ_CAPABILITY = "GitDiffRead"
DEFAULT_LOG_COUNT = 10
#: Number of tab-separated fields in a ``git diff --numstat`` line.
_NUMSTAT_FIELD_COUNT = 3
type GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[bytes]]
type CwdProvider = Callable[[], Path]


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for git command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def _workspace_root(workspace: object, *, cwd_provider: CwdProvider = Path.cwd) -> Path:
    if isinstance(workspace, WorkspaceWithRoot):
        return workspace.root
    root_value = cast("Path | str | None", getattr(workspace, "root", None))
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def parse_git_diff_params(params: Mapping[str, object]) -> GitDiffParams:
    """Parse git diff params, keeping only string arguments."""
    args_value = params.get("args")
    args = (
        [value for value in args_value if isinstance(value, str)]
        if isinstance(args_value, list)
        else []
    )
    return GitDiffParams(args=args)


def parse_git_log_params(params: Mapping[str, object]) -> GitLogParams:
    """Parse git log params with the Rust default count."""
    count_value = params.get("count", DEFAULT_LOG_COUNT)
    count = count_value if isinstance(count_value, int) and count_value >= 0 else DEFAULT_LOG_COUNT
    return GitLogParams(count=count)


def parse_git_show_params(params: Mapping[str, object]) -> GitShowParams:
    """Parse git show params."""
    ref_value = params.get("ref")
    if not isinstance(ref_value, str):
        raise InvalidParamsError("Missing 'ref' parameter")
    return GitShowParams(git_ref=ref_value)


def _decode_output(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def run_git_command(
    workspace: object,
    args: list[str],
    *,
    runner: GitRunner | None = None,
    cwd_provider: CwdProvider = Path.cwd,
) -> str:
    """Execute git and require a successful exit status."""
    git_runner = runner or _run_git_subprocess
    try:
        output = git_runner(["git", *args], _workspace_root(workspace, cwd_provider=cwd_provider))
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"git command timed out after {exc.timeout:g}s: {' '.join(args)}",
            timed_out=True,
        ) from exc
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc

    stdout = _decode_output(output.stdout)
    stderr = _decode_output(output.stderr)

    if output.returncode != 0:
        raise ExecutionError(f"git command failed: {stderr}")

    return stdout


def run_git_command_lenient(
    workspace: object,
    args: list[str],
    *,
    runner: GitRunner | None = None,
    cwd_provider: CwdProvider = Path.cwd,
) -> subprocess.CompletedProcess[bytes]:
    """Execute git and return a :class:`CompletedProcess` regardless of exit code.

    Callers that want a single text blob can use ``lenient_stdout`` or
    join ``result.stdout`` and ``result.stderr`` themselves.
    """
    git_runner = runner or _run_git_subprocess
    try:
        output = git_runner(["git", *args], _workspace_root(workspace, cwd_provider=cwd_provider))
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"git command timed out after {exc.timeout:g}s: {' '.join(args)}",
            timed_out=True,
        ) from exc
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute git: {exc}") from exc

    return output


def lenient_stdout(result: subprocess.CompletedProcess[bytes]) -> str:
    """Return the decoded combined stdout/stderr of a lenient git run."""
    return f"{_decode_output(result.stdout)}{_decode_output(result.stderr)}"


def _parse_numstat_count(value: str) -> int:
    """Parse a numstat field; ``"-"`` (binary file) maps to 0."""
    if value == "-":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


# MCP read tools must never block the server thread indefinitely: a hung
# `git status` (large vendor/ submodules, a held .git lock) would starve the
# agent of output and trip the idle watchdog. Git reads are bounded at a fixed
# 30s (they are short, fixed subcommands with no agent-tunable timeout, unlike
# the exec tool) and fail closed — communicate_and_cleanup terminates and kills
# the process tree on expiry, then re-raises TimeoutExpired for
# run_git_command* to convert into an actionable is_error result.
_GIT_READ_TIMEOUT_SECONDS = 30.0


def _run_git_subprocess(command: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    proc = get_process_manager().spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="git-mcp-read",
        ),
    )
    stdout, stderr = proc.communicate_and_cleanup(timeout=_GIT_READ_TIMEOUT_SECONDS)
    returncode = proc.returncode if proc.returncode is not None else 0
    return subprocess.CompletedProcess(command, returncode, stdout or b"", stderr or b"")


def _git_read_result(produce: Callable[[], str]) -> ToolResult:
    """Run a git read and wrap its output, converting a timeout into an
    actionable, non-retryable is_error result (NOT a propagated -32603)."""
    try:
        output = produce()
    except ExecutionError as exc:
        if not exc.timed_out:
            raise
        message = (
            f"{exc}\n"
            "This is expected for large vendor/ submodules or a held .git lock."
            " Re-issuing the identical call will time out again — narrow the command,"
            " exclude large submodules, or retry later. Do not retry unchanged."
        )
        return ToolResult(content=[ToolContent.text_content(message)], is_error=True)
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_git_status(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git status of the workspace.

    AC-11: the optional ``format`` argument selects between
    ``raw`` (default; unchanged legacy output) and ``compact``
    (ranked changed paths with role tags + byte budget).
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git status")
    format_value = params.get("format", "raw") if params else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "compact"}:
        raise InvalidParamsError(
            f"Invalid format: {format_value!r}; expected 'raw' or 'compact'"
        )
    if format_value == "raw":
        return _git_read_result(lambda: run_git_command(workspace, ["status"]))
    # AC-11: compact mode runs through the same timeout-wrapping
    # helper as raw mode so a hung ``git status`` returns an
    # actionable is_error result rather than an uncaught exception.
    return _git_read_result(
        lambda: _build_compact_status_payload(workspace, session=session)
    )


def _resolve_compact_status_index(
    workspace: object,
    session: object | None = None,
) -> tuple[ExploreIndexLike, ExploreStoreLike] | None:
    """Best-effort resolve of the explore index for compact status.

    AC-06: compact ``git_status`` may include bounded
    changed-symbol hints when the index is current. The lookup
    is best-effort: a missing handle, missing index, or any
    exception during resolution produces ``None`` so the caller
    can emit explicit unavailable/stale metadata without
    blocking the porcelain payload.
    """
    # The index can ride on the session, the workspace, or
    # ``workspace.session`` (the harness contract). Try the
    # obvious attributes first; the resolve helper is
    # session-shaped, so we pass any object that may carry an
    # ``explore_index`` attribute.
    handle: ExploreIndexLike | None = None
    workspace_session: object = (
        cast("object", getattr(workspace, "session", None))
        if workspace is not None
        else None
    )
    for candidate in (session, workspace, workspace_session):
        if candidate is None:
            continue
        try:
            handle = resolve_explore_index(candidate)
        except Exception:
            handle = None
        if handle is not None:
            break
    if handle is None:
        return None
    store: ExploreStoreLike | None = getattr(handle, "store", None)
    if store is None:
        return None
    return (handle, store)


def _compact_status_index_meta(
    workspace: object,
    paths: list[dict[str, object]],
    session: object | None = None,
) -> dict[str, object]:
    """Return index metadata for the compact ``git_status`` payload.

    AC-06: when the index is current, attach bounded
    changed-symbol hints per changed path. When the index is
    missing, stale, or otherwise unavailable, surface explicit
    metadata so agents do not guess. The function never raises
    into the caller.
    """
    meta: dict[str, object] = {
        "index_used": False,
        "index_generation": 0,
        "index_status": "unavailable",
        "changed_symbols": {},
        "fallback_reason": "index_not_attached",
    }
    resolved = _resolve_compact_status_index(workspace, session=session)
    if resolved is not None:
        meta = _compute_compact_status_meta(resolved[1], paths, meta)
    return meta


def _compute_compact_status_meta(
    store: ExploreStoreLike,
    paths: list[dict[str, object]],
    meta: dict[str, object],
) -> dict[str, object]:
    """Compute the compact ``git_status`` index metadata for a resolved store.

    AC-06: returns a copy of ``meta`` annotated with the
    observed index state. The function uses bounded
    aggregates (``peek_dirty_paths`` + ``has_deleted_files``)
    so the compact path never materializes the entire
    ``files`` table. Errors during freshness reads are
    treated as ``unavailable`` (never as a fresh index) so a
    corrupt or unreadable store cannot produce silent green
    hints.
    """
    result: dict[str, object] = dict(meta)
    try:
        current_generation_raw = store.get_setting("current_generation")
        current_generation = (
            int(current_generation_raw)
            if isinstance(current_generation_raw, str) and current_generation_raw.isdigit()
            else 0
        )
    except Exception:
        return result
    if current_generation <= 0:
        result["index_status"] = "stale"
        result["fallback_reason"] = "no_committed_generation"
        return result
    try:
        dirty_paths = list(store.peek_dirty_paths())
    except Exception:
        result["index_status"] = "unavailable"
        result["fallback_reason"] = "dirty_paths_read_failed"
        return result
    try:
        has_deleted = bool(store.has_deleted_files())
    except Exception:
        result["index_status"] = "unavailable"
        result["fallback_reason"] = "deleted_files_read_failed"
        return result
    is_stale: bool = bool(dirty_paths) or has_deleted
    if is_stale:
        result["index_status"] = "stale"
        result["fallback_reason"] = "index_reports_stale"
        return result
    hints: dict[str, list[dict[str, object]]] = {}
    for card in paths:
        path_value = card.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        try:
            symbols = store.find_symbols(path=path_value)
        except Exception:
            symbols = []
        if not symbols:
            continue
        hints[path_value] = [
            {
                "qualified_name": sym.qualified_name,
                "kind": sym.kind,
                "symbol_id": sym.symbol_id,
                "span_id": sym.span_id,
            }
            for sym in symbols[:3]
        ]
    result["index_used"] = True
    result["index_generation"] = current_generation
    result["index_status"] = "current"
    result["changed_symbols"] = hints
    result["fallback_reason"] = None
    return result


def _strict_max_bytes_param(
    params: Mapping[str, object],
) -> int:
    """Strict-bounded ``max_bytes`` for ``git_diff`` summary mode.

    AC-06: rejects ``bool``, ``0``, negatives, malformed strings,
    non-integer floats, and oversized values. Returns the
    default (``50_000``) only when the caller omits the key.
    The minimum is ``1`` so the slice ``text[:max_bytes]`` always
    returns at least one byte when the diff is non-empty; the
    maximum matches the documented default so callers cannot
    silently extend the excerpt cap.
    """
    default = 50_000
    minimum = 1
    if "max_bytes" not in params:
        return default
    raw: object = params["max_bytes"]
    if isinstance(raw, bool):
        raise InvalidParamsError(
            f"max_bytes must be an integer in [{minimum}, {default}]; "
            f"got bool {raw!r}"
        )
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError as exc:
            raise InvalidParamsError(
                f"max_bytes must be an integer in [{minimum}, {default}]; "
                f"got {raw!r}"
            ) from exc
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise InvalidParamsError(
                f"max_bytes must be an integer in [{minimum}, {default}]; "
                f"got non-integer float {raw!r}"
            )
        value = int(raw)
    else:
        raise InvalidParamsError(
            f"max_bytes must be an integer in [{minimum}, {default}]; "
            f"got {type(raw).__name__}"
        )
    if value < minimum or value > default:
        raise InvalidParamsError(
            f"max_bytes must be an integer in [{minimum}, {default}]; got {value}"
        )
    return value


def _build_compact_status_payload(
    workspace: object,
    session: object | None = None,
) -> str:
    """Build the compact-mode JSON payload for ``git status``.

    Ponytail: isolated helper so the timeout-wrapping
    ``_git_read_result`` can call it without a try/except chain.
    The lenient runner is used so a non-zero exit (e.g. outside a
    git repo) still surfaces a useful result; the returncode is
    inspected separately by the caller if needed.

    AC-06: when an explore index is attached and current, the
    payload includes bounded changed-symbol hints per changed
    path. When the index is missing or stale, explicit
    ``index_status`` and ``fallback_reason`` metadata is
    surfaced so agents do not guess.
    """
    raw_result = run_git_command_lenient(workspace, ["status", "--porcelain"])
    lines = raw_result.stdout.decode("utf-8", errors="replace").splitlines()
    cards: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        role = "staged" if code[0] != " " and code[0] != "?" else "unstaged"
        cards.append(
            {
                "path": path,
                "code": code,
                "role": role,
                "untracked": code == "??",
            }
        )
    payload: dict[str, object] = {
        "format": "compact",
        "changed_count": len(cards),
        "staged_count": sum(1 for c in cards if c["role"] == "staged"),
        "unstaged_count": sum(1 for c in cards if c["role"] == "unstaged"),
        "untracked_count": sum(1 for c in cards if c["untracked"]),
        "paths": cards,
        "raw_lines": lines,
    }
    payload.update(_compact_status_index_meta(workspace, cards, session=session))
    return json.dumps(payload)


def handle_git_diff(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git diff of the workspace.

    AC-11: ``format=raw`` (default) preserves the legacy text
    output. ``format=summary`` returns a compact summary card
    with changed files, insertion/deletion counts, and an output
    byte cap so agents do not pay for unread diff bodies.
    ``max_bytes`` caps the returned text; default 50_000.
    AC-06: ``max_bytes`` is strictly bounded to a positive
    integer in ``[1, 50_000]`` so callers cannot bypass the
    excerpt cap with zero, negative, malformed, or non-integer
    values.
    """
    require_capability(session, GIT_DIFF_READ_CAPABILITY, "Git diff")
    parsed = parse_git_diff_params(params)
    format_value = params.get("format", "raw") if params else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "summary"}:
        raise InvalidParamsError(
            f"Invalid format: {format_value!r}; expected 'raw' or 'summary'"
        )
    if format_value == "raw":
        return _git_read_result(lambda: lenient_stdout(
            run_git_command_lenient(workspace, ["diff", *parsed.args])
        ))
    max_bytes = _strict_max_bytes_param(params)
    # AC-11: wrap the summary branch in ``_git_read_result`` so a
    # timeout converts into the same actionable ``is_error`` result
    # as the raw branch, never an uncaught exception.
    return _git_read_result(
        lambda: _build_diff_summary_payload(workspace, parsed.args, max_bytes)
    )


def _build_diff_summary_payload(
    workspace: object,
    git_args: Sequence[str],
    max_bytes: int,
) -> str:
    """Build the summary-mode JSON payload for ``git diff``.

    Ponytail: isolated helper so the timeout-wrapping
    ``_git_read_result`` can call it without a try/except chain.
    """
    raw_result = run_git_command_lenient(workspace, ["diff", "--numstat", *git_args])
    numstat_output = raw_result.stdout.decode("utf-8", errors="replace")
    cards: list[dict[str, object]] = []
    for line in numstat_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < _NUMSTAT_FIELD_COUNT:
            continue
        added_str = parts[0]
        removed_str = parts[1]
        path_str = parts[2]
        added_count = _parse_numstat_count(added_str)
        removed_count = _parse_numstat_count(removed_str)
        cards.append(
            {
                "path": path_str,
                "added": added_count,
                "removed": removed_count,
            }
        )
    full_result = run_git_command_lenient(workspace, ["diff", *git_args])
    full_text = full_result.stdout.decode("utf-8", errors="replace")
    truncated = len(full_text) > max_bytes
    if truncated:
        full_text = full_text[:max_bytes]
    added_total = sum(
        c["added"] for c in cards if isinstance(c["added"], int)
    )
    removed_total = sum(
        c["removed"] for c in cards if isinstance(c["removed"], int)
    )
    payload: dict[str, object] = {
        "format": "summary",
        "files_changed": len(cards),
        "added": added_total,
        "removed": removed_total,
        "files": cards,
        "diff_excerpt": full_text,
        "truncated": truncated,
        "max_bytes": max_bytes,
    }
    return json.dumps(payload)


def handle_git_log(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Read the git commit log.

    Args:
        session: Agent session; must declare ``GitStatusRead``.
        workspace: Workspace surface whose root is the cwd for ``git log``.
        params: Mapping with optional ``count`` (positive integer,
            defaults to ``DEFAULT_LOG_COUNT = 10``).

    Returns:
        A ``ToolResult`` whose text content is the ``git log -<count>
        --oneline`` output.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitStatusRead``.
        InvalidParamsError: When ``params`` fails ``parse_git_log_params``.

    Side effects:
        Spawns a ``git log`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git log")
    parsed = parse_git_log_params(params)
    return _git_read_result(
        lambda: run_git_command(workspace, ["log", f"-{parsed.count}", "--oneline"])
    )


def handle_git_show(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Show a git object by ref.

    Args:
        session: Agent session; must declare ``GitStatusRead``.
        workspace: Workspace surface whose root is the cwd for ``git show``.
        params: Mapping with required ``ref`` (string; commit-ish, tag,
            or branch) per ``parse_git_show_params``.

    Returns:
        A ``ToolResult`` whose text content is the ``git show <ref>``
        output.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``GitStatusRead``.
        InvalidParamsError: When ``params`` fails ``parse_git_show_params``.

    Side effects:
        Spawns a ``git show`` subprocess registered with the global
        ``ProcessManager``. Bounded by ``_GIT_READ_TIMEOUT_SECONDS = 30``.
        No workspace writes, no network calls.
    """
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git show")
    parsed = parse_git_show_params(params)
    return _git_read_result(lambda: run_git_command(workspace, ["show", parsed.git_ref]))


__all__ = [
    "GIT_DIFF_READ_CAPABILITY",
    "GIT_STATUS_READ_CAPABILITY",
    "ExecutionError",
    "GitDiffParams",
    "GitLogParams",
    "GitShowParams",
    "WorkspaceWithRoot",
    "handle_git_diff",
    "handle_git_log",
    "handle_git_show",
    "handle_git_status",
    "parse_git_diff_params",
    "parse_git_log_params",
    "parse_git_show_params",
    "run_git_command",
    "run_git_command_lenient",
]
