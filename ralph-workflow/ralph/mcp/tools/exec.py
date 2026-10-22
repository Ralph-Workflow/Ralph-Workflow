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

A plain command runs argv-direct (no shell). A compound shell string —
one carrying an unquoted ``| & ; < >`` operator — is run through
``sh -c`` so pipes, redirections, and ``&&``/``;`` sequences work; before
it runs, the blacklist below is enforced against EVERY command in the
pipeline (``echo hi; sudo rm -rf /`` is denied on its ``sudo`` segment).
An argv LIST is always explicit argv and is never shell-interpreted.

Trust boundary: this tool is the only public path that lets a hosted
agent spawn an arbitrary subprocess. It enforces:

- A mandatory capability check (default-deny if the session does not
  declare ``ProcessExecBounded``).
- A static blacklist applied to every shell pipeline segment, including a
  VCS scanner (``_scan_text_for_vcs_violation``) that denies ``hg`` /
  ``svn`` unconditionally and denies ``git`` subcommands in the
  high-risk-operation blacklist (``_GIT_BLOCKED_SUBCOMMANDS``). ``diff``
  output-writing and external-helper flags are denied separately — see
  ``_scan_diff_flags_for_writes``.
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

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.mcp.server.exec_sse_streaming import get_current_progress_subscription
from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_job_tools import get_exec_job_registry
from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobSnapshot,
    serialize_liveness_verdict,
)
from ralph.mcp.tools._exec_output_spill import SPILL_OUTPUT_LIMIT_BYTES, format_or_spill
from ralph.mcp.tools._exec_params import ExecParams
from ralph.mcp.tools._exec_run_deps import CwdProvider, ExecRunDeps, build_effective_exec_deps
from ralph.mcp.tools._exec_safety import (
    _command_key,
    apply_exec_policy,
    check_command,
    check_container_escape,
    check_destructive_system,
    check_multi_file_operation,
    check_network_exfiltration,
    check_privilege_escalation,
)
from ralph.mcp.tools._exec_stringified_argv import decode_stringified_argv
from ralph.mcp.tools._exec_vcs_scanner import (
    _GIT_BLOCKED_SUBCOMMANDS,
    _VCS_COMMANDS,
    _scan_text_for_vcs_violation,
    check_version_control,
    exec_usage_hints,
    find_vcs_usage_in_scripts,
)
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.process._spawn_env import scrub_activity_relay_controls
from ralph.process.manager import DeadlineTimeoutExpired, SpawnOptions, get_process_manager
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.timeout_defaults import (
    EXEC_DEFAULT_TIMEOUT_MS,
    EXEC_JOB_CEILING_MAX_S,
    EXEC_MAX_TIMEOUT_MS,
    EXEC_SOFT_REPLY_BUDGET_MS,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ProcessManager

PROCESS_EXEC_BOUNDED_CAPABILITY = "ProcessExecBounded"
# Default per-call exec timeout. Single source of truth lives in
# ``ralph.timeout_defaults`` so the advertised tool-schema default (see
# ``_specs_git_exec``) cannot drift from the handler's actual behavior. Set above
# the 60s combined verify budget so an agent running `make verify`/`make test`
# through exec does not time out on every call. Per-call
# `timeout_ms` overrides this; the process tree is still killed on expiry.
DEFAULT_TIMEOUT_MS = EXEC_DEFAULT_TIMEOUT_MS
CompletedProcessAdapter = _CompletedProcessAdapter
_TIMEOUT_NOTE_THRESHOLD_MS = 60_000
_TIMEOUT_PARTIAL_OUTPUT_LIMIT_BYTES = 16 * 1024
_EXEC_USAGE_EXAMPLES = (
    'Examples: {"command": "python -m pytest"}, '
    '{"command": ["python", "-m", "pytest"]}, '
    '{"argv": ["python", "-m", "pytest"]}.'
)

_SHELL_OPERATOR_CHARS = frozenset("|&;<>\n")
# A redirection operator (``< > >> <<``) is followed by a filename target, not a
# new command; a command separator (``| ; & && ||``) introduces one. Segmentation
# splits on separators and skips redirection targets so a benign ``echo x >
# reboot`` is not misread as invoking the ``reboot`` command.
_REDIRECTION_CHARS = frozenset("<>")


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def _reject_nul_params(params: Mapping[str, object]) -> None:
    """Reject any string parameter carrying an embedded NUL.

    ``ProcessManager`` strips NULs out of argv (execve cannot carry one),
    so a token accepted here with a NUL inside would reach the child
    WITHOUT it: ``su<NUL>do`` passes every blacklist check in
    ``check_command`` and then executes as ``sudo``. The same holds for a
    compound shell string, whose segments are checked but whose full text
    is what ``sh -c`` runs. Every exec parameter is an argv token, a shell
    string, or a timeout — none of them has a legitimate NUL — so the
    denylist boundary fails closed here instead.
    """
    for name, value in params.items():
        items: list[object] = list(value) if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and "\x00" in item:
                raise InvalidParamsError(f"exec rejects an embedded NUL in {name!r}: {item!r}")


def parse_exec_max_runtime(params: Mapping[str, object]) -> int | None:
    """Return the optional clamped max_runtime_ms parameter."""
    raw = params.get("max_runtime_ms")
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        max_ms = int(raw)
        hard_max_ms = int(EXEC_JOB_CEILING_MAX_S * 1000)
        return min(max_ms, hard_max_ms)
    return None


def parse_exec_params(params: Mapping[str, object]) -> ExecParams:
    """Parse and validate exec tool parameters."""
    params = decode_stringified_argv(params)
    _reject_nul_params(params)
    timeout_ms = parse_exec_timeout(params)
    max_runtime_ms = parse_exec_max_runtime(params)

    # A command/argv STRING carrying an unquoted shell operator is a compound
    # shell command (pipe, redirection, ``&&``/``;`` sequence). Route it through
    # ``sh -c`` so the shell actually interprets it, but first split it into
    # pipeline segments so ``handle_exec_command`` can enforce the blacklist
    # against every command the shell would run — see ``_enforce_exec_policy``.
    shell_source = _shell_command_source(params)
    if shell_source is not None:
        segments = _shell_command_segments(shell_source)
        first = segments[0] if segments else ("", [])
        return ExecParams(
            command=first[0],
            args=first[1],
            timeout_ms=timeout_ms,
            shell_command=shell_source,
            max_runtime_ms=max_runtime_ms,
        )

    command_tokens = _parse_exec_command_tokens(params)
    args = _parse_exec_args(params.get("args"))
    command = command_tokens[0] if command_tokens else ""
    merged_args = [*command_tokens[1:], *args]
    return ExecParams(
        command=command,
        args=merged_args,
        timeout_ms=timeout_ms,
        max_runtime_ms=max_runtime_ms,
    )


def parse_exec_timeout(params: Mapping[str, object]) -> int:
    """Return the bounded exec-family timeout from untrusted tool parameters."""
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
    return min(timeout_ms, EXEC_MAX_TIMEOUT_MS)


def _shell_command_source(params: Mapping[str, object]) -> str | None:
    """Return the raw shell string when the caller passed a compound command.

    Only a ``command`` / ``argv`` STRING with an UNQUOTED shell operator is
    treated as shell: a caller that passes an argv LIST is asking for explicit,
    non-shell argv, so its operator tokens stay literal (no ``sh -c``). Returns
    ``None`` for every non-compound invocation.
    """
    command_value = params.get("command")
    argv_value = params.get("argv")
    if isinstance(command_value, str):
        source = command_value
    elif command_value is None and isinstance(argv_value, str):
        source = argv_value
    else:
        return None
    stripped = source.strip()
    if stripped and (
        _has_unquoted_shell_operator(stripped) or _starts_with_environment_assignment(stripped)
    ):
        explicit_args = _parse_exec_args(params.get("args"))
        return f"{stripped} {shlex.join(explicit_args)}" if explicit_args else stripped
    return None


def _is_environment_assignment(token: str) -> bool:
    name, separator, _value = token.partition("=")
    return bool(separator and name and name.isidentifier())


def _starts_with_environment_assignment(command: str) -> bool:
    tokens = _parse_shell_words(command, field_name="command")
    return bool(tokens and _is_environment_assignment(tokens[0]))


def _has_unquoted_shell_operator(command: str) -> bool:
    """Return True when ``command`` contains an UNQUOTED shell control operator.

    Walks the raw string once, tracking single- and double-quote
    state (and backslash escapes) so a literal ``>`` inside ``'...'``
    or ``"..."`` is not flagged. Quoted literals are valid argv
    content (``printf '>'``, ``grep "a|b"``); an unquoted ``|``,
    ``&``, ``;``, ``<``, or ``>`` is unsafe because the per-token
    blacklist only inspects the top-level command, not embedded
    sub-commands the shell would run. Direct callers needing shell
    composition must use ``unsafe_exec`` / ``raw_exec``, which
    docs declare as the appropriate surface for compound shell
    work.
    """
    in_single = False
    in_double = False
    i = 0
    length = len(command)
    while i < length:
        c = command[i]
        # Backslash escapes the next char in both shell quote modes.
        if c == "\\" and i + 1 < length and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and c in _SHELL_OPERATOR_CHARS:
            return True
        i += 1
    return False


def _parse_exec_command_tokens(params: Mapping[str, object]) -> list[str]:
    # A compound shell STRING is handled earlier (see ``_shell_command_source``);
    # anything reaching here is either a single command string or an explicit
    # argv list. An argv list is never shell-interpreted, so its operator tokens
    # stay literal argv content.
    command_value = params.get("command")
    if isinstance(command_value, str):
        return _parse_shell_words(command_value, field_name="command")
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
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars="|&;<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise InvalidParamsError(f"Malformed {field_name} value: {exc}") from exc

    return tokens


def _is_operator_token(token: str) -> bool:
    return bool(token) and all(char in _SHELL_OPERATOR_CHARS for char in token)


def _shell_command_segments(command: str) -> list[tuple[str, list[str]]]:
    """Split a compound shell string into ``(command, args)`` pipeline segments.

    ``command`` is tokenized with the shell-operator punctuation lexer, so
    operators surface as standalone tokens. Segments break on command separators
    (``|``, ``;``, ``&``, ``&&``, ``||``); redirection operators (``<``, ``>``,
    ``>>``) consume their following token as a filename target rather than
    starting a new command. Each returned segment head is exactly what the shell
    would execute, so ``check_command`` can veto a blacklisted command anywhere
    in the pipeline (``echo hi; sudo rm -rf /`` denies on the ``sudo`` segment).

    Best-effort: shell features that hide a command from a static token walk —
    command substitution ``$(...)``, backticks, ``eval``, ``xargs sh -c`` — are
    not decomposed here. The per-segment blacklist is defense-in-depth, not a
    sandbox; the trust boundary remains the ``ProcessExecBounded`` capability.
    """
    tokens = _parse_shell_words(command, field_name="command")
    segments: list[tuple[str, list[str]]] = []
    current: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if _is_operator_token(token):
            if any(char in _REDIRECTION_CHARS for char in token):
                skip_next = True
                continue
            if current:
                segments.append((current[0], current[1:]))
                current = []
            continue
        if not current and _is_environment_assignment(token):
            continue
        current.append(token)
    if current:
        segments.append((current[0], current[1:]))
    return segments


def _env_wrapped_segment(args: list[str]) -> tuple[str, list[str]] | None:
    expanded = _expand_env_split_options(args)
    if expanded is None:
        return None
    index = 0
    while index < len(expanded):
        token = expanded[index]
        if _is_environment_assignment(token):
            index += 1
            continue
        if token == "--":
            index += 1
            while index < len(expanded) and _is_environment_assignment(expanded[index]):
                index += 1
            break
        if token in {"-u", "--unset", "-C", "--chdir"}:
            if index + 1 >= len(expanded):
                return None
            index += 2
            continue
        if token.startswith(("--unset=", "--chdir=")) or (
            token.startswith(("-u", "-C")) and len(token) > len("-u")
        ):
            index += 1
            continue
        if token in {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"}:
            index += 1
            continue
        if token.startswith("-"):
            return None
        break
    if index >= len(expanded):
        return None
    return expanded[index], expanded[index + 1 :]


def _expand_env_split_options(args: list[str]) -> list[str] | None:
    expanded = list(args)
    index = 0
    while index < len(expanded):
        token = expanded[index]
        consumed = 0
        value: str | None = None
        if token in {"-S", "--split-string"}:
            if index + 1 >= len(expanded):
                return None
            value, consumed = expanded[index + 1], 2
        elif token.startswith("--split-string="):
            value, consumed = token.partition("=")[2], 1
        elif token.startswith("-S") and token != "-S":
            value, consumed = token[2:], 1
        if value is None:
            index += 1
            continue
        if any(operator in value for operator in ("\\", "$")):
            return None
        expanded[index : index + consumed] = _parse_shell_words(
            value, field_name="env --split-string"
        )
    return expanded


def _enforce_exec_policy(parsed: ExecParams, workspace: object) -> str | None:
    """Enforce the blacklist for an exec invocation, shell-aware.

    A compound shell command is checked against every command in the pipeline;
    a plain command is checked directly. Executed scripts are content-scanned;
    blocked operations in their contents produce a warning instead of denial.
    """
    if parsed.shell_command is None:
        segments: list[tuple[str, list[str]]] = [(parsed.command, parsed.args)]
    else:
        segments = _shell_command_segments(parsed.shell_command)
    inspected: list[tuple[str, list[str]]] = []
    for segment_command, segment_args in segments:
        command, args = segment_command, segment_args
        inspected.append((command, args))
        apply_exec_policy(command, args)
        while _command_key(command) == "env" and args:
            wrapped = _env_wrapped_segment(args)
            if wrapped is None:
                raise CapabilityDeniedError(
                    "Command 'env' denied by policy: unsupported wrapper arguments"
                )
            command, args = wrapped
            inspected.append((command, args))
            apply_exec_policy(command, args)
    script_hit = find_vcs_usage_in_scripts(inspected, _workspace_root(workspace))
    if script_hit is None:
        return None
    script, _word = script_hit
    return f"Warning: script '{script}' contains version-control commands"


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
    env = scrub_activity_relay_controls(dict(os.environ))
    env.pop("RALPH_BROKER_SECRET", None)
    env["PWD"] = str(cwd)
    env.pop("OLDPWD", None)
    scrub_activity_relay_controls(env)
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
        timeout_cause = exc.timeout_cause if isinstance(exc, DeadlineTimeoutExpired) else None
        # Suggest a larger timeout but never above the cap (the MCP client request
        # timeout is derived to exceed EXEC_MAX_TIMEOUT_MS; suggesting more would
        # let the next call outrun the client and re-trigger -32001).
        suggested = min(timeout_ms * 2, EXEC_MAX_TIMEOUT_MS) if timeout_ms > 0 else None
        if suggested is not None and suggested <= timeout_ms:
            suggested = None
        deadline_timeout_ms = EXEC_MAX_TIMEOUT_MS if timeout_cause == "hard_cap" else None
        raise ExecutionError(
            f"Failed to execute '{command}': timed out after {deadline_timeout_ms or timeout_ms}ms",
            timed_out=True,
            timeout_ms=timeout_ms,
            deadline_timeout_ms=deadline_timeout_ms,
            suggested_timeout_ms=suggested,
            timeout_cause=timeout_cause,
            partial_output=_timeout_partial_output(exc),
        ) from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc


_DEFAULT_RUN_COMMAND = run_command


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
            # Progress extends the inactivity window, never this absolute cap.
            hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000,
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


def _timeout_partial_output(exc: subprocess.TimeoutExpired) -> str | None:
    """Return a bounded decoded tail captured before a process timeout."""
    parts: list[str] = []
    for label, value in (("Stdout", exc.stdout), ("Stderr", exc.stderr)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        if text:
            parts.append(f"{label}:\n{text[-_TIMEOUT_PARTIAL_OUTPUT_LIMIT_BYTES:]}")
    return "\n\n".join(parts) or None


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
    # Omit the args repr when there are none: the shell-command path passes the
    # full command line as ``command`` with empty ``args``, and a trailing ``[]``
    # is noise there (and for any argument-less command).
    command_line = f"{command} {args!r}" if args else command
    text = (
        f"Command: {command_line}\nExit code: {exit_code}\n\nStdout:\n{stdout}\n\nStderr:\n{stderr}"
    )
    if 0 < timeout_ms < _TIMEOUT_NOTE_THRESHOLD_MS:
        text = f"{text}\n\nNote: This command had a {timeout_ms}ms timeout"
    return text


def handle_exec_command(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    deps: ExecRunDeps | None = None,
    *,
    registry: ExecJobRegistry | None = None,
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
        registry: Optional ExecJobRegistry coordinator for job execution.

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
    script_warning = _enforce_exec_policy(parsed, workspace)
    effective_deps = build_effective_exec_deps(session, deps)
    # AC-11: ``format=summary`` requests the bounded JSON envelope with
    # replayable resource handles; the default preserves the legacy
    # text/head-tail shape.
    format_value = params.get("format", "raw") if isinstance(params, Mapping) else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "summary"}:
        raise InvalidParamsError(f"Invalid format: {format_value!r}; expected 'raw' or 'summary'")
    summary = format_value == "summary"
    # A compound shell command runs through ``sh -c`` (after the per-segment
    # blacklist above); a plain command runs argv-direct.
    if parsed.shell_command is not None:
        run_argv0, run_args = "sh", ["-c", parsed.shell_command]
        hint_segments = _shell_command_segments(parsed.shell_command)
        display_cmd = parsed.shell_command
        display_args: list[str] = []
    else:
        run_argv0, run_args = parsed.command, parsed.args
        hint_segments = [(parsed.command, parsed.args)]
        display_cmd = parsed.command
        display_args = parsed.args

    if (
        effective_deps is not None and effective_deps.runner is not None
    ) or run_command is not _DEFAULT_RUN_COMMAND:
        try:
            output = run_command(
                run_argv0, run_args, workspace, parsed.timeout_ms, deps=effective_deps
            )
        except ExecutionError as exc:
            if not exc.timed_out:
                raise
            return ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
    else:
        effective_registry = (
            registry
            if registry is not None
            else get_exec_job_registry(
                process_manager=effective_deps.process_manager if effective_deps else None,
                spill_dir=resolve_spill_dir(workspace, effective_deps),
            )
        )
        cwd_prov = (
            effective_deps.cwd_provider
            if effective_deps is not None and effective_deps.cwd_provider is not None
            else Path.cwd
        )
        cwd = _workspace_root(workspace, cwd_provider=cwd_prov)
        full_cmd = [run_argv0, *run_args]
        current_sub = get_current_progress_subscription()
        try:
            job_id = effective_registry.start(
                full_cmd,
                cwd=str(cwd),
                timeout_ms=parsed.timeout_ms,
                max_runtime_ms=parsed.max_runtime_ms,
                capability=PROCESS_EXEC_BOUNDED_CAPABILITY,
                progress=current_sub,
            )
            res = effective_registry.wait(
                job_id,
                wait_ms=EXEC_SOFT_REPLY_BUDGET_MS,
                capability=PROCESS_EXEC_BOUNDED_CAPABILITY,
                progress=current_sub,
            )
        except ExecJobError as exc:
            raise ExecutionError(f"Failed to execute '{display_cmd}': {exc}") from exc
        except Exception as exc:
            raise ExecutionError(f"Failed to execute '{display_cmd}': {exc}") from exc

        if isinstance(res, JobSnapshot):
            snapshot_payload: dict[str, object] = {
                "job_id": res.job_id,
                "running": res.running,
                "elapsed_s": res.elapsed_s,
                "output": res.output,
                "verdict": serialize_liveness_verdict(res.verdict),
                "instruction": "call exec_wait; do not re-run",
            }
            return ToolResult(
                content=[ToolContent.json_content(snapshot_payload)],
                is_error=False,
            )

        if res.kill_cause is not None:
            verdict_dict = serialize_liveness_verdict(res.verdict)
            verdict_str = json.dumps(verdict_dict) if verdict_dict is not None else None
            kill_exc = ExecutionError(
                f"Failed to execute '{display_cmd}': job {res.job_id} terminated"
                f" ({res.kill_cause})",
                timed_out=True,
                timeout_ms=parsed.timeout_ms,
                timeout_cause=res.kill_cause,
                elapsed_ms=int(res.elapsed_s * 1000),
                ceiling_ms=res.ceiling_ms,
                job_id=res.job_id,
                partial_output=str(res.output.get("stdout", ""))
                or str(res.output.get("stderr", ""))
                or None,
                diagnostics=verdict_str,
            )
            return ToolResult(
                content=[ToolContent.text_content(str(kill_exc))],
                is_error=True,
            )

        stdout_bytes = str(res.output.get("stdout", "")).encode("utf-8")
        stderr_bytes = str(res.output.get("stderr", "")).encode("utf-8")
        output = _CompletedProcessAdapter(
            stdout=stdout_bytes,
            stderr=stderr_bytes,
            returncode=res.returncode or 0,
            truncated=bool(res.output.get("truncated", False)),
        )

    # The result header shows the command the caller actually asked for: the raw
    # shell string for a pipeline, the argv otherwise.
    text = format_exec_result(display_cmd, display_args, output, parsed.timeout_ms)
    # Append usage hints to the result text BEFORE spilling: an allowed
    # ``git status`` result should mention the dedicated git_* MCP read tools
    # so the agent learns the preferred surface; a ``grep`` result should
    # warn that the MCP explore endpoint is more efficient. Both hints go
    # inside the same text block so a spill still carries them.
    hints = exec_usage_hints(hint_segments)
    if script_warning is not None:
        hints.insert(0, script_warning)
    if hints:
        text = f"{text}\n\n" + "\n\n".join(hints)
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
        exec_resource_resolver=cast(
            "object | None", getattr(session, "exec_resource_resolver", None)
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    )


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "PROCESS_EXEC_BOUNDED_CAPABILITY",
    # Re-exported from ``_exec_vcs_scanner`` so tests / specs that pass over
    # the blacklist categories and the unsafe_exec / raw_exec handlers can
    # import the scanner through the same module surface.
    "_GIT_BLOCKED_SUBCOMMANDS",
    "_VCS_COMMANDS",
    "ExecParams",
    "ExecRunDeps",
    "ExecutionError",
    "WorkspaceWithRoot",
    "_CompletedProcessAdapter",
    "_format_exec_error",
    "_scan_text_for_vcs_violation",
    "apply_exec_policy",
    "check_command",
    "check_container_escape",
    "check_destructive_system",
    "check_multi_file_operation",
    "check_network_exfiltration",
    "check_privilege_escalation",
    "check_version_control",
    "exec_usage_hints",
    "find_vcs_usage_in_scripts",
    "format_exec_result",
    "handle_exec_command",
    "parse_exec_max_runtime",
    "parse_exec_params",
    "parse_exec_timeout",
    "resolve_spill_dir",
    "run_command",
    "serialize_liveness_verdict",
]
