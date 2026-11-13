from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
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
from ralph.mcp.tools._git_cwd_validator import resolve_git_cwd
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
from ralph.process.manager import (
    ProcessTerminationIssuer,
    SpawnOptions,
    get_process_manager,
)

GIT_STATUS_READ_CAPABILITY = "GitStatusRead"
GIT_DIFF_READ_CAPABILITY = "GitDiffRead"
DEFAULT_LOG_COUNT = 10
_NUMSTAT_FIELD_COUNT = 3
type GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[bytes]]
type CwdProvider = Callable[[], Path]


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    @property
    def root(self) -> Path: ...


class _SpawnedProcessLike(Protocol):
    @property
    def stdout(self) -> object: ...

    @property
    def stderr(self) -> object: ...

    def kill(
        self,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "operator_cancellation",
    ) -> None: ...

    def communicate_and_cleanup(self, *args: object, **kwargs: object) -> object: ...


class _ReadablePipe(Protocol):
    def read(self, n: int) -> bytes: ...


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
    args_value = params.get("args")
    args = (
        [value for value in args_value if isinstance(value, str)]
        if isinstance(args_value, list)
        else []
    )
    _validate_diff_args(args)
    return GitDiffParams(args=args)


_DIFF_EXTERNAL_HELPER_FLAGS: tuple[str, ...] = (
    "--ext-diff",
    "--textconv",
    "--convience-diff",
)


def _validate_diff_args(args: Sequence[str]) -> None:
    for arg in args:
        if "\x00" in arg:
            raise InvalidParamsError(f"read-only git_diff rejects an embedded NUL in: {arg!r}")
        if (
            "--output=" in arg
            or arg == "--output"
            or arg.startswith("--output ")
            or arg == "-o"
            or arg.startswith("-o=")
            or arg.startswith("-o ")
        ):
            raise InvalidParamsError(f"read-only git_diff rejects output-writing flag: {arg!r}")
        for bad in _DIFF_EXTERNAL_HELPER_FLAGS:
            if bad in arg:
                raise InvalidParamsError(
                    f"read-only git_diff rejects external-helper flag: {arg!r}"
                )


def parse_git_log_params(params: Mapping[str, object]) -> GitLogParams:
    count_value = params.get("count", DEFAULT_LOG_COUNT)
    count = count_value if isinstance(count_value, int) and count_value >= 0 else DEFAULT_LOG_COUNT
    format_value = params.get("format", "raw")
    if format_value not in ("raw", "summary"):
        raise InvalidParamsError(
            f"Invalid git_log format: {format_value!r}; expected 'raw' or 'summary'"
        )
    return GitLogParams(count=count, format=format_value)


def parse_git_show_params(params: Mapping[str, object]) -> GitShowParams:
    ref_value = params.get("ref")
    if not isinstance(ref_value, str):
        raise InvalidParamsError("Missing 'ref' parameter")
    if ref_value.startswith("-"):
        raise InvalidParamsError(
            f"read-only git_show rejects a flag-shaped ref: {ref_value!r}; pass a revision"
        )
    if "\x00" in ref_value:
        raise InvalidParamsError(
            f"read-only git_show rejects an embedded NUL in ref: {ref_value!r}"
        )
    format_value = params.get("format", "raw")
    if format_value not in ("raw", "summary"):
        raise InvalidParamsError(
            f"Invalid git_show format: {format_value!r}; expected 'raw' or 'summary'"
        )
    return GitShowParams(git_ref=ref_value, format=format_value)


def _decode_output(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _resolve_git_cwd(
    workspace: object, params: Mapping[str, object]
) -> tuple[Path, bool, Path | None]:
    raw: object = params.get("cwd") if params else None
    if raw is not None and not isinstance(raw, str):
        raise InvalidParamsError(f"Invalid cwd: expected a string, got {type(raw).__name__}")
    if isinstance(raw, str) and "\x00" in raw:
        raise InvalidParamsError(f"Invalid cwd: must not contain an embedded NUL: {raw!r}")
    return resolve_git_cwd(
        workspace_root=_workspace_root(workspace),
        requested_cwd=raw if isinstance(raw, str) else None,
    )


def _outside_workspace_warning_text(
    workspace: object, resolved_path: Path, *, top_level: Path | None = None
) -> str:
    root = _workspace_root(workspace).resolve()
    message = (
        f"WARNING: git cwd is outside the workspace: resolved={resolved_path} workspace_root={root}"
    )
    if top_level is not None:
        message += f" top_level={top_level}"
    return message


def run_git_command(
    workspace: object,
    args: list[str],
    *,
    runner: GitRunner | None = None,
    cwd_provider: CwdProvider = Path.cwd,
    cwd: Path | None = None,
) -> str:
    git_runner = runner or _run_git_subprocess
    effective_cwd = (
        cwd if cwd is not None else _workspace_root(workspace, cwd_provider=cwd_provider)
    )
    try:
        output = git_runner(["git", *args], effective_cwd)
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
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    git_runner = runner or _run_git_subprocess
    effective_cwd = (
        cwd if cwd is not None else _workspace_root(workspace, cwd_provider=cwd_provider)
    )
    try:
        output = git_runner(["git", *args], effective_cwd)
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
    return f"{_decode_output(result.stdout)}{_decode_output(result.stderr)}"


def _parse_numstat_count(value: str) -> int:
    if value == "-":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


GIT_READ_TIMEOUT_SECONDS = 30.0

_NO_EXTERNAL_DIFF_HELPER_ARGS: tuple[str, ...] = ("--no-ext-diff", "--no-textconv")

_PIPE_READ_CHUNK_BYTES = 8192


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
    stdout, stderr = proc.communicate_and_cleanup(timeout=GIT_READ_TIMEOUT_SECONDS)
    returncode = proc.returncode if proc.returncode is not None else 0
    return subprocess.CompletedProcess(command, returncode, stdout or b"", stderr or b"")


def _git_read_result_with_outside_warning(
    produce: Callable[[], str], *, is_outside: bool, warning_text: str
) -> ToolResult:
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
    if is_outside:
        return ToolResult(
            content=[ToolContent.text_content(warning_text), ToolContent.text_content(output)],
            is_error=False,
        )
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def _git_read_result(produce: Callable[[], str]) -> ToolResult:
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
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git status")
    resolved_cwd, is_outside, top_level = _resolve_git_cwd(workspace, params)
    warning_text = _outside_workspace_warning_text(workspace, resolved_cwd, top_level=top_level)
    format_value = params.get("format", "raw") if params else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "compact"}:
        raise InvalidParamsError(f"Invalid format: {format_value!r}; expected 'raw' or 'compact'")
    if format_value == "raw":
        return _git_read_result_with_outside_warning(
            lambda: run_git_command(workspace, ["status"], cwd=resolved_cwd),
            is_outside=is_outside,
            warning_text=warning_text,
        )
    return _git_read_result_with_outside_warning(
        lambda: _build_compact_status_payload(workspace, session=session, cwd=resolved_cwd),
        is_outside=is_outside,
        warning_text=warning_text,
    )


def _resolve_compact_status_index(
    workspace: object,
    session: object | None = None,
) -> tuple[ExploreIndexLike, ExploreStoreLike] | None:
    handle: ExploreIndexLike | None = None
    workspace_session: object = (
        cast("object", getattr(workspace, "session", None)) if workspace is not None else None
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
    default = 50_000
    minimum = 1
    if "max_bytes" not in params:
        return default
    raw: object = params["max_bytes"]
    if isinstance(raw, bool):
        raise InvalidParamsError(
            f"max_bytes must be an integer in [{minimum}, {default}]; got bool {raw!r}"
        )
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError as exc:
            raise InvalidParamsError(
                f"max_bytes must be an integer in [{minimum}, {default}]; got {raw!r}"
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
            f"max_bytes must be an integer in [{minimum}, {default}]; got {type(raw).__name__}"
        )
    if value < minimum or value > default:
        raise InvalidParamsError(
            f"max_bytes must be an integer in [{minimum}, {default}]; got {value}"
        )
    return value


def _build_compact_status_payload(
    workspace: object,
    session: object | None = None,
    cwd: Path | None = None,
) -> str:
    raw_result = run_git_command_lenient(workspace, ["status", "--porcelain"], cwd=cwd)
    lines = raw_result.stdout.decode("utf-8", errors="replace").splitlines()
    cards: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
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
    payload.update(_compact_status_index_meta(workspace, ranked_cards, session=session))
    return json.dumps(payload)


def _rank_compact_status_cards(
    cards: list[dict[str, object]],
) -> list[dict[str, object]]:
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
        if card.get("untracked") is True and role != "untracked":
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

    def _rank_key(c: dict[str, object]) -> tuple[int, str]:
        score_value = c.get("score")
        score_int: int = score_value if isinstance(score_value, int) else 0
        path_value = c.get("path")
        path_str: str = path_value if isinstance(path_value, str) else ""
        return (-score_int, path_str)

    ranked.sort(key=_rank_key)
    return ranked


def _is_generated_or_vendor_path(path: str) -> bool:
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
    require_capability(session, GIT_DIFF_READ_CAPABILITY, "Git diff")
    resolved_cwd, is_outside, top_level = _resolve_git_cwd(workspace, params)
    warning_text = _outside_workspace_warning_text(workspace, resolved_cwd, top_level=top_level)
    parsed = parse_git_diff_params(params)
    format_value = params.get("format", "raw") if params else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "summary"}:
        raise InvalidParamsError(f"Invalid format: {format_value!r}; expected 'raw' or 'summary'")
    if format_value == "raw":
        return _git_read_result_with_outside_warning(
            lambda: lenient_stdout(
                run_git_command_lenient(
                    workspace,
                    ["diff", *_NO_EXTERNAL_DIFF_HELPER_ARGS, *parsed.args],
                    cwd=resolved_cwd,
                )
            ),
            is_outside=is_outside,
            warning_text=warning_text,
        )
    max_bytes = _strict_max_bytes_param(params)
    return _git_read_result_with_outside_warning(
        lambda: _build_diff_summary_payload(workspace, parsed.args, max_bytes, cwd=resolved_cwd),
        is_outside=is_outside,
        warning_text=warning_text,
    )


def _build_diff_summary_payload(
    workspace: object,
    git_args: Sequence[str],
    max_bytes: int,
    cwd: Path | None = None,
) -> str:
    raw_result = run_git_command_lenient(
        workspace, ["diff", "--numstat", *_NO_EXTERNAL_DIFF_HELPER_ARGS, *git_args], cwd=cwd
    )
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
    full_text, truncated = _collect_git_diff_capped(workspace, git_args, max_bytes, cwd=cwd)
    added_total = sum(c["added"] for c in cards if isinstance(c["added"], int))
    removed_total = sum(c["removed"] for c in cards if isinstance(c["removed"], int))
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
    cwd: Path | None = None,
) -> tuple[str, bool]:
    if max_bytes <= 0:
        return ("", False)
    effective_cwd = cwd if cwd is not None else _workspace_root(workspace)
    timeout_seconds = GIT_READ_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    proc = cast(
        "_SpawnedProcessLike",
        get_process_manager().spawn(
            ["git", "diff", *_NO_EXTERNAL_DIFF_HELPER_ARGS, *git_args],
            SpawnOptions(
                cwd=str(effective_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                label="git-mcp-read",
            ),
        ),
    )
    stdout = proc.stdout
    if stdout is None:
        return ("", False)
    stderr_drain = _start_pipe_drain(proc.stderr)
    outcome: list[tuple[list[bytes], bool]] = []
    failure: list[Exception] = []

    def _read_stdout() -> None:
        try:
            outcome.append(
                _read_git_diff_until_cap(
                    cast("_ReadablePipe", stdout),
                    max_bytes,
                )
            )
        except Exception as exc:
            failure.append(exc)

    reader = threading.Thread(target=_read_stdout, name="git-diff-stdout", daemon=True)
    reader.start()
    reader.join(timeout=timeout_seconds)
    if reader.is_alive():
        _kill_git_diff_proc(proc, issuer=ProcessTerminationIssuer.GIT_TIMEOUT)
        raise ExecutionError(
            f"git command timed out after {timeout_seconds:g}s: diff {' '.join(git_args)}".rstrip(),
            timed_out=True,
        )
    if failure:
        _kill_git_diff_proc(proc, issuer=ProcessTerminationIssuer.GIT_TIMEOUT)
        raise ExecutionError(f"Failed to read git diff output: {failure[0]}") from failure[0]
    chunks, truncated = outcome[0]
    if truncated:
        _kill_git_diff_proc(proc, issuer=ProcessTerminationIssuer.OUTPUT_LIMIT)
    if stderr_drain is not None:
        stderr_drain.join(timeout=max(deadline - time.monotonic(), 0.0))
    _terminate_git_diff_proc(proc, timeout=max(deadline - time.monotonic(), 0.1))
    return (
        _decode_git_diff_capped(b"".join(chunks), truncated),
        truncated,
    )


def _start_pipe_drain(pipe: object) -> threading.Thread | None:
    if pipe is None:
        return None
    readable = cast("_ReadablePipe", pipe)

    def _drain() -> None:
        with contextlib.suppress(Exception):
            while readable.read(_PIPE_READ_CHUNK_BYTES):
                pass

    drain = threading.Thread(target=_drain, name="git-diff-stderr", daemon=True)
    drain.start()
    return drain


def _read_git_diff_until_cap(
    stdout: _ReadablePipe,
    max_bytes: int,
) -> tuple[list[bytes], bool]:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stdout.read(_PIPE_READ_CHUNK_BYTES)
        if not chunk:
            break
        if total + len(chunk) > max_bytes:
            needed = max_bytes - total
            if needed > 0:
                chunks.append(chunk[:needed])
                total += needed
            return chunks, True
        chunks.append(chunk)
        total += len(chunk)
    return chunks, False


def _kill_git_diff_proc(
    proc: _SpawnedProcessLike,
    *,
    issuer: ProcessTerminationIssuer,
) -> None:
    with contextlib.suppress(Exception):
        proc.kill(issuer=issuer)


def _terminate_git_diff_proc(proc: _SpawnedProcessLike, *, timeout: float) -> None:
    with contextlib.suppress(Exception):
        proc.communicate_and_cleanup(timeout=timeout)


def _decode_git_diff_capped(encoded: bytes, truncated: bool) -> str:
    if truncated and len(encoded) > 0:
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
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git log")
    resolved_cwd, is_outside, top_level = _resolve_git_cwd(workspace, params)
    warning_text = _outside_workspace_warning_text(workspace, resolved_cwd, top_level=top_level)
    parsed = parse_git_log_params(params)
    if parsed.format == "raw":
        return _git_read_result_with_outside_warning(
            lambda: run_git_command(
                workspace, ["log", f"-{parsed.count}", "--oneline"], cwd=resolved_cwd
            ),
            is_outside=is_outside,
            warning_text=warning_text,
        )
    return _git_read_result_with_outside_warning(
        lambda: _build_git_log_summary_payload(workspace, parsed.count, cwd=resolved_cwd),
        is_outside=is_outside,
        warning_text=warning_text,
    )


def _build_git_log_summary_payload(workspace: object, count: int, cwd: Path | None = None) -> str:
    raw = run_git_command(workspace, ["log", f"-{count}", "--oneline"], cwd=cwd)
    raw_bytes = raw.encode("utf-8")
    commits: list[dict[str, object]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        short_sha = parts[0] if parts else ""
        subject = parts[1] if len(parts) > 1 else ""
        commits.append({"short_sha": short_sha, "sha": short_sha, "subject": subject})
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
    require_capability(session, GIT_STATUS_READ_CAPABILITY, "Git show")
    resolved_cwd, is_outside, top_level = _resolve_git_cwd(workspace, params)
    warning_text = _outside_workspace_warning_text(workspace, resolved_cwd, top_level=top_level)
    parsed = parse_git_show_params(params)
    if parsed.format == "raw":
        return _git_read_result_with_outside_warning(
            lambda: run_git_command(workspace, ["show", parsed.git_ref], cwd=resolved_cwd),
            is_outside=is_outside,
            warning_text=warning_text,
        )
    return _git_read_result_with_outside_warning(
        lambda: _build_git_show_summary_payload(workspace, parsed.git_ref, cwd=resolved_cwd),
        is_outside=is_outside,
        warning_text=warning_text,
    )


def _build_git_show_summary_payload(workspace: object, ref: str, cwd: Path | None = None) -> str:
    fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%P%x1f%D%x1f%H"
    raw = run_git_command(workspace, ["show", "--no-patch", f"--format={fmt}", ref], cwd=cwd)
    raw_bytes = raw.encode("utf-8")
    fields = raw.split("\x1f")
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
    author_name = fields[author_name_idx].strip() if len(fields) > author_name_idx else ""
    author_email = fields[author_email_idx].strip() if len(fields) > author_email_idx else ""
    author_date = fields[author_date_idx].strip() if len(fields) > author_date_idx else ""
    subject = fields[subject_idx].strip() if len(fields) > subject_idx else ""
    parents_raw = fields[parents_raw_idx].strip() if len(fields) > parents_raw_idx else ""
    decoration = fields[decoration_idx].strip() if len(fields) > decoration_idx else ""
    parents = [p for p in parents_raw.split() if p] if parents_raw else []
    kind = "commit"
    if "tag:" in decoration:
        kind = "tag"
    elif not parents and sha:
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
    "GIT_READ_TIMEOUT_SECONDS",
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
