"""Whitelist-aware VCS scanner for the exec / unsafe_exec / raw_exec handlers.

The exec family used to apply a BLANKET VCS ban: any ``git`` / ``hg`` / ``svn``
word anywhere in the command text was denied. The relaxed contract keeps
``hg`` / ``svn`` fully banned and turns ``git`` into a read-only-subcommand
whitelist (``status``, ``diff``, ``log``, ``show``, ``grep``, ``blame``,
``shortlog``, ``describe``, ``rev-parse``, ``rev-list``, ``ls-files``,
``ls-tree``, ``cat-file``, ``whatchanged``, ``name-rev``, ``for-each-ref``,
``show-ref``, ``count-objects``, ``var``). Anything else under ``git`` is
denied so an agent cannot mutate repository state out from under the run;
all writes go through Ralph's commit pipeline, all reads through the
``git_*`` MCP tools.

This module is the single source of truth for the policy. It is split out
of ``ralph.mcp.tools.exec`` so the public handler module stays under the
1000-line cap the repo-structure audit enforces.

Public surface:

- ``check_version_control`` — the per-segment scanner used by the segment-
  aware policy enforcer in ``exec.py``.
- ``find_vcs_usage_in_scripts`` — the script-content scanner; reuses
  the same whitelist so ``bash deploy.sh`` running ``git status`` is
  allowed but ``git push`` is denied.
- ``exec_usage_hints`` — the human-readable hint strings appended to
  the exec result text (a ``git_*`` MCP note for whitelisted git, an
  explore-endpoint warning for ``grep``).

Trust boundary: this scanner is a textual / static check only. It is the
defence-in-depth layer that catches the textual VCS word; the trust
boundary itself is the ``ProcessExecBounded`` capability check in the
public handler.
"""

from __future__ import annotations

import re
from pathlib import Path

# Version control tools: hg / svn are NEVER allowed via exec (the agent has
# no native read tools for either). git is allowed only for a fixed
# read-only subcommand whitelist; everything else (push, stash, checkout,
# commit, apply, tag, ...) is denied so the agent cannot mutate repository
# state out from under the run. All git writes go through Ralph's commit
# pipeline, all git reads through the git_* read tools.
_VCS_COMMANDS: frozenset[str] = frozenset({"git", "hg", "svn"})
# Deep VCS match: a standalone ``git``/``hg``/``svn`` word ANYWHERE in the
# command text is the trigger — including inside quotes, ``$(...)``/backtick
# substitutions, ``sh -c`` strings, and newline-separated sequences. Word
# boundaries keep ``github.com`` and ``.gitignore`` out of the net while
# still catching ``/usr/bin/git`` and ``git@host``. The textual match is
# fail-closed: a benign mention of the word (``echo git``) trips the
# scanner; the per-segment policy above decides whether the underlying
# subcommand is whitelisted.
_VCS_USAGE_PATTERN = re.compile(r"\b(" + "|".join(sorted(_VCS_COMMANDS)) + r")\b", re.IGNORECASE)
# Read-only git subcommands permitted via exec / unsafe_exec / raw_exec.
# The set is intentionally narrow: anything that can mutate the workspace,
# the index, refs, or remote state is omitted. ``diff`` is whitelisted but
# gated by ``_scan_diff_flags_for_writes`` so output-writing and
# external-helper flags are still denied. Bare ``git`` and ``git <flag>``
# without a whitelisted subcommand fail closed (denied).
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "grep",
        "blame",
        "shortlog",
        "describe",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "cat-file",
        "whatchanged",
        "name-rev",
        "for-each-ref",
        "show-ref",
        "count-objects",
        "var",
    }
)
# Git global flags that take a SEPARATE value (the next token is the value).
# ``-C``/``-c`` accept both forms (separate value and ``=value``); the long
# forms (``--git-dir`` etc.) are matched both as separate value and with
# ``=value``.
_GIT_VALUE_FLAG_NAMES: frozenset[str] = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)
# Git global flags that are BOOLEAN (skip in place; no value consumed).
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
# git diff flag guard: output-writing and external-helper flags. Mirrors
# the AC-06 read-only contract in ``git_read.py::_validate_diff_args`` so
# the same deny list applies no matter which surface (the diff MCP tool OR
# ``git diff`` via exec) the agent uses. A divergence between the two
# would be a policy leak.
_GIT_DIFF_OUTPUT_FLAGS: tuple[str, ...] = (
    "--output=",
    "--output",
    "-o=",
    "-o",
)
_GIT_DIFF_EXTERNAL_HELPER_FLAGS: tuple[str, ...] = (
    "--ext-diff",
    "--textconv",
    "--convience-diff",  # misspelled but accepted by older git
)
# Interpreters whose script-file argument is executed as shell: the script's
# CONTENT is scanned for VCS usage (``bash deploy.sh`` where deploy.sh runs
# ``git push`` is denied). ``source``/``.`` execute in-shell the same way.
_SHELL_INTERPRETERS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash", "ksh", "source", "."})
_SCRIPT_EXTENSIONS = (".sh", ".bash", ".zsh", ".ksh")
_SCRIPT_SCAN_LIMIT_BYTES = 256 * 1024
_SHEBANG_PREFIX = b"#!"


def _extract_git_subcommand_and_args(remainder: str) -> tuple[str | None, str]:
    """Find the git subcommand in the remainder text after a ``git`` match.

    Skips known global flags (value flags consume the next token; boolean
    flags skip in place). Any other ``-flag``-shaped token is treated as a
    subcommand-specific flag and skipped in place — the scanner is not a git
    parser, it only extracts the first non-flag token to use as the
    subcommand.

    Returns ``(subcommand, args_text)``. ``subcommand`` is the lowercased
    first non-flag token, or ``None`` if no subcommand can be determined
    (e.g. ``git`` alone, or only flags). ``args_text`` is the remainder
    text after the subcommand — used by ``_scan_diff_flags_for_writes``
    when the subcommand is ``diff``.
    """
    pos = 0
    length = len(remainder)
    while pos < length:
        # Skip leading whitespace.
        while pos < length and remainder[pos].isspace():
            pos += 1
        if pos >= length:
            return None, remainder
        # Read the next token (whitespace-bounded; quoted subcommands fall
        # through as-is and are fail-closed — the policy enforcer is not
        # a shell parser).
        token_end = pos
        while token_end < length and not remainder[token_end].isspace():
            token_end += 1
        token = remainder[pos:token_end]
        pos = token_end
        # Value flag with separate next token (e.g. ``-C .`` or
        # ``--git-dir <path>``): consume the next whitespace-bounded value.
        if token in _GIT_VALUE_FLAG_NAMES:
            while pos < length and remainder[pos].isspace():
                pos += 1
            if pos < length:
                value_end = pos
                while value_end < length and not remainder[value_end].isspace():
                    value_end += 1
                pos = value_end
            continue
        # Value flag with inline ``=value`` form (e.g. ``--git-dir=.git``).
        if any(token.startswith(flag + "=") for flag in _GIT_VALUE_FLAG_NAMES):
            continue
        # Boolean global flag: skip in place.
        if token in _GIT_BOOLEAN_GLOBAL_FLAGS:
            continue
        # Any other flag-shaped token is a subcommand-specific flag: skip in
        # place. This means ``git --version`` and ``git --unknown-flag``
        # never produce a subcommand → fail closed (denied) below.
        if token.startswith("-"):
            continue
        # Found the subcommand.
        return token.lower(), remainder[pos:]
    return None, remainder


def _scan_diff_flags_for_writes(args_text: str) -> str | None:
    """Return the offending flag if any write-producing or external-helper
    flag is present in the args text following a ``git diff`` invocation.

    Mirrors ``git_read.py::_validate_diff_args`` so the AC-06 read-only
    contract is enforced identically across both surfaces (the dedicated
    ``git_diff`` MCP tool AND ``git diff`` invoked via exec / unsafe_exec).
    Returns ``None`` if every flag is read-only.
    """
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
        # Output-writing flags: ``--output=path``, ``--output path``,
        # ``--output``, ``-o=path``, ``-o``, ``-o path``. Substring match
        # is intentional so ``--output-threshold`` is also rejected.
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
    """Return a denial reason when ``text`` references a banned VCS usage.

    Walks every ``_VCS_USAGE_PATTERN`` match. ``hg`` / ``svn`` are denied
    immediately (the agent has no native read tools for either). ``git``
    matches extract the subcommand from the remainder text via
    ``_extract_git_subcommand_and_args``:

    - a non-whitelisted subcommand → denial naming the subcommand.
    - a bare ``git`` or only-flags invocation (no determinable subcommand)
      → denial (fail closed; preserves the prior ``test_exec_blocks_git_command``
      contract for ``git`` / ``git --version``).
    - a whitelisted ``diff`` invocation → ``_scan_diff_flags_for_writes``
      guards output-writing and external-helper flags.
    - any other whitelisted subcommand → accepted; the loop continues so a
      single text containing both ``git status`` and ``git push`` still
      denies on the mutating call.

    Returns ``None`` when every VCS reference in the text is permitted.
    """
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
        # ``git``: extract subcommand from the remainder after this match.
        subcommand, args_text = _extract_git_subcommand_and_args(text[match.end() :])
        if subcommand is None:
            return (
                "git invocation without a whitelisted read-only subcommand is "
                "not allowed via exec (fail closed). Use the git_status / "
                "git_diff / git_log / git_show MCP tools, or specify a "
                "read-only subcommand."
            )
        if subcommand not in _GIT_READ_ONLY_SUBCOMMANDS:
            return (
                f"git subcommand '{subcommand}' mutates state and is never "
                "allowed via exec. Read-only git (status, diff, log, show, "
                "grep, ...) is permitted — use the git_status / git_diff / "
                "git_log / git_show MCP tools when no shell processing is "
                "needed. Commits go through the pipeline's commit phase."
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
    """Return a denial reason if the command invokes or references a VCS tool.

    hg / svn are denied unconditionally. ``git`` is denied unless its
    subcommand is in ``_GIT_READ_ONLY_SUBCOMMANDS`` and (for ``diff``) the
    flag guard passes; see ``_scan_text_for_vcs_violation`` for the full
    policy. The textual scan walks the WHOLE joined text so a VCS call
    hidden in a quoted ``sh -c`` string, a ``$(...)`` / backtick
    substitution, or a newline-separated sequence is still caught.
    """
    return _scan_text_for_vcs_violation(" ".join([command, *args]))


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
    """Return ``(script_token, vcs_word)`` when an executed shell script uses VCS.

    Best-effort static check: each candidate script token is resolved against
    the workspace root; a readable file that looks like a shell script (a
    known extension or a ``#!`` shebang) has its first
    ``_SCRIPT_SCAN_LIMIT_BYTES`` scanned by the same whitelist scanner that
    ``check_version_control`` uses. ``hg`` / ``svn`` and any non-whitelisted
    ``git`` subcommand trip the scan; a script running only ``git status``
    is allowed. Unreadable or non-file tokens are skipped — the textual
    match on the command line remains the primary net.
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
        reason = _scan_text_for_vcs_violation(head_bytes.decode("utf-8", errors="replace"))
        if reason is not None:
            return token, "git/hg/svn"
    return None


def exec_usage_hints(segments: list[tuple[str, list[str]]]) -> list[str]:
    """Return human-readable hint strings to append to the exec result text.

    Two hints can be emitted, in order:

    1. ``git``-hint — when any segment ran a whitelisted read-only git
       subcommand, append a note pointing at the dedicated ``git_status`` /
       ``git_diff`` / ``git_log`` / ``git_show`` MCP read tools. They are
       the preferred surface for repository-state reads (typed schemas,
       bounded output, dedicated capabilities); ``exec`` is for the case
       where shell-level processing is genuinely needed.
    2. ``grep``-hint — when any segment head is ``grep`` / ``egrep`` /
       ``fgrep``, append a warning that the MCP explore endpoint
       (``grep_files`` / indexed search) is more efficient for
       workspace-wide content searches. ``grep`` is never denied (the
       tooling exists, the hint is informational).

    Returns an empty list when no hint applies. The hints are joined with a
    blank line onto the standard ``format_exec_result`` text so they surface
    inside the same ToolContent block.
    """
    saw_whitelisted_git = False
    saw_grep = False
    for head, args in segments:
        head_key = head.strip().lower()
        head_base = head_key.rsplit("/", 1)[-1]
        if head_base == "git":
            subcommand, _ = _extract_git_subcommand_and_args(" ".join(args))
            if subcommand is not None and subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
                saw_whitelisted_git = True
        if head_base in {"grep", "egrep", "fgrep"}:
            saw_grep = True
    hints: list[str] = []
    if saw_whitelisted_git:
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
    "_GIT_READ_ONLY_SUBCOMMANDS",
    "_scan_text_for_vcs_violation",
    "check_version_control",
    "exec_usage_hints",
    "find_vcs_usage_in_scripts",
]
