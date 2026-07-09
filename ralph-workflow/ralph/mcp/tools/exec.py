"""MCP exec tool handler.

Executes bounded subprocesses directly in the workspace after capability checks
and blacklist policy filtering.

Exported surface:

- ``handle_exec_command`` — the public MCP tool handler. Validates the
  ``ProcessExecBounded`` capability on the session, parses and policy-checks
  the command, runs the bounded subprocess, and returns the result or a
  timeout-shaped error.
- ``parse_exec_params`` / ``run_command`` / ``apply_exec_policy`` —
  parameter parsing, subprocess execution, and blacklist enforcement helpers
  (exposed for tests; the public tool contract is the handler above).
- ``ExecParams`` / ``ExecRunDeps`` / ``ExecutionError`` — typed parameter
  bundle, dependency-injection bundle, and the typed error raised on
  timeout / launch failure.
- ``check_command`` / ``format_exec_result`` / ``resolve_spill_dir`` —
  lower-level helpers used by the handler.
- ``PROCESS_EXEC_BOUNDED_CAPABILITY`` / ``DEFAULT_TIMEOUT_MS`` — the
  capability string and the per-call default timeout (90 000 ms; the
  hard cap is ``EXEC_MAX_TIMEOUT_MS`` in ``ralph.timeout_defaults``).

Trust boundary: this tool is the only public path that lets a hosted
agent spawn an arbitrary subprocess. It enforces:

- A mandatory capability check (default-deny if the session does not
  declare ``ProcessExecBounded``).
- A static blacklist covering privilege escalation (``sudo``, ``su``,
  ``doas``, ``pkexec``, ``runuser``), destructive system commands
  (``shutdown``, ``reboot``, ``halt``, ``poweroff``, ``killall``), network
  tunnel and remote-network tools (``nc``, ``ncat``, ``netcat``,
  ``socat``, ``ssh``, ``scp``, ``rsync``), and container / namespace
  escapes (``docker``, ``podman``, ``chroot``, ``nsenter``, ``unshare``).
- A bounded per-call timeout (``timeout_ms`` capped at
  ``EXEC_MAX_TIMEOUT_MS``); a non-positive or missing value is clamped
  to the default so a direct caller can never produce an unbounded
  blocking call.
- A bounded output spill (anything above ``SPILL_OUTPUT_LIMIT_BYTES``
  is written to ``.agent/tmp/`` rather than returned to the model).

Side effects: spawns a subprocess under ``ralph.process.manager``
(registered with the global ``ProcessManager``), executes it in the
workspace root, captures stdout/stderr, and may write a spill file to
``<workspace>/.agent/tmp/`` when the output exceeds the spill limit.
The subprocess is killed on timeout. The capability check is the trust
boundary — everything else is a hard-coded defence-in-depth layer.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_output_spill import SPILL_OUTPUT_LIMIT_BYTES, format_or_spill
from ralph.mcp.tools._exec_params import ExecParams
from ralph.mcp.tools._exec_run_deps import CwdProvider, ExecRunDeps, OutputChunkCallback
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ProcessManager

PROCESS_EXEC_BOUNDED_CAPABILITY = "ProcessExecBounded"
# Default per-call exec timeout. Single source of truth lives in
# ``ralph.timeout_defaults`` so the advertised tool-schema default (see
# ``_specs_git_exec``) cannot drift from the handler's actual behavior. Set above
# the 60s combined verify budget so an agent running `make verify`/`make test`
# (or a slow git op) through exec does not time out on every call. Per-call
# `timeout_ms` overrides this; the process tree is still killed on expiry.
DEFAULT_TIMEOUT_MS = EXEC_DEFAULT_TIMEOUT_MS
_TIMEOUT_NOTE_THRESHOLD_MS = 60_000
_KILL_SIGNAL_ARG_COUNT = 2
_ARCHIVE_EXTENSIONS = (".tar", ".zip", ".gz", ".bz2", ".xz")
_ARCHIVE_EXTRACT_FLAGS = ("-x", "--extract", "-d", "--delete")
_EXEC_USAGE_EXAMPLES = (
    'Examples: {"command": "python -m pytest"}, '
    '{"command": ["python", "-m", "pytest"]}, '
    '{"argv": ["python", "-m", "pytest"]}.'
)

_BLACKLIST_DESCRIPTIONS = {
    "privilege_escalation": "privilege escalation",
    "destructive_system": "destructive system operation",
    "network_exfiltration": "network/exfiltration",
    "container_escape": "container/VM escape",
    "multi_file_operation": "multi-file operation",
}

_SHELL_OPERATOR_CHARS = frozenset("|&;<>")

_PRIVILEGE_ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runuser"}
_DESTRUCTIVE_SYSTEM_COMMANDS = {"shutdown", "reboot", "halt", "poweroff", "killall"}
_NETWORK_TUNNEL_COMMANDS = {"nc", "ncat", "netcat", "socat"}
_REMOTE_NETWORK_COMMANDS = {"ssh", "scp", "rsync"}
_CONTAINER_COMMANDS = {"docker", "podman", "chroot", "nsenter", "unshare"}


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def parse_exec_params(params: Mapping[str, object]) -> ExecParams:
    """Parse and validate exec tool parameters."""
    command_tokens = _parse_exec_command_tokens(params)
    args = _parse_exec_args(params.get("args"))
    command = command_tokens[0] if command_tokens else ""
    merged_args = [*command_tokens[1:], *args]

    # Require a strictly positive timeout: timeout_ms<=0 (or non-int) falls back to
    # the default. Zero must NOT mean "unbounded" — that would make exec a blocking-
    # forever call on the MCP server thread, an agent-controllable hang vector.
    timeout_value = params.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value > 0
        else DEFAULT_TIMEOUT_MS
    )
    # Cap the per-call override: the MCP client request timeout is derived to exceed
    # EXEC_MAX_TIMEOUT_MS, so a tool call can never outrun the client and re-trigger
    # the -32001 "Request timed out" storm.
    timeout_ms = min(timeout_ms, EXEC_MAX_TIMEOUT_MS)

    return ExecParams(command=command, args=merged_args, timeout_ms=timeout_ms)


def _has_shell_operator_tokens(tokens: list[str]) -> bool:
    return any(token and all(c in _SHELL_OPERATOR_CHARS for c in token) for token in tokens)


def _parse_exec_command_tokens(params: Mapping[str, object]) -> list[str]:
    command_value = params.get("command")
    if isinstance(command_value, str):
        tokens = _parse_shell_words(command_value, field_name="command")
        if _has_shell_operator_tokens(tokens):
            return ["sh", "-c", command_value.strip()]
        return tokens
    if isinstance(command_value, list):
        return _coerce_argv_tokens(command_value, field_name="command")
    if command_value is not None:
        raise InvalidParamsError(
            "'command' must be a string or string array. " + _EXEC_USAGE_EXAMPLES
        )

    argv_value = params.get("argv")
    if isinstance(argv_value, str):
        return _parse_shell_words(argv_value, field_name="argv")
    if isinstance(argv_value, list):
        return _coerce_argv_tokens(argv_value, field_name="argv")
    if argv_value is not None:
        raise InvalidParamsError("'argv' must be a string or string array. " + _EXEC_USAGE_EXAMPLES)

    raise InvalidParamsError("Missing 'command' or 'argv' parameter. " + _EXEC_USAGE_EXAMPLES)


def _parse_exec_args(args_value: object) -> list[str]:
    if isinstance(args_value, list):
        return [value for value in args_value if isinstance(value, str)]
    if isinstance(args_value, str):
        return _parse_shell_words(args_value, field_name="args")
    return []


def _coerce_argv_tokens(values: list[object], *, field_name: str) -> list[str]:
    tokens = [value for value in values if isinstance(value, str)]
    if not tokens:
        raise InvalidParamsError(f"{field_name} must include at least one string token")
    return tokens


def _parse_shell_words(value: str, *, field_name: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []

    try:
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise InvalidParamsError(f"Malformed {field_name} value: {exc}") from exc

    return tokens


def check_command(command: str, args: list[str]) -> str | None:
    """Return a denial reason when a command matches the blacklist policy."""
    cmd = command.strip()
    if not cmd:
        return None

    for checker in (
        check_privilege_escalation,
        check_destructive_system,
        check_network_exfiltration,
        check_container_escape,
        check_multi_file_operation,
    ):
        reason = checker(cmd, args)
        if reason:
            return reason
    return None


def _description(key: str) -> str:
    return _BLACKLIST_DESCRIPTIONS.get(key, "operation")


def _command_key(command: str) -> str:
    return command.strip().lower()


def _lower_args(args: list[str]) -> list[str]:
    return [arg.lower() for arg in args]


def _contains_any(arg_list: list[str], targets: set[str]) -> bool:
    return any(arg in targets for arg in arg_list)


def check_privilege_escalation(command: str, _args: list[str]) -> str | None:
    """Return a denial reason if the command is a privilege escalation tool."""
    key = _command_key(command)
    if key in _PRIVILEGE_ESCALATION_COMMANDS:
        desc = _description("privilege_escalation")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_destructive_system(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command is a destructive system operation."""
    key = _command_key(command)
    args_lower = _lower_args(args)
    desc = _description("destructive_system")

    if _is_destructive_rm(key, args, args_lower):
        return f"Command 'rm' with recursive force flag targeting root/home is blacklisted: {desc}"

    if key in {"mkfs", "dd"} and any(
        arg.startswith("/dev/") or "of=/dev/" in arg for arg in args_lower
    ):
        return f"Command '{command}' targeting devices is blacklisted: {desc}"

    if key in _DESTRUCTIVE_SYSTEM_COMMANDS:
        return f"Command '{command}' is blacklisted: {desc} is not allowed"

    if _is_init_kill(key, args_lower):
        return f"Command 'kill -9 1' (init) is blacklisted: {desc} is not allowed"

    return None


def _is_destructive_rm(key: str, args: list[str], args_lower: list[str]) -> bool:
    return (
        key == "rm"
        and _contains_any(args_lower, {"-rf", "-r", "-f"})
        and any(
            target == "/"
            or target.startswith("/.")
            or target.startswith("~")
            or target.startswith("/home")
            for target in args
        )
    )


def _is_init_kill(key: str, args_lower: list[str]) -> bool:
    return (
        key == "kill"
        and len(args_lower) >= _KILL_SIGNAL_ARG_COUNT
        and args_lower[0] == "-9"
        and args_lower[1] == "1"
    )


def check_network_exfiltration(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command could exfiltrate data over the network."""
    key = _command_key(command)
    args_lower = _lower_args(args)

    if key in {"curl", "wget"}:
        if any(_is_external_url(arg) for arg in args):
            desc = _description("network_exfiltration")
            return (
                f"Command '{command}' to external URLs is blacklisted: {desc} risk. "
                "Use Ralph's HTTP capabilities instead."
            )
        return None

    if key in _NETWORK_TUNNEL_COMMANDS:
        desc = _description("network_exfiltration")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"

    if key in _REMOTE_NETWORK_COMMANDS:
        joined = " ".join(args_lower)
        if "@" in joined or ":/" in joined or "::" in joined:
            desc = _description("network_exfiltration")
            return f"Command '{command}' to remote hosts is blacklisted: {desc} is not allowed"
    return None


def _is_external_url(arg: str) -> bool:
    token = arg.strip()
    if not token or token.startswith("-"):
        return False
    lower = token.lower()
    if "localhost" in lower or "127.0.0.1" in lower:
        return False
    return lower.startswith("http://") or lower.startswith("https://") or "://" in lower


def check_container_escape(command: str, _args: list[str]) -> str | None:
    """Return a denial reason if the command could escape container isolation."""
    key = _command_key(command)
    if key in _CONTAINER_COMMANDS:
        desc = _description("container_escape")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_multi_file_operation(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command performs bulk file operations."""
    key = _command_key(command)
    args_lower = _lower_args(args)
    desc = _description("multi_file_operation")

    checks = (
        (
            key == "find" and any(flag in args_lower for flag in ("-exec", "-delete")),
            "Command 'find' with -exec/-delete is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            key == "xargs"
            and any(flag in args_lower for flag in ("rm", "mv", "cp", "chmod", "chown")),
            "Command 'xargs' with destructive commands is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            key == "sed" and "-i" in args_lower,
            f"Command 'sed -i' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key == "awk" and ("-i" in args_lower or "-inplace" in args_lower),
            f"Command 'awk -i' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key in {"rename", "mmv"},
            f"Command '{command}' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key in {"chmod", "chown"} and any(flag in args_lower for flag in ("-r", "-R")),
            "Command '"
            f"{command} -R' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            _has_recursive_glob_copy(key, args, args_lower),
            "Command '"
            f"{command}' with recursive glob is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            _extracts_archive_in_place(key, args_lower),
            "Command '"
            f"{command}' extracting archives in-place is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
    )
    for applies, message in checks:
        if applies:
            return message
    return None


def _has_recursive_glob_copy(key: str, args: list[str], args_lower: list[str]) -> bool:
    if key not in {"cp", "mv"}:
        return False
    has_glob = any("*" in arg or "?" in arg for arg in args)
    has_recursive = any(flag in args_lower for flag in ("-r", "-rf", "-R", "-f"))
    return has_glob and has_recursive


def _extracts_archive_in_place(key: str, args_lower: list[str]) -> bool:
    if key not in {"tar", "zip", "unzip"}:
        return False
    has_extract_flag = any(
        any(flag in arg for flag in _ARCHIVE_EXTRACT_FLAGS) for arg in args_lower
    )
    has_archive = any(arg.endswith(ext) for arg in args_lower for ext in _ARCHIVE_EXTENSIONS)
    return has_extract_flag and has_archive


def apply_exec_policy(command: str, args: list[str]) -> None:
    """Apply command policy and raise if the command is denied."""
    reason = check_command(command, args)
    if reason is None:
        return
    raise CapabilityDeniedError(f"Command '{command}' denied by policy: {reason}")


def _workspace_root(workspace: object, *, cwd_provider: CwdProvider = Path.cwd) -> Path:
    if isinstance(workspace, Path):
        return workspace
    if isinstance(workspace, str):
        return Path(workspace)

    root_value: object | None = getattr(workspace, "root", None)
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def resolve_spill_dir(workspace: object, deps: ExecRunDeps | None) -> Path:
    """Resolve where oversized exec output spills, INSIDE the workspace by default.

    The agent reads spill files through the workspace-scoped read/exec tools,
    which reject any path resolving outside the workspace root. Spilling to the
    OS temp dir produces a path the agent is told to read but cannot reach — it
    goes blind on exactly the large outputs (a full pytest run) where the failing
    summary lives, and loops re-running the command until the watchdog kills it.
    Default to ``<workspace>/.agent/tmp`` (Ralph's own readable scratch dir); an
    explicitly injected ``deps.spill_dir`` (tests, custom deployments) wins.
    """
    if deps is not None and deps.spill_dir is not None:
        return deps.spill_dir
    return _workspace_root(workspace) / ".agent" / "tmp"


def _child_env(cwd: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PWD"] = str(cwd)
    env.pop("OLDPWD", None)
    return env


def run_command(
    command: str,
    args: list[str],
    workspace: object,
    timeout_ms: int,
    deps: ExecRunDeps | None = None,
) -> _CompletedProcessAdapter:
    """Execute a subprocess directly in the workspace root after blacklist checks."""
    resolved_deps = deps or ExecRunDeps()
    cwd_provider = resolved_deps.cwd_provider or Path.cwd
    cwd = _workspace_root(workspace, cwd_provider=cwd_provider)
    # Defense in depth: never produce an unbounded (None) timeout. A non-positive
    # timeout_ms is clamped to the default so a direct caller cannot create a
    # blocking-forever subprocess on the MCP server thread.
    effective_timeout_ms = timeout_ms if timeout_ms > 0 else DEFAULT_TIMEOUT_MS
    timeout_seconds = effective_timeout_ms / 1000

    try:
        if resolved_deps.runner is not None:
            return resolved_deps.runner([command, *args], cwd, timeout_seconds)
        return _run_subprocess(
            [command, *args],
            cwd,
            timeout_seconds,
            resolved_deps.process_manager,
            on_output_chunk=resolved_deps.on_output_chunk,
        )
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        # Suggest a larger timeout but never above the cap (the MCP client request
        # timeout is derived to exceed EXEC_MAX_TIMEOUT_MS; suggesting more would
        # let the next call outrun the client and re-trigger -32001).
        suggested = min(timeout_ms * 2, EXEC_MAX_TIMEOUT_MS) if timeout_ms > 0 else None
        raise ExecutionError(
            f"Failed to execute '{command}': timed out after {timeout_ms}ms",
            timed_out=True,
            timeout_ms=timeout_ms,
            suggested_timeout_ms=suggested,
        ) from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc


def _run_subprocess(
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    pm: ProcessManager | None = None,
    on_output_chunk: Callable[[str], None] | None = None,
) -> _CompletedProcessAdapter:
    effective_pm = pm if pm is not None else get_process_manager()
    handle = effective_pm.spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            env=_child_env(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label=f"mcp-exec:{command[0]}",
        ),
    )
    stdout: bytes | None = b""
    stderr: bytes | None = b""
    chunk_callback: Callable[[bytes], None] | None = None
    if on_output_chunk is not None:
        _str_callback = on_output_chunk

        def chunk_callback(raw: bytes) -> None:
            _str_callback(raw.decode("utf-8", errors="replace"))

    try:
        stdout, stderr = handle.communicate_and_cleanup(
            timeout=timeout_seconds,
            output_limit_bytes=SPILL_OUTPUT_LIMIT_BYTES,
            on_output_chunk=chunk_callback,
        )
    except subprocess.TimeoutExpired:
        handle.terminate(grace_period_s=0)
        raise
    except ManagedProcessOutputLimitExceededError as exc:
        # Don't discard the output: return the captured tail flagged as truncated
        # so the caller can spill it to a file instead of forcing a blind retry.
        return _CompletedProcessAdapter(
            stdout=exc.stdout,
            stderr=exc.stderr,
            returncode=handle.returncode if handle.returncode is not None else -1,
            truncated=True,
        )
    finally:
        effective_pm.cleanup_orphans(handle)
    return _CompletedProcessAdapter(
        stdout=stdout or b"",
        stderr=stderr or b"",
        returncode=handle.returncode or 0,
    )


def _format_exec_error(exc: Exception) -> str:
    """Format an exec error into a self-explanatory agent-actionable message.

    Delegates to ``__str__`` for ``ExecutionError`` (which uses structured
    templates), and wraps generic exceptions in a minimal format.
    """
    if isinstance(exc, ExecutionError):
        return str(exc)
    return f"Error: {exc}"


def format_exec_result(
    command: str,
    args: list[str],
    output: _CompletedProcessAdapter,
    timeout_ms: int,
) -> str:
    """Format subprocess output to match the Rust tool response."""
    stdout = output.stdout.decode("utf-8", errors="replace")
    stderr = output.stderr.decode("utf-8", errors="replace")
    exit_code = output.returncode
    text = (
        f"Command: {command} {args!r}\n"
        f"Exit code: {exit_code}\n\n"
        f"Stdout:\n{stdout}\n\n"
        f"Stderr:\n{stderr}"
    )
    if 0 < timeout_ms < _TIMEOUT_NOTE_THRESHOLD_MS:
        text = f"{text}\n\nNote: This command had a {timeout_ms}ms timeout"
    return text


@runtime_checkable
class _SessionWithStreaming(Protocol):
    """Subset of AgentSession that supports thread-owned tool output streaming."""

    def current_thread_tool_output_sink(
        self,
    ) -> Callable[[dict[str, object]], None] | None:
        """Return the active sink when the calling thread owns it."""
        ...


def _build_effective_deps(
    session: CoordinationSessionLike,
    deps: ExecRunDeps | None,
) -> ExecRunDeps | None:
    """Compose the session's thread-owned output sink into deps.on_output_chunk."""
    if not isinstance(session, _SessionWithStreaming):
        return deps
    # Capture the sink ONCE, on the dispatching thread. The session is shared
    # across concurrent request threads; resolving the sink at chunk time (from
    # subprocess reader threads) would route this exec's output to whichever
    # request swapped the shared sink last — cross-connection output cross-talk.
    sink = session.current_thread_tool_output_sink()
    if sink is None:
        return deps
    captured_sink = sink

    def _session_chunk(chunk: str) -> None:
        captured_sink({"tool": "exec", "stream": "combined", "text": chunk})

    if deps is None:
        return ExecRunDeps(on_output_chunk=_session_chunk)

    existing_cb = deps.on_output_chunk
    if existing_cb is None:
        composed_cb: OutputChunkCallback = _session_chunk
    else:

        def composed_cb(chunk: str) -> None:
            existing_cb(chunk)
            _session_chunk(chunk)

    return ExecRunDeps(
        runner=deps.runner,
        cwd_provider=deps.cwd_provider,
        process_manager=deps.process_manager,
        on_output_chunk=composed_cb,
        spill_dir=deps.spill_dir,
    )


def handle_exec_command(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    deps: ExecRunDeps | None = None,
) -> ToolResult:
    """Execute a bounded subprocess in the workspace after blacklist checks.

    Public MCP tool handler. Validates the ``ProcessExecBounded`` capability
    on the session, parses and policy-checks the command, runs the bounded
    subprocess under the workspace root, and returns the formatted result
    or a timeout-shaped error.

    Args:
        session: Agent session carrying the capability set, run id, and
            chunk callback used to compose output for live streaming.
        workspace: Workspace surface whose ``workspace_root`` is the cwd
            for the spawned subprocess. ``Path``-like is required.
        params: Mapping with ``command`` (string) and optional ``args``
            (list of strings), ``timeout_ms`` (int, bounded by
            ``EXEC_MAX_TIMEOUT_MS``).
        deps: Optional dependency-injection bundle (custom ``runner``,
            ``cwd_provider``, ``process_manager``, ``on_output_chunk``,
            ``spill_dir``). When ``None``, ``DEFAULT_EXEC_RUN_DEPS`` is
            used.

    Returns:
        A ``ToolResult`` whose text content is the formatted command
        output (``returncode`` + stdout/stderr). Output above
        ``SPILL_OUTPUT_LIMIT_BYTES`` is written to
        ``<workspace>/.agent/tmp/`` instead of returned to the model.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``ProcessExecBounded``. The handler enforces default-deny.
        InvalidParamsError: When ``params`` fails the ``ExecParams``
            parser (missing ``command``, wrong types, etc.).
        ExecutionError: When the subprocess fails to launch (not on
            non-zero return; non-zero return is preserved as text).

    Side effects:
        Spawns a subprocess registered with the global ``ProcessManager``
        and executes it in the workspace root. Captures stdout/stderr,
        kills the subprocess on timeout, and may write a spill file to
        ``<workspace>/.agent/tmp/`` when the output exceeds the spill
        limit. A timeout is converted into an actionable, non-retryable
        ``is_error`` ``ToolResult`` (not a -32603 protocol error).
    """
    require_capability(session, PROCESS_EXEC_BOUNDED_CAPABILITY, "Command execution")
    parsed = parse_exec_params(params)
    apply_exec_policy(parsed.command, parsed.args)
    effective_deps = _build_effective_deps(session, deps)
    # AC-11: ``format=summary`` requests the bounded JSON envelope with
    # replayable resource handles; the default preserves the legacy
    # text/head-tail shape.
    format_value = params.get("format", "raw") if isinstance(params, Mapping) else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "summary"}:
        raise InvalidParamsError(
            f"Invalid format: {format_value!r}; expected 'raw' or 'summary'"
        )
    summary = format_value == "summary"
    try:
        output = run_command(
            parsed.command, parsed.args, workspace, parsed.timeout_ms, deps=effective_deps
        )
    except ExecutionError as exc:
        if not exc.timed_out:
            raise
        # A timeout EXECUTED but failed: return an actionable, non-retryable
        # is_error result instead of letting it become a -32603 protocol error
        # the agent reads as transient and retries forever.
        return ToolResult(
            content=[ToolContent.text_content(str(exc))],
            is_error=True,
        )
    text = format_exec_result(parsed.command, parsed.args, output, parsed.timeout_ms)
    stdout_text = output.stdout.decode("utf-8", errors="replace")
    stderr_text = output.stderr.decode("utf-8", errors="replace")
    return format_or_spill(
        text,
        returncode=output.returncode,
        truncated=output.truncated,
        spill_dir=resolve_spill_dir(workspace, deps),
        summary=summary,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
    )


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "PROCESS_EXEC_BOUNDED_CAPABILITY",
    "ExecParams",
    "ExecRunDeps",
    "ExecutionError",
    "WorkspaceWithRoot",
    "_format_exec_error",
    "apply_exec_policy",
    "check_command",
    "format_exec_result",
    "handle_exec_command",
    "parse_exec_params",
    "resolve_spill_dir",
    "run_command",
]
