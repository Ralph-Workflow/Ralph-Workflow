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
from ralph.process.manager import (
    DeadlineTimeoutExpired,
    ProcessTerminationIssuer,
    SpawnOptions,
    get_process_manager,
)
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
_REDIRECTION_CHARS = frozenset("<>")


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    @property
    def root(self) -> Path: ...


def _reject_nul_params(params: Mapping[str, object]) -> None:
    for name, value in params.items():
        items: list[object] = list(value) if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and "\x00" in item:
                raise InvalidParamsError(f"exec rejects an embedded NUL in {name!r}: {item!r}")


def parse_exec_max_runtime(params: Mapping[str, object]) -> int | None:
    raw = params.get("max_runtime_ms")
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        max_ms = int(raw)
        hard_max_ms = int(EXEC_JOB_CEILING_MAX_S * 1000)
        return min(max_ms, hard_max_ms)
    return None


def parse_exec_params(params: Mapping[str, object]) -> ExecParams:
    params = decode_stringified_argv(params)
    _reject_nul_params(params)
    timeout_ms = parse_exec_timeout(params)
    max_runtime_ms = parse_exec_max_runtime(params)

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
    timeout_value = params.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value > 0
        else DEFAULT_TIMEOUT_MS
    )
    return min(timeout_ms, EXEC_MAX_TIMEOUT_MS)


def _shell_command_source(params: Mapping[str, object]) -> str | None:
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
    in_single = False
    in_double = False
    i = 0
    length = len(command)
    while i < length:
        c = command[i]
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
    resolved_deps = deps or ExecRunDeps()
    cwd_provider = resolved_deps.cwd_provider or Path.cwd
    cwd = _workspace_root(workspace, cwd_provider=cwd_provider)
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
            hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000,
        )
    except subprocess.TimeoutExpired:
        effective_pm.terminate(
            handle, grace_period_s=0, issuer=ProcessTerminationIssuer.TOOL_TIMEOUT
        )
        raise
    except ManagedProcessOutputLimitExceededError as exc:
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
    parts: list[str] = []
    for label, value in (("Stdout", exc.stdout), ("Stderr", exc.stderr)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        if text:
            parts.append(f"{label}:\n{text[-_TIMEOUT_PARTIAL_OUTPUT_LIMIT_BYTES:]}")
    return "\n\n".join(parts) or None


def _format_exec_error(exc: Exception) -> str:
    if isinstance(exc, ExecutionError):
        return str(exc)
    return f"Error: {exc}"


def format_exec_result(
    command: str,
    args: list[str],
    output: _CompletedProcessAdapter,
    timeout_ms: int,
) -> str:
    stdout = output.stdout.decode("utf-8", errors="replace")
    stderr = output.stderr.decode("utf-8", errors="replace")
    exit_code = output.returncode
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
    require_capability(session, PROCESS_EXEC_BOUNDED_CAPABILITY, "Command execution")
    parsed = parse_exec_params(params)
    script_warning = _enforce_exec_policy(parsed, workspace)
    effective_deps = build_effective_exec_deps(session, deps)
    format_value = params.get("format", "raw") if isinstance(params, Mapping) else "raw"
    if not isinstance(format_value, str) or format_value not in {"raw", "summary"}:
        raise InvalidParamsError(f"Invalid format: {format_value!r}; expected 'raw' or 'summary'")
    summary = format_value == "summary"
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

    text = format_exec_result(display_cmd, display_args, output, parsed.timeout_ms)
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
        ),
    )


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "PROCESS_EXEC_BOUNDED_CAPABILITY",
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
