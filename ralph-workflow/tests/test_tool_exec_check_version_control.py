"""Tests for the whitelist-aware VCS check in exec.py.

The exec / unsafe_exec / raw_exec tools used to apply a blanket VCS ban
(any git / hg / svn word in the command text is denied). The new contract
keeps hg / svn fully banned and adds a read-only-subcommand WHITELIST for
git (``status``, ``diff``, ``log``, ``show``, ``grep``, ``blame``,
``shortlog``, ``describe``, ``rev-parse``, ``rev-list``, ``ls-files``,
``ls-tree``, ``cat-file``, ``whatchanged``, ``name-rev``,
``for-each-ref``, ``show-ref``, ``count-objects``, ``var``). Anything
else under ``git`` is denied.

These tests pin that contract end-to-end: the low-level scanner
(``check_version_control``), the per-segment policy
(``apply_exec_policy``), the public handler (``handle_exec_command``),
and the unsafe_exec handler (``handle_unsafe_exec``).

Every case is table-driven: the tables below hold the concrete inputs and
each test walks the table, asserting the observable outcome of every row.
The failure message always names the offending row, so a table-driven
failure is as actionable as a per-case one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
    ToolResult,
)
from ralph.mcp.tools.exec import (
    _GIT_BLOCKED_SUBCOMMANDS,
    ExecRunDeps,
    apply_exec_policy,
    check_version_control,
    handle_exec_command,
)
from ralph.mcp.tools.unsafe_exec import (
    PROCESS_EXEC_UNBOUNDED_CAPABILITY,
    handle_unsafe_exec,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

#: Git subcommands the exec policy must ALLOW: the read-only whitelist.
_ALLOWED_GIT_SUBCOMMANDS: tuple[str, ...] = (
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
)

#: Git subcommands the exec policy must DENY: the high-risk, state-mutating
#: operations that belong to the pipeline's commit phase, not to exec.
_BLOCKED_GIT_SUBCOMMANDS: tuple[str, ...] = (
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
)

#: ``(command, args)`` invocations the policy must ALLOW. The scanner
#: returns ``None`` (no denial reason) for every row.
_ALLOWED_INVOCATIONS: tuple[tuple[str, list[str]], ...] = (
    # The read-only git whitelist.
    ("git", ["status"]),
    ("git", ["diff"]),
    ("git", ["log", "-n", "5"]),
    ("git", ["show", "HEAD"]),
    ("git", ["grep", "needle"]),
    ("git", ["blame", "file.py"]),
    ("git", ["shortlog", "-n", "10"]),
    ("git", ["describe", "--tags"]),
    ("git", ["rev-parse", "HEAD"]),
    ("git", ["rev-list", "--count", "HEAD"]),
    ("git", ["ls-files"]),
    ("git", ["ls-tree", "HEAD"]),
    ("git", ["cat-file", "-p", "HEAD"]),
    ("git", ["whatchanged", "--since=yesterday"]),
    ("git", ["name-rev", "HEAD"]),
    ("git", ["for-each-ref", "--format=%(refname)"]),
    ("git", ["show-ref"]),
    ("git", ["count-objects", "-v"]),
    ("git", ["var", "GIT_AUTHOR_IDENT"]),
    # Git global flags. ``-C <path>`` is a value flag (the repo path) and
    # must be skipped before the subcommand is parsed so the subcommand
    # check sees ``status``, not ``-C`` or ``.``. ``-P`` / ``--no-pager``
    # are boolean global flags: they must be skipped in place without
    # consuming a following value.
    ("git", ["-C", ".", "status"]),
    ("git", ["--no-pager", "log"]),
    ("git", ["-P", "log"]),
    ("git", ["--git-dir=.git", "log"]),
    ("git", ["--work-tree=/tmp/x", "status"]),
    # No subcommand, a flag-only invocation, ``worktree`` (Ralph's own
    # agent workflow uses worktrees) and an UNKNOWN subcommand are all
    # allowed: the policy denies only KNOWN high-risk subcommands, so a
    # future git subcommand cannot be rejected by accident.
    ("git", []),
    ("git", ["--version"]),
    ("git", ["worktree", "add", "wt", "main"]),
    ("git", ["future-read-command"]),
    # Non-VCS commands. A ``github.com`` substring in an argument must not
    # be misread as a git tool call: only a standalone ``git`` word in
    # COMMAND position is a VCS invocation. ``.gitignore`` is a file, not
    # a git command.
    ("ls", ["-la"]),
    ("echo", ["hello"]),
    ("echo", ["https://github.com/foo/bar"]),
    ("cat", [".gitignore"]),
)

#: ``(command, args, reason_fragment)`` invocations the policy must DENY.
#: ``reason_fragment`` is the token the denial reason must name.
_DENIED_INVOCATIONS: tuple[tuple[str, list[str], str], ...] = (
    ("git", ["push"], "push"),
    ("git", ["push", "origin", "main"], "push"),
    ("git", ["stash"], "stash"),
    ("git", ["stash", "pop"], "stash"),
    ("git", ["checkout", "main"], "checkout"),
    ("git", ["switch", "main"], "switch"),
    ("git", ["restore", "."], "restore"),
    ("git", ["commit", "-m", "msg"], "commit"),
    ("git", ["add", "."], "add"),
    ("git", ["apply", "patch.diff"], "apply"),
    ("git", ["am", "patch"], "am"),
    ("git", ["tag", "v1"], "tag"),
    ("git", ["branch", "x"], "branch"),
    ("git", ["merge", "feat"], "merge"),
    ("git", ["rebase", "main"], "rebase"),
    ("git", ["reset", "--hard"], "reset"),
    ("git", ["fetch", "origin"], "fetch"),
    ("git", ["pull", "origin", "main"], "pull"),
    ("git", ["clone", "url"], "clone"),
    ("git", ["init"], "init"),
    ("git", ["config", "user.name", "x"], "config"),
    ("git", ["remote", "add", "origin", "url"], "remote"),
    ("git", ["reflog"], "reflog"),
    ("git", ["clean", "-fd"], "clean"),
    ("git", ["rm", "file"], "rm"),
    ("git", ["mv", "a", "b"], "mv"),
    ("git", ["bisect", "start"], "bisect"),
    # The ``-c alias.x=push`` global flag value contains a mutating
    # subcommand token; the scanner must skip the ``-c x=y`` pair and
    # evaluate the actual subcommand ``push``.
    ("git", ["-c", "alias.x=push", "push"], "push"),
    # A full path to the git binary, like ``/usr/bin/git``, must be treated
    # the same as bare ``git``: the basename is what counts.
    ("/usr/bin/git", ["push"], "push"),
    # hg / svn are banned unconditionally.
    ("hg", ["update"], "hg"),
    ("svn", ["commit"], "svn"),
)

#: ``git diff`` flags that write output or shell out to an external helper.
#: They are denied even though ``diff`` itself is whitelisted, so
#: ``git diff`` cannot be turned into a write.
_DIFF_WRITE_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--output=/tmp/x",),
    ("--output", "/tmp/x"),
    ("-o", "/tmp/x"),
    ("-o=/tmp/x",),
    ("--ext-diff",),
    ("--textconv",),
    ("--convience-diff",),
)

#: Read-only ``git diff`` flags that must not trip the flag guard.
_READ_ONLY_DIFF_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--stat",),
    ("--name-only",),
    ("--shortstat",),
    ("--staged",),
    ("--unified=3",),
    ("--diff-filter=ACMRT",),
)

#: Compound shell commands where a whitelisted subcommand must not mask a
#: mutating one (and the other way round).
_COMPOUND_COMMANDS: tuple[str, ...] = (
    "git status && git push origin main",
    "git push origin main; git status",
)

#: Command shapes that hide a mutating git call from the segment-aware scan.
#: The deep textual scan must still catch it.
_SUBSTITUTION_COMMANDS: tuple[str | list[str], ...] = (
    "echo $(git push origin main) | wc -c",
    "echo `git commit -m x` | cat",
    ["sh", "-c", "git push origin main"],
)

#: ``(params, required_tokens)`` rows for the exec git-hint. A successful
#: exec that used an allowed git subcommand must carry a note pointing at
#: the dedicated ``git_*`` MCP read tools in its result text, so an agent
#: reading the output learns that a dedicated endpoint exists.
_EXEC_GIT_HINT_CASES: tuple[tuple[dict[str, object], tuple[str, ...]], ...] = (
    ({"command": "git", "args": ["status"]}, ("git_status", "git_diff", "git_log", "git_show")),
    ({"command": "git status"}, ("git_status",)),
)

#: ``(command, args)`` grep invocations. ``grep`` is NEVER denied whatever
#: the arguments are; only the explore-endpoint warning is appended.
_GREP_INVOCATIONS: tuple[tuple[str, list[str]], ...] = (
    ("grep", ["-r", "needle", "."]),
    ("grep", ["needle", "file"]),
    ("egrep", ["needle", "file"]),
    ("fgrep", ["needle", "file"]),
)

#: ``(script_name, script_body, expected_warning)`` rows for the
#: script-content scanner. ``expected_warning`` is the exact warning
#: fragment the handler must emit; ``None`` means no warning at all.
_SCRIPT_CASES: tuple[tuple[str, str, str | None], ...] = (
    (
        "audit.sh",
        "#!/bin/sh\ngit status\necho done\n",
        None,
    ),
    (
        "deploy.sh",
        "#!/bin/sh\ngit status\ngit push origin main\n",
        "Warning: script 'deploy.sh' contains version-control commands",
    ),
)

#: ``(command, stdout, required_all, required_any)`` rows for the
#: unsafe_exec hints. ``required_all`` tokens must ALL be present in the
#: result text; at least one ``required_any`` token must be present.
_UNSAFE_EXEC_HINT_CASES: tuple[tuple[str, bytes, tuple[str, ...], tuple[str, ...]], ...] = (
    ("git status", b"clean", ("git_status", "git_log"), ()),
    ("grep needle file", b"file:1:needle", (), ("grep_files", "explore")),
)


def test_git_subcommand_blocklist_matches_policy() -> None:
    """The blocked set is exactly the high-risk subcommand policy.

    ``worktree`` is an intentional exception: Ralph's agent workflow uses
    worktrees, so it must never land in the blocked set.
    """
    for subcommand in _ALLOWED_GIT_SUBCOMMANDS:
        assert subcommand not in _GIT_BLOCKED_SUBCOMMANDS, (
            f"{subcommand!r} is a read-only git subcommand and must not be blocked"
        )
    for subcommand in _BLOCKED_GIT_SUBCOMMANDS:
        assert subcommand in _GIT_BLOCKED_SUBCOMMANDS, (
            f"{subcommand!r} is a state-mutating git subcommand and must be blocked"
        )
    assert "worktree" not in _GIT_BLOCKED_SUBCOMMANDS, (
        "worktree must stay allowed: Ralph's agent workflow uses worktrees"
    )
    unexpected = sorted(_GIT_BLOCKED_SUBCOMMANDS - frozenset(_BLOCKED_GIT_SUBCOMMANDS))
    assert not unexpected, (
        f"the blocked set must contain exactly the high-risk subcommands; "
        f"unexpected entries: {unexpected}"
    )


def test_allowed_invocations_are_not_denied() -> None:
    """The scanner admits every allowed invocation."""
    for command, args in _ALLOWED_INVOCATIONS:
        reason = check_version_control(command, args)
        assert reason is None, f"{command} {args} must be allowed; got denial reason {reason!r}"


def test_denied_invocations_are_denied() -> None:
    """The scanner denies every state-mutating or non-git VCS invocation."""
    for command, args, fragment in _DENIED_INVOCATIONS:
        reason = check_version_control(command, args)
        assert reason is not None, f"{command} {args} must be denied"
        assert fragment in reason.lower(), (
            f"{command} {args} must be denied naming {fragment!r}; got {reason!r}"
        )


def test_diff_write_flags_are_denied() -> None:
    """``git diff`` with an output-writing or external-helper flag is denied."""
    for flags in _DIFF_WRITE_FLAGS:
        reason = check_version_control("git", ["diff", *flags])
        assert reason is not None, f"git diff {' '.join(flags)} must be denied"
        text = reason.lower()
        assert "diff" in text or "output" in text or "helper" in text, (
            f"git diff {' '.join(flags)} must be denied naming the offending flag; got {reason!r}"
        )


def test_read_only_diff_flags_are_allowed() -> None:
    """Read-only ``git diff`` flags must not trip the flag guard."""
    for flags in _READ_ONLY_DIFF_FLAGS:
        reason = check_version_control("git", ["diff", *flags])
        assert reason is None, (
            f"git diff {' '.join(flags)} must be allowed; got denial reason {reason!r}"
        )


def test_compound_command_with_mutating_git_is_denied(tmp_path: Path) -> None:
    """A whitelisted subcommand cannot mask a mutating one in the same command.

    The text scan must catch ``git push`` even after ``git status`` was
    admitted by the segment-aware scan.
    """
    for command in _COMPOUND_COMMANDS:
        # The whitelisted half is admitted on its own...
        assert check_version_control("git", ["status"]) is None, (
            "a whitelisted git subcommand must be admitted on its own"
        )
        # ...and the mutating half is denied by the per-segment policy.
        with pytest.raises(CapabilityDeniedError, match="git"):
            apply_exec_policy("git", ["push"])
        # The compound command is denied as a whole.
        with pytest.raises(CapabilityDeniedError, match="git"):
            handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(tmp_path),
                {"command": command},
                _deps(stdout=b"clean"),
            )


def test_whitelisted_only_in_pipeline_is_allowed(tmp_path: Path) -> None:
    """A compound command whose git subcommands are all whitelisted runs."""
    result = handle_exec_command(
        MockSession({"ProcessExecBounded"}),
        MockWorkspaceRoot(tmp_path),
        {"command": "git status && git log -n 1"},
        _deps(stdout=b"clean"),
    )
    assert result.is_error is False


def test_script_vcs_usage_is_reported(tmp_path: Path) -> None:
    """The script-content scanner reuses the whitelist scanner.

    A script running only ``git status`` is allowed; a script running
    ``git push`` is reported with a warning naming the script.
    """
    for script_name, script_body, expected_warning in _SCRIPT_CASES:
        script = tmp_path / script_name
        script.write_text(script_body, encoding="utf-8")

        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": f"bash {script_name}"},
            _deps(stdout=b"clean"),
        )

        assert result.is_error is False, (
            f"running {script_name} must not error; got {_result_text(result)!r}"
        )
        text = _result_text(result)
        if expected_warning is None:
            assert "Warning:" not in text, (
                f"{script_name} runs only a whitelisted git subcommand and must "
                f"not be flagged; got {text!r}"
            )
        else:
            assert expected_warning in text, (
                f"{script_name} runs a mutating git subcommand and must be "
                f"flagged with {expected_warning!r}; got {text!r}"
            )


def test_framework_launcher_with_git_text_is_not_flagged(tmp_path: Path) -> None:
    """A non-script launcher whose text merely mentions git is not flagged."""
    script = tmp_path / "rails"
    script.write_text("#!/usr/bin/env ruby\n# git metadata is optional\n", encoding="utf-8")

    result = handle_exec_command(
        MockSession({"ProcessExecBounded"}),
        MockWorkspaceRoot(tmp_path),
        {"command": "./rails"},
        _deps(stdout=b"ok"),
    )

    assert result.is_error is False
    assert "Warning:" not in _result_text(result)


def test_mutating_git_in_substitution_is_denied(tmp_path: Path) -> None:
    """A VCS command hidden inside a substitution or ``sh -c`` string is denied."""
    for command in _SUBSTITUTION_COMMANDS:
        with pytest.raises(CapabilityDeniedError, match="git"):
            handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(tmp_path),
                {"command": command},
                _deps(stdout=b"clean"),
            )


def test_whitelisted_git_appends_git_mcp_hint(tmp_path: Path) -> None:
    """An allowed git subcommand appends a note about the dedicated MCP tools."""
    for params, required_tokens in _EXEC_GIT_HINT_CASES:
        result = handle_exec_command(
            MockSession({"ProcessExecBounded"}),
            MockWorkspaceRoot(tmp_path),
            params,
            _deps(stdout=b"clean"),
        )
        assert result.is_error is False, f"{params!r} must not error"
        text = _result_text(result)
        for token in required_tokens:
            assert token in text, (
                f"{params!r} must carry the git MCP hint naming {token!r}; got {text!r}"
            )


def test_non_git_command_does_not_emit_git_mcp_hint(tmp_path: Path) -> None:
    """A command without an allowed git subcommand carries no git hint."""
    result = handle_exec_command(
        MockSession({"ProcessExecBounded"}),
        MockWorkspaceRoot(tmp_path),
        {"command": "ls", "args": []},
        _deps(stdout=b"x"),
    )
    text = _result_text(result)
    assert "git_status" not in text
    assert "git_log" not in text


def test_grep_variants_run_and_emit_explore_hint(tmp_path: Path) -> None:
    """Every grep flavour runs and the result points at the MCP explore endpoint."""
    for command, args in _GREP_INVOCATIONS:
        result = handle_exec_command(
            MockSession({"ProcessExecBounded"}),
            MockWorkspaceRoot(tmp_path),
            {"command": command, "args": args},
            _deps(stdout=b"file.py:1:matched"),
        )
        assert result.is_error is False, f"{command} {args} must not error"
        text = _result_text(result)
        # ``grep`` is never denied; the result must still execute.
        assert "matched" in text, f"{command} {args} must return the command output"
        # The warning must mention the dedicated MCP explore endpoint.
        assert "grep_files" in text or "explore" in text.lower(), (
            f"{command} {args} must carry the explore hint; got {text!r}"
        )


def test_grep_command_is_not_denied(tmp_path: Path) -> None:
    """Explicit deny guard: ``grep`` is NEVER blacklisted regardless of args."""
    result = handle_exec_command(
        MockSession({"ProcessExecBounded"}),
        MockWorkspaceRoot(tmp_path),
        {"command": "grep", "args": ["-r", "x", "."]},
        _deps(stdout=b""),
    )
    assert result.is_error is False


def test_unsafe_exec_appends_the_same_hints(tmp_path: Path) -> None:
    """unsafe_exec / raw_exec append the git-hint and grep-hint notes too."""
    for command, stdout, required_all, required_any in _UNSAFE_EXEC_HINT_CASES:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": command},
            _deps(stdout=stdout),
        )
        assert result.is_error is False, f"{command!r} must not error"
        text = _result_text(result)
        for token in required_all:
            assert token in text, f"{command!r} must carry a hint naming {token!r}; got {text!r}"
        if required_any:
            assert any(token in text for token in required_any), (
                f"{command!r} must carry a hint naming one of {required_any!r}; got {text!r}"
            )


def _deps(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    truncated: bool = False,
) -> ExecRunDeps:
    """Build an injected ``ExecRunDeps`` whose runner returns a fixed adapter.

    Injecting the runner keeps every handler call free of a real
    subprocess: the tests exercise the policy and the result text only.
    """

    def _run(
        _argv: list[str],
        _cwd: Path,
        timeout_seconds: float | None,
    ) -> exec_completed_process._CompletedProcessAdapter:
        del timeout_seconds
        return exec_completed_process._CompletedProcessAdapter(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            truncated=truncated,
        )

    return ExecRunDeps(runner=_run)


def _result_text(result: ToolResult) -> str:
    """Return the text of the handler's first ToolContent block."""
    content = result.content[0]
    assert isinstance(content, ToolContent), (
        f"the handler must answer with a ToolContent block; got {content!r}"
    )
    return content.text
