
from __future__ import annotations

import re
from pathlib import Path

_VCS_COMMANDS: frozenset[str] = frozenset({"git", "hg", "svn"})
_VCS_USAGE_PATTERN = re.compile(r"\b(" + "|".join(sorted(_VCS_COMMANDS)) + r")\b", re.IGNORECASE)
_GIT_BLOCKED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "add",
        "am",
        "apply",
        "bisect",
        "branch",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "config",
        "fetch",
        "init",
        "merge",
        "mv",
        "pull",
        "push",
        "rebase",
        "reflog",
        "remote",
        "reset",
        "restore",
        "rm",
        "stash",
        "switch",
        "tag",
    }
)
_GIT_VALUE_FLAG_NAMES: frozenset[str] = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)
_GIT_BOOLEAN_GLOBAL_FLAGS: frozenset[str] = frozenset(
    {
        "-P",
        "--no-pager",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
    }
)
_GIT_DIFF_OUTPUT_FLAGS: tuple[str, ...] = (
    "--output=",
    "--output",
    "-o=",
    "-o",
)
_GIT_DIFF_EXTERNAL_HELPER_FLAGS: tuple[str, ...] = (
    "--ext-diff",
    "--textconv",
    "--convience-diff",
)
_SHELL_INTERPRETERS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash", "ksh", "source", "."})
_SCRIPT_EXTENSIONS = (".sh", ".bash", ".zsh", ".ksh")
_SCRIPT_SCAN_LIMIT_BYTES = 256 * 1024
_SHEBANG_PREFIX = b"#!"


def _extract_git_subcommand_and_args(remainder: str) -> tuple[str | None, str]:
    pos = 0
    length = len(remainder)
    while pos < length:
        while pos < length and remainder[pos].isspace():
            pos += 1
        if pos >= length:
            return None, remainder
        token_end = pos
        while token_end < length and not remainder[token_end].isspace():
            token_end += 1
        token = remainder[pos:token_end]
        pos = token_end
        if token in _GIT_VALUE_FLAG_NAMES:
            while pos < length and remainder[pos].isspace():
                pos += 1
            if pos < length:
                value_end = pos
                while value_end < length and not remainder[value_end].isspace():
                    value_end += 1
                pos = value_end
            continue
        if any(token.startswith(flag + "=") for flag in _GIT_VALUE_FLAG_NAMES):
            continue
        if token in _GIT_BOOLEAN_GLOBAL_FLAGS:
            continue
        if token.startswith("-"):
            continue
        return token.lower(), remainder[pos:]
    return None, remainder


def _scan_diff_flags_for_writes(args_text: str) -> str | None:
    pos = 0
    length = len(args_text)
    while pos < length:
        while pos < length and args_text[pos].isspace():
            pos += 1
        if pos >= length:
            return None
        token_end = pos
        while token_end < length and not args_text[token_end].isspace():
            token_end += 1
        token = args_text[pos:token_end]
        pos = token_end
        for prefix in _GIT_DIFF_OUTPUT_FLAGS:
            if prefix == "--output" and (
                token == "--output" or "--output=" in token or token.startswith("--output ")
            ):
                return token
            if prefix == "-o" and (
                token == "-o" or token.startswith("-o=") or token.startswith("-o ")
            ):
                return token
        for bad in _GIT_DIFF_EXTERNAL_HELPER_FLAGS:
            if bad in token:
                return bad
    return None


def _scan_text_for_vcs_violation(text: str) -> str | None:
    pos = 0
    while True:
        match = _VCS_USAGE_PATTERN.search(text, pos)
        if match is None:
            return None
        word = match.group(0).lower()
        if word in {"hg", "svn"}:
            return (
                f"VCS tool '{word}' is never allowed via exec. Use Ralph's "
                "git_* read tools for git reads; commits go through the "
                "pipeline's commit phase."
            )
        subcommand, args_text = _extract_git_subcommand_and_args(text[match.end() :])
        if subcommand in _GIT_BLOCKED_SUBCOMMANDS:
            return (
                f"git subcommand '{subcommand}' is a high-risk version-control "
                "operation and is not allowed via exec. Commits go through "
                "the pipeline's commit phase."
            )
        if subcommand == "diff":
            bad_flag = _scan_diff_flags_for_writes(args_text)
            if bad_flag is not None:
                return (
                    f"git diff with output-writing or external-helper flag "
                    f"({bad_flag!r}) is not allowed via exec. The diff MCP "
                    "tool enforces the same flag guard — use it instead."
                )
        pos = match.end()


def check_version_control(command: str, args: list[str]) -> str | None:
    return _scan_text_for_vcs_violation(" ".join([command, *args]))


def _script_candidate_tokens(segments: list[tuple[str, list[str]]]) -> list[str]:
    candidates: list[str] = []
    for head, args in segments:
        candidates.append(head)
        head_key = head.strip().lower()
        if head_key.rsplit("/", 1)[-1] in _SHELL_INTERPRETERS:
            for arg in args:
                if not arg.startswith("-"):
                    candidates.append(arg)
                    break
    return candidates


def find_vcs_usage_in_scripts(
    segments: list[tuple[str, list[str]]], workspace_root: Path
) -> tuple[str, str] | None:
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
        script_text = head_bytes.decode("utf-8", errors="replace").replace("\x00", "")
        reason = _scan_text_for_vcs_violation(script_text)
        if reason is not None:
            return token, "git/hg/svn"
    return None


def exec_usage_hints(segments: list[tuple[str, list[str]]]) -> list[str]:
    saw_allowed_git = False
    saw_grep = False
    for head, args in segments:
        head_key = head.strip().lower()
        head_base = head_key.rsplit("/", 1)[-1]
        if head_base == "git":
            subcommand, _ = _extract_git_subcommand_and_args(" ".join(args))
            if subcommand is not None and subcommand not in _GIT_BLOCKED_SUBCOMMANDS:
                saw_allowed_git = True
        if head_base in {"grep", "egrep", "fgrep"}:
            saw_grep = True
    hints: list[str] = []
    if saw_allowed_git:
        hints.append(
            "Note: the dedicated MCP read tools git_status, git_diff, "
            "git_log, and git_show are preferred for repository-state "
            "reads (typed schemas, bounded output, dedicated "
            "capabilities). Use exec only when shell-level processing "
            "is genuinely needed."
        )
    if saw_grep:
        hints.append(
            "Note: the MCP explore endpoint (grep_files, indexed "
            "search) is more efficient than spawning a grep process "
            "for workspace-wide content searches — prefer it unless "
            "shell-level post-processing is required."
        )
    return hints


__all__ = [
    "_GIT_BLOCKED_SUBCOMMANDS",
    "_scan_text_for_vcs_violation",
    "check_version_control",
    "exec_usage_hints",
    "find_vcs_usage_in_scripts",
]
