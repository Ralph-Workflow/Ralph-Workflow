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
- A static blacklist (applied per pipeline segment for shell commands)
  covering privilege escalation (``sudo``, ``su``,
  ``doas``, ``pkexec``, ``runuser``), destructive system commands
  (``shutdown``, ``reboot``, ``halt``, ``poweroff``, ``killall``), network
  tunnel and remote-network tools (``nc``, ``ncat``, ``netcat``,
  ``socat``, ``ssh``, ``scp``, ``rsync``), container / namespace
  escapes (``docker``, ``podman``, ``chroot``, ``nsenter``, ``unshare``),
  and version control commands (``git``, ``hg``, ``svn`` — reads go through
  the ``git_*`` tools, writes through the pipeline's commit phase).
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
import re
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_output_spill import SPILL_OUTPUT_LIMIT_BYTES, format_or_spill
from ralph.mcp.tools._exec_params import ExecParams
from ralph.mcp.tools._exec_run_deps import CwdProvider, ExecRunDeps, build_effective_exec_deps
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
# through exec does not time out on every call. Per-call
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
    "version_control": "version control",
}

_SHELL_OPERATOR_CHARS = frozenset("|&;<>")
# A redirection operator (``< > >> <<``) is followed by a filename target, not a
# new command; a command separator (``| ; & && ||``) introduces one. Segmentation
# splits on separators and skips redirection targets so a benign ``echo x >
# reboot`` is not misread as invoking the ``reboot`` command.
_REDIRECTION_CHARS = frozenset("<>")

_PRIVILEGE_ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runuser"}
# Version control never runs through exec (safe OR unsafe): all git writes go
# through Ralph's commit pipeline and all git reads through the git_* read
# tools, so an agent cannot mutate repository state out from under the run.
_VCS_COMMANDS: frozenset[str] = frozenset({"git", "hg", "svn"})
# Deep VCS match: a standalone ``git``/``hg``/``svn`` word ANYWHERE in the
# command text is denied — including inside quotes, ``$(...)``/backtick
# substitutions, ``sh -c`` strings, and newline-separated sequences. Word
# boundaries keep ``github.com`` and ``.gitignore`` out of the net while
# still catching ``/usr/bin/git`` and ``git@host``. Deliberately fail-closed:
# a benign mention of the word (``echo git``) is denied rather than risk a
# textual smuggling path.
_VCS_USAGE_PATTERN = re.compile(r"\b(" + "|".join(sorted(_VCS_COMMANDS)) + r")\b", re.IGNORECASE)
# Interpreters whose script-file argument is executed as shell: the script's
# CONTENT is scanned for VCS usage (``bash deploy.sh`` where deploy.sh runs
# ``git push`` is denied). ``source``/``.`` execute in-shell the same way.
_SHELL_INTERPRETERS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash", "ksh", "source", "."})
_SCRIPT_EXTENSIONS = (".sh", ".bash", ".zsh", ".ksh")
_SCRIPT_SCAN_LIMIT_BYTES = 256 * 1024
_SHEBANG_PREFIX = b"#!"
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
    timeout_ms = _parse_exec_timeout(params)

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
        )

    command_tokens = _parse_exec_command_tokens(params)
    args = _parse_exec_args(params.get("args"))
    command = command_tokens[0] if command_tokens else ""
    merged_args = [*command_tokens[1:], *args]
    return ExecParams(command=command, args=merged_args, timeout_ms=timeout_ms)


def _parse_exec_timeout(params: Mapping[str, object]) -> int:
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
    if stripped and _has_unquoted_shell_operator(stripped):
        return stripped
    return None


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
        check_version_control,
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


def find_vcs_usage(command: str, args: list[str]) -> str | None:
    """Return the matched VCS word when the command text references one anywhere.

    Matches the whole joined text, not just the command head, so a git call
    hidden in a quoted ``sh -c`` string, a ``$(...)``/backtick substitution, or
    a newline-separated sequence is still caught (see ``_VCS_USAGE_PATTERN``).
    """
    match = _VCS_USAGE_PATTERN.search(" ".join([command, *args]))
    return match.group(0) if match else None


def check_version_control(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command invokes or references a VCS tool."""
    word = find_vcs_usage(command, args)
    if word is not None:
        desc = _description("version_control")
        return (
            f"Command references '{word}': {desc} operations are not "
            "allowed via exec. Use Ralph's git_* read tools; commits go "
            "through the pipeline's commit phase."
        )
    return None


def _script_candidate_tokens(segments: list[tuple[str, list[str]]]) -> list[str]:
    """Return tokens that may name an executed script file.

    Every segment head is a candidate (``./release``); when the head is a
    shell interpreter, its first non-flag argument is the script it runs
    (``bash deploy.sh``). An inline ``-c`` string is not a file and is
    already covered by the textual VCS match.
    """
    candidates: list[str] = []
    for head, args in segments:
        candidates.append(head)
        if _command_key(head).rsplit("/", 1)[-1] in _SHELL_INTERPRETERS:
            for arg in args:
                if not arg.startswith("-"):
                    candidates.append(arg)
                    break
    return candidates


def find_vcs_usage_in_scripts(
    segments: list[tuple[str, list[str]]], workspace_root: Path
) -> tuple[str, str] | None:
    """Return ``(script_token, vcs_word)`` when an executed shell script uses VCS.

    Best-effort static check: each candidate script token is resolved against
    the workspace root; a readable file that looks like a shell script (a
    known extension or a ``#!`` shebang) has its first
    ``_SCRIPT_SCAN_LIMIT_BYTES`` scanned for a VCS word. Unreadable or
    non-file tokens are skipped — the textual match on the command line
    remains the primary net.
    """
    for token in _script_candidate_tokens(segments):
        path = Path(token) if Path(token).is_absolute() else workspace_root / token
        try:
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                head_bytes = handle.read(_SCRIPT_SCAN_LIMIT_BYTES)
        except OSError:
            continue
        if not (token.endswith(_SCRIPT_EXTENSIONS) or head_bytes.startswith(_SHEBANG_PREFIX)):
            continue
        match = _VCS_USAGE_PATTERN.search(head_bytes.decode("utf-8", errors="replace"))
        if match:
            return token, match.group(0)
    return None


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
        current.append(token)
    if current:
        segments.append((current[0], current[1:]))
    return segments


def _enforce_exec_policy(parsed: ExecParams, workspace: object) -> None:
    """Enforce the blacklist for an exec invocation, shell-aware.

    A compound shell command is checked against every command in the pipeline;
    a plain command is checked directly. Executed shell scripts (``bash x.sh``,
    ``./x.sh``) are additionally content-scanned for VCS usage so a git call
    cannot be laundered through a script file. Raises ``CapabilityDeniedError``
    on the first blacklisted command.
    """
    if parsed.shell_command is None:
        segments: list[tuple[str, list[str]]] = [(parsed.command, parsed.args)]
    else:
        segments = _shell_command_segments(parsed.shell_command)
    for command, args in segments:
        apply_exec_policy(command, args)
    script_hit = find_vcs_usage_in_scripts(segments, _workspace_root(workspace))
    if script_hit is not None:
        script, word = script_hit
        raise CapabilityDeniedError(
            f"Script '{script}' uses '{word}': version control operations are not allowed via exec"
        )


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
    _enforce_exec_policy(parsed, workspace)
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
    else:
        run_argv0, run_args = parsed.command, parsed.args
    try:
        output = run_command(run_argv0, run_args, workspace, parsed.timeout_ms, deps=effective_deps)
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
    # The result header shows the command the caller actually asked for: the raw
    # shell string for a pipeline, the argv otherwise.
    if parsed.shell_command is not None:
        text = format_exec_result(parsed.shell_command, [], output, parsed.timeout_ms)
    else:
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
        exec_resource_resolver=_resolve_exec_resolver(session),
    )


def _resolve_exec_resolver(session: object) -> object | None:
    """Read the session's exec resource resolver attribute (or ``None``).

    The attribute is typed as the
    :class:`ralph.mcp.tools._exec_resource_protocol.ExecResourceResolverLike`
    protocol on the production session classes; the helper returns
    ``object | None`` to keep the surrounding ``format_or_spill``
    signature narrow without importing the protocol at module
    load time.
    """
    # The local annotation is intentionally ``Any | None`` so the
    # surrounding ``format_or_spill`` keeps its broad ``object | None``
    # parameter type. The helper is internal; callers that need
    # the protocol type import it directly.
    result: object | None = getattr(session, "exec_resource_resolver", None)
    return result


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
