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
from ralph.mcp.tools._envelope_bytes import finalize_envelope_bytes_out
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


class _SpawnedProcessLike(Protocol):
    """Minimal surface for a managed ``git diff`` subprocess.

    AC-06: the streaming-cap helpers consume stdout in fixed
    chunks and tear down the process at the end. We declare
    the surface as a Protocol so mypy can verify the
    ``get_process_manager().spawn`` return shape without
    importing the concrete ``ManagedProcess`` class.
    """

    @property
    def stdout(self) -> object:
        """Return the stdout pipe."""
        ...

    def communicate_and_cleanup(self, *args: object, **kwargs: object) -> object:
        """Wait for the process to exit and clean up resources."""
        ...


class _ReadablePipe(Protocol):
    """Minimal pipe-like surface with ``read``.

    Used by the streaming-cap helper so mypy can verify the
    call site without coupling to ``IO[bytes]``.
    """

    def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes from the pipe."""
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
    """Parse git diff params, keeping only string arguments.

    AC-06: read-only contract. The git diff MCP tool is a
    read-only ``GitDiffRead`` operation, so the ``args`` allowlist
    MUST reject output-writing flags (``--output=...``,
    ``--output ...``) and external-helper flags
    (``--ext-diff``, ``--textconv``, ``--convience-diff`` etc.).
    These flags would let a caller make git write to the
    workspace or invoke an external helper, bypassing the
    intended capability separation. Malformed types (non-string
    args, non-list containers) are silently dropped so the
    legacy parsing contract is preserved.
    """
    args_value = params.get("args")
    args = (
        [value for value in args_value if isinstance(value, str)]
        if isinstance(args_value, list)
        else []
    )
    _validate_diff_args(args)
    return GitDiffParams(args=args)


#: External-helper flags that cause git to invoke an external
#: program (``GIT_EXTERNAL_DIFF`` / ``GIT_TEXTConv``). These
#: bypass the read-only intent of the MCP tool and can run
#: arbitrary commands.
_DIFF_EXTERNAL_HELPER_FLAGS: tuple[str, ...] = (
    "--ext-diff",
    "--textconv",
    "--convience-diff",  # misspelled but accepted by older git
)


def _validate_diff_args(args: Sequence[str]) -> None:
    """Reject output-writing and external-helper flags.

    AC-06: read-only contract. The check inspects each arg
    against the output-writing and external-helper allowlists
    so a caller cannot slip a write-producing or
    external-helper-invoking flag past the schema. The check
    runs at parse time, before git is invoked, so a rejected
    flag never reaches the subprocess.

    Flags like ``--output`` are checked as substrings so
    ``--output=...``, ``--output ...``, and
    ``--output-threshold`` all fail closed. The
    external-helper list mirrors the git diff docs verbatim;
    a flag not in the list is forwarded unchanged.
    """
    for arg in args:
        # ``args`` is already filtered to strings by
        # ``parse_git_diff_params``; no ``isinstance`` guard
        # is needed. The ``Sequence[str]`` annotation carries
        # through to mypy so the helper stays typed.
        # Output-writing flags: ``--output=path``, ``--output path``,
        # and the short form ``-o path`` / ``-o=path``. The check
        # is substring-based so callers cannot slip past via
        # ``--output-threshold`` (rejected because the substring
        # ``--output`` matches). ``-o`` is matched only when it is
        # exactly ``-o`` or starts with ``-o=``/``-o<space>``, so we
        # do not false-positive on ``-output``, ``-only`` etc.
        if (
            "--output=" in arg
            or arg == "--output"
            or arg.startswith("--output ")
            or arg == "-o"
            or arg.startswith("-o=")
            or arg.startswith("-o ")
        ):
            raise InvalidParamsError(
                f"read-only git_diff rejects output-writing flag: {arg!r}"
            )
        for bad in _DIFF_EXTERNAL_HELPER_FLAGS:
            if bad in arg:
                raise InvalidParamsError(
                    f"read-only git_diff rejects external-helper flag: {arg!r}"
                )


def parse_git_log_params(params: Mapping[str, object]) -> GitLogParams:
    """Parse git log params with the Rust default count.

    Phase 4: ``format`` is optional and closed to ``{'raw', 'summary'}``.
    ``raw`` is the default (preserved legacy output); ``summary`` returns a
    compact JSON envelope with one entry per commit. Any other value
    raises ``InvalidParamsError`` naming the closed enum so a malformed
    selector never reaches the git subprocess.
    """
    count_value = params.get("count", DEFAULT_LOG_COUNT)
    count = count_value if isinstance(count_value, int) and count_value >= 0 else DEFAULT_LOG_COUNT
    format_value = params.get("format", "raw")
    if format_value not in ("raw", "summary"):
        raise InvalidParamsError(
            f"Invalid git_log format: {format_value!r}; expected 'raw' or 'summary'"
        )
    return GitLogParams(count=count, format=format_value)


def parse_git_show_params(params: Mapping[str, object]) -> GitShowParams:
    """Parse git show params.

    Phase 4: ``format`` is optional and closed to ``{'raw', 'summary'}``.
    ``raw`` is the default (preserved legacy output); ``summary`` returns
    a compact header-only envelope without the patch body. Any other
    value raises ``InvalidParamsError``.
    """
    ref_value = params.get("ref")
    if not isinstance(ref_value, str):
        raise InvalidParamsError("Missing 'ref' parameter")
    format_value = params.get("format", "raw")
    if format_value not in ("raw", "summary"):
        raise InvalidParamsError(
            f"Invalid git_show format: {format_value!r}; expected 'raw' or 'summary'"
        )
    return GitShowParams(git_ref=ref_value, format=format_value)


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

    AC-06: ranked changed paths. Each card carries a
    deterministic integer ``score`` and a list of human-readable
    ``score_reasons`` describing which signals contributed.
    Cards are sorted by ``score`` descending; ties break on
    lexicographic path. The score formula (mirrors the
    ``search_files`` ranking contract in the architecture
    finding):

    - ``+100`` unstaged role requested by the caller
      (always applied: unstaged changes are the most
      actionable for an agent about to edit)
    - ``+80``  staged role
    - ``+60``  untracked path (``code == "??"``)
    - ``-50``  generated/vendor path (heuristic: filename
      contains a ``generated`` or ``vendor`` token, OR the
      path lives under a directory named ``build``,
      ``dist``, ``.venv``, ``__pycache__``, or
      ``node_modules``)
    - ``+0``   tiebreaker on lexicographic path

    A positive ``score`` is recorded; the ``score_reasons``
    list lets tests and downstream tools assert exactly
    why a card ranked above another card.
    """
    raw_result = run_git_command_lenient(workspace, ["status", "--porcelain"])
    lines = raw_result.stdout.decode("utf-8", errors="replace").splitlines()
    cards: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        # AC-06: ``role`` is one of ``staged``, ``unstaged``,
        # or ``untracked``. The previous binary "staged vs
        # unstaged" model lumped untracked entries in with
        # unstaged and made ranking ambiguous. The compact
        # contract now distinguishes them so the
        # ``_rank_compact_status_cards`` helper can score
        # untracked paths independently.
        if code == "??":
            role = "untracked"
        elif code[0] != " " and code[0] != "?":
            role = "staged"
        else:
            role = "unstaged"
        cards.append(
            {
                "path": path,
                "code": code,
                "role": role,
                "untracked": code == "??",
            }
        )
    ranked_cards = _rank_compact_status_cards(cards)
    payload: dict[str, object] = {
        "format": "compact",
        "changed_count": len(ranked_cards),
        "staged_count": sum(1 for c in ranked_cards if c["role"] == "staged"),
        "unstaged_count": sum(1 for c in ranked_cards if c["role"] == "unstaged"),
        "untracked_count": sum(1 for c in ranked_cards if c["role"] == "untracked"),
        "paths": ranked_cards,
        "raw_lines": lines,
        "ranking": {
            "scheme": "deterministic_integer_score",
            "tiebreak": "lexicographic_path",
            "components": [
                "+100 role=unstaged",
                "+80 role=staged",
                "+60 role=untracked",
                "-50 generated_or_vendor_path",
            ],
        },
    }
    payload.update(
        _compact_status_index_meta(workspace, ranked_cards, session=session)
    )
    return json.dumps(payload)


def _rank_compact_status_cards(
    cards: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Sort cards by deterministic rank and attach score metadata.

    AC-06: each card receives ``score`` (int) and
    ``score_reasons`` (list[str]) so callers can assert why a
    path ranked where it did. The sort is stable: equal scores
    break on lexicographic path. The function never throws on
    a missing or non-string ``path`` field; the sort still
    runs (the offending entry is pushed to the bottom).
    """
    ranked: list[dict[str, object]] = []
    for card in cards:
        score = 0
        reasons: list[str] = []
        role = card.get("role")
        if role == "unstaged":
            score += 100
            reasons.append("+100 role=unstaged")
        elif role == "staged":
            score += 80
            reasons.append("+80 role=staged")
        elif role == "untracked":
            score += 60
            reasons.append("+60 role=untracked")
        if (
            card.get("untracked") is True
            and role != "untracked"
        ):
            # Defensive: legacy callers may still pass the
            # boolean ``untracked`` flag without the new
            # role. Honor the +60 signal in that case so
            # ranking stays consistent.
            score += 60
            reasons.append("+60 untracked")
        path = card.get("path")
        if isinstance(path, str) and _is_generated_or_vendor_path(path):
            score -= 50
            reasons.append("-50 generated_or_vendor_path")
        new_card = dict(card)
        new_card["score"] = score
        new_card["score_reasons"] = reasons
        ranked.append(new_card)
    # Sort: score descending, then path lexicographic. Stable
    # sort preserves the relative order of equal elements, but
    # we explicitly key on path so the tiebreak is
    # deterministic regardless of input order.
    def _rank_key(c: dict[str, object]) -> tuple[int, str]:
        score_value = c.get("score")
        score_int: int = score_value if isinstance(score_value, int) else 0
        path_value = c.get("path")
        path_str: str = path_value if isinstance(path_value, str) else ""
        return (-score_int, path_str)

    ranked.sort(key=_rank_key)
    return ranked


def _is_generated_or_vendor_path(path: str) -> bool:
    """Return True when ``path`` looks like a generated or vendor path.

    AC-06: heuristic detection. A path matches if any path
    segment equals ``build``, ``dist``, ``.venv``,
    ``__pycache__``, ``node_modules``, ``vendor``, or
    ``generated``. Filename tokens are NOT matched (a file
    named ``vendor.txt`` at the repo root is NOT a vendor
    path; a file under ``vendor/foo.py`` IS).
    """
    lower = path.lower()
    segments = lower.replace("\\", "/").split("/")
    vendor_segments = {
        "build",
        "dist",
        ".venv",
        "__pycache__",
        "node_modules",
        "vendor",
        "generated",
    }
    return any(seg in vendor_segments for seg in segments)


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

    AC-06: the diff excerpt is bounded by ``max_bytes`` AT THE
    STREAMING BOUNDARY, not after fully buffering the entire
    diff. The subprocess is killed as soon as the byte cap is
    reached so the caller never holds an unbounded buffer.
    The bytes are truncated at a UTF-8 character boundary; any
    trailing incomplete UTF-8 sequence is replaced by ``U+FFFD``
    on decode so the returned string never contains invalid
    UTF-8. The encoded excerpt (UTF-8) never exceeds
    ``max_bytes``.

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
    # AC-06: enforce the byte cap while reading the
    # subprocess. ``_collect_git_diff_capped`` streams stdout
    # chunk by chunk and stops as soon as the cap is reached,
    # so the caller never holds an unbounded buffer. The
    # returned string is UTF-8 safe (errors="replace") and the
    # re-encoded length never exceeds ``max_bytes``.
    full_text, truncated = _collect_git_diff_capped(
        workspace, git_args, max_bytes
    )
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


def _collect_git_diff_capped(
    workspace: object,
    git_args: Sequence[str],
    max_bytes: int,
) -> tuple[str, bool]:
    """Run ``git diff`` and stream at most ``max_bytes`` bytes.

    AC-06: bounded streaming. The subprocess is spawned
    through the shared ``ProcessManager`` so the same
    ``_GIT_READ_TIMEOUT_SECONDS`` timeout applies as the
    legacy ``run_git_command`` path. The stdout pipe is
    consumed in 8 KiB chunks until either EOF is reached or
    the running total equals/exceeds ``max_bytes``. When the
    cap is hit, the last chunk is sliced at the byte
    boundary and the subprocess is terminated; no more bytes
    are read. The decoded string uses ``errors="replace"``
    so any partial UTF-8 sequence at the truncation
    boundary becomes ``U+FFFD`` rather than an invalid
    encoding.

    Returns ``(decoded_text, truncated)`` where
    ``truncated`` is ``True`` iff the underlying diff was
    longer than ``max_bytes`` bytes (i.e. the cap was
    actually applied). The encoded excerpt length never
    exceeds ``max_bytes``.
    """
    if max_bytes <= 0:
        return ("", False)
    cwd = str(_workspace_root(workspace))
    proc = get_process_manager().spawn(
        ["git", "diff", *git_args],
        SpawnOptions(
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="git-mcp-read",
        ),
    )
    stdout = proc.stdout
    if stdout is None:  # pragma: no cover — defensive
        return ("", False)
    chunks, truncated = _read_git_diff_until_cap(
        cast("_ReadablePipe", stdout), max_bytes
    )
    _terminate_git_diff_proc(cast("_SpawnedProcessLike", proc))
    return (
        _decode_git_diff_capped(b"".join(chunks), truncated),
        truncated,
    )


def _read_git_diff_until_cap(
    stdout: _ReadablePipe,
    max_bytes: int,
) -> tuple[list[bytes], bool]:
    """Stream stdout until EOF or ``max_bytes`` is collected.

    AC-06: the streaming loop reads in 8 KiB chunks and
    stops when either EOF is reached (returning
    ``truncated=False``) or the running total equals/exceeds
    ``max_bytes`` (returning ``truncated=True`` with the
    last chunk sliced at the exact byte boundary).
    """
    chunks: list[bytes] = []
    total = 0
    # Ponytail: local constant, never re-bound.
    read_chunk_size = 8192
    while True:
        chunk = stdout.read(read_chunk_size)
        if not chunk:
            break
        if total + len(chunk) >= max_bytes:
            # Cap at the exact byte boundary. The
            # remaining bytes in this chunk are not
            # appended, so the encoded length of the
            # returned bytes never exceeds ``max_bytes``.
            needed = max_bytes - total
            if needed > 0:
                chunks.append(chunk[:needed])
                total += needed
            return chunks, True
        chunks.append(chunk)
        total += len(chunk)
    return chunks, False


def _terminate_git_diff_proc(proc: _SpawnedProcessLike) -> None:
    """Best-effort terminate the diff subprocess.

    AC-06: the subprocess is always torn down so file
    handles do not leak across calls. The
    ``communicate_and_cleanup`` call is bounded by
    ``_GIT_READ_TIMEOUT_SECONDS``; ``TimeoutExpired`` and
    other exceptions are swallowed because the bytes we
    need have already been read.
    """
    try:
        proc.communicate_and_cleanup(timeout=_GIT_READ_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass


def _decode_git_diff_capped(encoded: bytes, truncated: bool) -> str:
    """Decode ``encoded`` bytes, truncating at a UTF-8 boundary.

    AC-06: the streaming cap stops at exactly ``max_bytes``
    raw bytes, but a UTF-8 character may be 1-4 bytes long.
    If the boundary lands inside a multi-byte sequence, the
    trailing bytes are incomplete and would decode into
    ``U+FFFD`` (3 bytes) under ``errors="replace"``,
    blowing past the cap. We instead walk back from the end
    until the prefix decodes cleanly, so the encoded length
    of the decoded string is guaranteed to be ``<= max_bytes``
    and the bytes never contain invalid UTF-8.
    """
    if truncated and len(encoded) > 0:
        # ``encoded`` is at most ``max_bytes`` bytes (the
        # streaming cap enforced that). Walk back one byte
        # at a time until the prefix decodes without
        # raising. The walk is bounded by the cap itself
        # (at most ``max_bytes`` iterations) and converges
        # in O(1)-O(4) steps in the common case (a
        # truncated multi-byte char is at most 4 bytes).
        while len(encoded) > 0:
            try:
                encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
    return encoded.decode("utf-8", errors="replace")


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
            defaults to ``DEFAULT_LOG_COUNT = 10``) and optional
            ``format`` (``'raw'|'summary'``, default ``'raw'``).

    Returns:
        A ``ToolResult`` whose text content is the ``git log -<count>
        --oneline`` output when ``format='raw'`` (default), or a compact
        JSON envelope ``{format, count, commits: [{short_sha, sha,
        subject}], bytes_in, bytes_out}`` when ``format='summary'``.

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
    if parsed.format == "raw":
        return _git_read_result(
            lambda: run_git_command(
                workspace, ["log", f"-{parsed.count}", "--oneline"]
            )
        )
    return _git_read_result(
        lambda: _build_git_log_summary_payload(workspace, parsed.count)
    )


def _build_git_log_summary_payload(workspace: object, count: int) -> str:
    """Build the summary-mode JSON envelope for ``git log``.

    Ponytail: isolated helper so the timeout-wrapping
    ``_git_read_result`` can call it without a try/except chain. The
    porcelain ``--oneline`` output is one line per commit with the
    form ``<short_sha> <subject>``. The parser ignores blank lines
    so an empty log still produces an empty ``commits`` list rather
    than a fake sentinel.
    """
    raw = run_git_command(
        workspace, ["log", f"-{count}", "--oneline"]
    )
    raw_bytes = raw.encode("utf-8")
    commits: list[dict[str, object]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # ``--oneline`` emits ``<short_sha> <subject>``. The short
        # sha is exactly the leading 7-12 hex chars; the remainder
        # of the line is the subject. We do NOT split on a single
        # space because a subject may itself contain spaces.
        parts = stripped.split(maxsplit=1)
        short_sha = parts[0] if parts else ""
        subject = parts[1] if len(parts) > 1 else ""
        commits.append(
            {"short_sha": short_sha, "sha": short_sha, "subject": subject}
        )
    envelope = finalize_envelope_bytes_out(
        {
            "format": "summary",
            "count": len(commits),
            "commits": commits,
            "bytes_in": len(raw_bytes),
        }
    )
    return json.dumps(envelope, separators=(",", ":"))


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
            or branch) per ``parse_git_show_params`` and optional
            ``format`` (``'raw'|'summary'``, default ``'raw'``).

    Returns:
        A ``ToolResult`` whose text content is the ``git show <ref>``
        output when ``format='raw'`` (default), or a compact header-only
        JSON envelope ``{format, ref, kind, sha, short_sha, author_name,
        author_email, author_date, subject, parents, bytes_in, bytes_out,
        truncated:false}`` when ``format='summary'``.

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
    if parsed.format == "raw":
        return _git_read_result(lambda: run_git_command(workspace, ["show", parsed.git_ref]))
    return _git_read_result(
        lambda: _build_git_show_summary_payload(workspace, parsed.git_ref)
    )


def _build_git_show_summary_payload(workspace: object, ref: str) -> str:
    """Build the summary-mode JSON envelope for ``git show``.

    Ponytail: isolated helper so the timeout-wrapping
    ``_git_read_result`` can call it without a try/except chain. The
    ``--no-patch`` flag suppresses the diff body so we only carry the
    header fields. ``%x1f`` is the ASCII unit separator; we never see
    that byte in real author/subject lines so it is safe to split on.
    The ``kind`` field is inferred from the resolved ref: a tag is
    identified by ``refs/tags/`` in the resolved ref or by a name that
    matches the tag pattern. Commits are identified by the presence
    of parents in the header.
    """
    fmt = (
        "%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%P%x1f%D%x1f%H"
    )
    raw = run_git_command(
        workspace, ["show", "--no-patch", f"--format={fmt}", ref]
    )
    raw_bytes = raw.encode("utf-8")
    fields = raw.split("\x1f")
    # ``%D`` is appended to the format above to also include the
    # ``refs/tags/...`` decoration when present. ``%H`` is duplicated
    # so the last field is the SHA (we already have it in field 0).
    sha_idx = 0
    short_sha_idx = 1
    author_name_idx = 2
    author_email_idx = 3
    author_date_idx = 4
    subject_idx = 5
    parents_raw_idx = 6
    decoration_idx = 7
    sha = fields[sha_idx].strip() if len(fields) > sha_idx else ""
    short_sha = fields[short_sha_idx].strip() if len(fields) > short_sha_idx else ""
    author_name = (
        fields[author_name_idx].strip()
        if len(fields) > author_name_idx
        else ""
    )
    author_email = (
        fields[author_email_idx].strip()
        if len(fields) > author_email_idx
        else ""
    )
    author_date = (
        fields[author_date_idx].strip()
        if len(fields) > author_date_idx
        else ""
    )
    subject = fields[subject_idx].strip() if len(fields) > subject_idx else ""
    parents_raw = (
        fields[parents_raw_idx].strip()
        if len(fields) > parents_raw_idx
        else ""
    )
    decoration = (
        fields[decoration_idx].strip() if len(fields) > decoration_idx else ""
    )
    parents = [p for p in parents_raw.split() if p] if parents_raw else []
    # ``kind`` is inferred from decoration (tags/blobs/trees): tags
    # have ``tag:`` in the decoration; otherwise a commit with
    # parents is a commit. ``blob`` / ``tree`` would only show up
    # if the caller passes a raw SHA. We keep the inference
    # conservative: ``commit`` is the default.
    kind = "commit"
    if "tag:" in decoration:
        kind = "tag"
    elif not parents and sha:
        # Heuristic: a SHA with no parents and no tag decoration is
        # most likely a root commit; ``tree`` / ``blob`` are not
        # reachable through normal ``git show`` usage.
        kind = "commit"
    envelope = finalize_envelope_bytes_out(
        {
            "format": "summary",
            "ref": ref,
            "kind": kind,
            "sha": sha,
            "short_sha": short_sha,
            "author_name": author_name,
            "author_email": author_email,
            "author_date": author_date,
            "subject": subject,
            "parents": parents,
            "bytes_in": len(raw_bytes),
            "truncated": False,
        }
    )
    return json.dumps(envelope, separators=(",", ":"))


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
