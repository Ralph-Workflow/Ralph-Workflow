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
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
import ralph.mcp.tools.exec as exec_tool
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
)
from ralph.mcp.tools.exec import (
    _GIT_READ_ONLY_SUBCOMMANDS,
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

# ─────────────────────────────────────────────────────────────────────────────
# Constant contract
# ─────────────────────────────────────────────────────────────────────────────


class TestGitReadOnlySubcommandsConstant:
    """Pin the whitelist content so a future refactor cannot silently add or
    drop a read-only subcommand without a deliberate test edit. The set is
    the single source of truth shared by the scanner in exec.py."""

    def test_whitelist_includes_canonical_read_only_subcommands(self) -> None:
        for subcommand in (
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
        ):
            assert subcommand in _GIT_READ_ONLY_SUBCOMMANDS, (
                f"expected {subcommand!r} in the read-only whitelist"
            )

    def test_whitelist_excludes_state_mutating_subcommands(self) -> None:
        for subcommand in (
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
            "worktree",
        ):
            assert subcommand not in _GIT_READ_ONLY_SUBCOMMANDS, (
                f"state-mutating subcommand {subcommand!r} must never be whitelisted"
            )

    # ─────────────────────────────────────────────────────────────────────────────
    # Low-level scanner: check_version_control
    # ─────────────────────────────────────────────────────────────────────────────

    class TestCheckVersionControlAllowed:
        """The scanner must allow whitelisted read-only git subcommands and
        leave non-git commands untouched."""

        @pytest.mark.parametrize(
            "command,args",
            [
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
            ],
        )
        def test_whitelisted_subcommand_is_allowed(self, command: str, args: list[str]) -> None:
            assert check_version_control(command, args) is None

        def test_git_with_global_flag_is_allowed(self) -> None:
            # ``-C <path>`` is a git global flag (path of the repo) and must be
            # skipped before the subcommand is parsed so the subcommand check
            # sees ``status``, not ``-C`` or ``.``.
            assert check_version_control("git", ["-C", ".", "status"]) is None

        def test_git_with_no_pager_global_flag_is_allowed(self) -> None:
            # ``-P`` / ``--no-pager`` are boolean global flags; they must be
            # skipped in place without consuming a following value.
            assert check_version_control("git", ["--no-pager", "log"]) is None
            assert check_version_control("git", ["-P", "log"]) is None

        def test_git_with_git_dir_global_flag_is_allowed(self) -> None:
            assert check_version_control("git", ["--git-dir=.git", "log"]) is None

        def test_git_with_work_tree_global_flag_is_allowed(self) -> None:
            assert check_version_control("git", ["--work-tree=/tmp/x", "status"]) is None

        def test_non_vcs_command_is_allowed(self) -> None:
            assert check_version_control("ls", ["-la"]) is None
            assert check_version_control("echo", ["hello"]) is None

        def test_github_url_argument_is_allowed(self) -> None:
            """A 'github.com' substring in an argument must not be misread as a
            git tool call: only a standalone ``git`` word in COMMAND position is
            a VCS invocation. (The existing URL-safe test in unsafe_exec covers
            the textual scanner; this test pins the same contract at the level
            of the segment-aware scanner.)"""
            assert check_version_control("echo", ["https://github.com/foo/bar"]) is None

        def test_file_named_gitignore_is_allowed(self) -> None:
            """'.gitignore' is a file, not a git command."""
            assert check_version_control("cat", [".gitignore"]) is None

    class TestCheckVersionControlDenied:
        """The scanner must deny any non-whitelisted git subcommand, bare git,
        and any hg / svn invocation."""

        @pytest.mark.parametrize(
            "command,args",
            [
                ("git", ["push"]),
                ("git", ["push", "origin", "main"]),
                ("git", ["stash"]),
                ("git", ["stash", "pop"]),
                ("git", ["checkout", "main"]),
                ("git", ["switch", "main"]),
                ("git", ["restore", "."]),
                ("git", ["commit", "-m", "msg"]),
                ("git", ["add", "."]),
                ("git", ["apply", "patch.diff"]),
                ("git", ["am", "patch"]),
                ("git", ["tag", "v1"]),
                ("git", ["branch", "x"]),
                ("git", ["merge", "feat"]),
                ("git", ["rebase", "main"]),
                ("git", ["reset", "--hard"]),
                ("git", ["fetch", "origin"]),
                ("git", ["pull", "origin", "main"]),
                ("git", ["clone", "url"]),
                ("git", ["init"]),
                ("git", ["config", "user.name", "x"]),
                ("git", ["remote", "add", "origin", "url"]),
                ("git", ["reflog"]),
                ("git", ["worktree", "add", "wt", "main"]),
                ("git", ["clean", "-fd"]),
                ("git", ["rm", "file"]),
                ("git", ["mv", "a", "b"]),
                ("git", ["bisect", "start"]),
            ],
        )
        def test_state_mutating_subcommand_is_denied(self, command: str, args: list[str]) -> None:
            reason = check_version_control(command, args)
            assert reason is not None
            assert "git" in reason.lower()

        def test_bare_git_without_subcommand_is_denied(self) -> None:
            # ``git`` alone (no subcommand) prints usage and exits; the old
            # contract denied it (per ``test_exec_blocks_git_command``) and the
            # new contract keeps that behaviour — fail closed when no subcommand
            # is determinable.
            reason = check_version_control("git", [])
            assert reason is not None
            assert "git" in reason.lower()

        def test_git_version_is_denied(self) -> None:
            # ``git --version`` is a read-only query but not in the whitelist —
            # the contract says fail-closed when no whitelisted subcommand is
            # determinable. ``--version`` is a git-level flag, not a subcommand.
            reason = check_version_control("git", ["--version"])
            assert reason is not None

        def test_git_c_with_mutating_subcommand_is_denied(self) -> None:
            # The ``-c alias.x=push`` global flag value contains a mutating
            # subcommand token; the scanner must skip the ``-c x=y`` pair and
            # evaluate the actual subcommand ``push``.
            reason = check_version_control("git", ["-c", "alias.x=push", "push"])
            assert reason is not None

        def test_hg_command_is_denied(self) -> None:
            assert check_version_control("hg", ["update"]) is not None

        def test_svn_command_is_denied(self) -> None:
            assert check_version_control("svn", ["commit"]) is not None

        def test_path_prefixed_git_with_mutating_subcommand_is_denied(self) -> None:
            # A full path to the git binary, like ``/usr/bin/git``, must be
            # treated the same as bare ``git``: the basename is what counts.
            reason = check_version_control("/usr/bin/git", ["push"])
            assert reason is not None

    # ─────────────────────────────────────────────────────────────────────────────
    # Diff flag guard — whitelisted diff must still reject output-writing flags
    # ─────────────────────────────────────────────────────────────────────────────

    class TestDiffFlagGuard:
        """Even when ``diff`` is whitelisted, output-writing and external-helper
        flags must be denied so ``git diff`` cannot be turned into a write."""

        @pytest.mark.parametrize(
            "args",
            [
                ["--output=/tmp/x"],
                ["--output", "/tmp/x"],
                ["-o", "/tmp/x"],
                ["-o=/tmp/x"],
                ["--ext-diff"],
                ["--textconv"],
                ["--convience-diff"],
            ],
        )
        def test_diff_flag_is_denied(self, args: list[str]) -> None:
            reason = check_version_control("git", ["diff", *args])
            assert reason is not None
            text = reason.lower()
            assert "diff" in text or "output" in text or "helper" in text

        def test_diff_stat_is_allowed(self) -> None:
            # ``--stat`` is read-only and must not trip the flag guard.
            assert check_version_control("git", ["diff", "--stat"]) is None

        def test_diff_name_only_is_allowed(self) -> None:
            assert check_version_control("git", ["diff", "--name-only"]) is None

        def test_diff_shortstat_is_allowed(self) -> None:
            assert check_version_control("git", ["diff", "--shortstat"]) is None

        def test_diff_staged_is_allowed(self) -> None:
            assert check_version_control("git", ["diff", "--staged"]) is None

        def test_diff_unified_is_allowed(self) -> None:
            assert check_version_control("git", ["diff", "--unified=3"]) is None

        def test_diff_diff_filter_is_allowed(self) -> None:
            assert check_version_control("git", ["diff", "--diff-filter=ACMRT"]) is None

    # ─────────────────────────────────────────────────────────────────────────────
    # Pipeline / substitution / script-level enforcement
    # ─────────────────────────────────────────────────────────────────────────────

    class TestPipelineEnforcement:
        """A whitelisted subcommand in one segment cannot mask a mutating
        subcommand in another segment of the same compound shell command."""

        def test_whitelisted_then_mutating_is_denied(self) -> None:
            with pytest.raises(CapabilityDeniedError, match="git"):
                apply_exec_policy("git", ["status"])
                # The text scan must catch ``git push`` even after ``git status``
                # was admitted by the segment-aware scan. We test the
                # textual / per-segment scan path here via the public handler.
                handle_exec_command(
                    MockSession({"ProcessExecBounded"}),
                    MockWorkspaceRoot(_tmp()),
                    {"command": "git status && git push origin main"},
                )

        def test_mutating_then_whitelisted_is_denied(self) -> None:
            with pytest.raises(CapabilityDeniedError, match="git"):
                handle_exec_command(
                    MockSession({"ProcessExecBounded"}),
                    MockWorkspaceRoot(_tmp()),
                    {"command": "git push origin main; git status"},
                )

        def test_whitelisted_only_in_pipeline_is_allowed(self) -> None:
            def _runner(
                argv: list[str],
                cwd: object,
                timeout_seconds: float | None,
            ) -> exec_completed_process._CompletedProcessAdapter:
                del argv, cwd, timeout_seconds
                return exec_completed_process._CompletedProcessAdapter(
                    stdout=b"clean", stderr=b"", returncode=0
                )

            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": "git status && git log -n 1"},
                deps=ExecRunDeps(runner=_runner),
            )
            assert result.is_error is False

    class TestScriptEnforcement:
        """The script-content scanner must reuse the same whitelist scanner so
        ``bash build.sh`` running only ``git status`` is allowed, but a script
        running ``git push`` is still denied."""

        def test_script_with_only_whitelisted_git_is_allowed(self, tmp_path: Path) -> None:
            script = tmp_path / "audit.sh"
            script.write_text("#!/bin/sh\ngit status\necho done\n")
            result = handle_unsafe_exec(
                MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
                MockWorkspaceRoot(tmp_path),
                {"command": "bash audit.sh"},
                _runner(stdout=b"clean"),
            )
            assert result.is_error is False

        def test_script_with_mutating_git_is_denied(self, tmp_path: Path) -> None:
            script = tmp_path / "deploy.sh"
            script.write_text("#!/bin/sh\ngit status\ngit push origin main\n")
            with pytest.raises(CapabilityDeniedError, match="git"):
                handle_unsafe_exec(
                    MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
                    MockWorkspaceRoot(tmp_path),
                    {"command": "bash deploy.sh"},
                )

    class TestCommandSubstitution:
        """The deep textual scan must catch a VCS command hidden inside
        ``$(...)`` / backtick substitutions and ``sh -c`` strings."""

        def test_command_substitution_mutating_git_is_denied(self, tmp_path: Path) -> None:
            with pytest.raises(CapabilityDeniedError, match="git"):
                handle_exec_command(
                    MockSession({"ProcessExecBounded"}),
                    MockWorkspaceRoot(tmp_path),
                    {"command": "echo $(git push origin main) | wc -c"},
                )

        def test_backtick_substitution_mutating_git_is_denied(self, tmp_path: Path) -> None:
            with pytest.raises(CapabilityDeniedError, match="git"):
                handle_exec_command(
                    MockSession({"ProcessExecBounded"}),
                    MockWorkspaceRoot(tmp_path),
                    {"command": "echo `git commit -m x` | cat"},
                )

        def test_sh_c_string_mutating_git_is_denied(self, tmp_path: Path) -> None:
            with pytest.raises(CapabilityDeniedError, match="git"):
                handle_exec_command(
                    MockSession({"ProcessExecBounded"}),
                    MockWorkspaceRoot(tmp_path),
                    {"command": ["sh", "-c", "git push origin main"]},
                )

    # ─────────────────────────────────────────────────────────────────────────────
    # Result-text hints — whitelisted git usage must mention the MCP endpoints
    # ─────────────────────────────────────────────────────────────────────────────

    class TestExecResultHints:
        """A successful exec that used a whitelisted git subcommand must carry a
        note pointing at the dedicated ``git_*`` MCP read tools in its result
        text so an agent reading the output learns that a dedicated endpoint
        exists."""

        def test_whitelisted_git_in_command_appends_git_mcp_hint(self) -> None:
            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": "git", "args": ["status"]},
                deps=ExecRunDeps(runner=_ok_runner(stdout=b"clean")),
            )
            assert result.is_error is False
            text = _first_text(result)
            # The git-hint must reference the dedicated MCP read tools.
            assert "git_status" in text
            assert "git_diff" in text
            assert "git_log" in text
            assert "git_show" in text

        def test_whitelisted_git_in_shell_command_appends_git_mcp_hint(self) -> None:
            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": "git status"},
                deps=ExecRunDeps(runner=_ok_runner(stdout=b"clean")),
            )
            assert result.is_error is False
            text = _first_text(result)
            assert "git_status" in text

        def test_non_git_command_does_not_emit_git_mcp_hint(self) -> None:
            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": "ls", "args": []},
                deps=ExecRunDeps(runner=_ok_runner(stdout=b"x")),
            )
            text = _first_text(result)
            assert "git_status" not in text
            assert "git_log" not in text

    class TestGrepHint:
        """A ``grep`` (or egrep / fgrep) head must run, and the result text
        must carry a warning that the MCP explore endpoint is more efficient."""

        @pytest.mark.parametrize(
            "command,args",
            [
                ("grep", ["-r", "needle", "."]),
                ("grep", ["needle", "file"]),
                ("egrep", ["needle", "file"]),
                ("fgrep", ["needle", "file"]),
            ],
        )
        def test_grep_variants_run_and_emit_explore_hint(
            self, command: str, args: list[str]
        ) -> None:
            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": command, "args": args},
                deps=ExecRunDeps(runner=_ok_runner(stdout=b"file.py:1:matched")),
            )
            assert result.is_error is False
            text = _first_text(result)
            # ``grep`` is never denied; the result must still execute.
            assert "matched" in text
            # The warning must mention the dedicated MCP explore endpoint.
            assert "grep_files" in text or "explore" in text.lower()

        def test_grep_command_is_not_denied(self) -> None:
            # Explicit deny guard: ``grep`` is NEVER blacklisted regardless of
            # args; only the explore hint is appended to the result text.
            result = handle_exec_command(
                MockSession({"ProcessExecBounded"}),
                MockWorkspaceRoot(_tmp()),
                {"command": "grep", "args": ["-r", "x", "."]},
                deps=ExecRunDeps(runner=_ok_runner(stdout=b"")),
            )
            assert result.is_error is False

    class TestUnsafeExecHints:
        """unsafe_exec / raw_exec must also append the git-hint and grep-hint
        notes to the result text, mirroring exec's behavior."""

        def test_whitelisted_git_appends_git_mcp_hint(self, tmp_path: Path) -> None:
            result = handle_unsafe_exec(
                MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
                MockWorkspaceRoot(tmp_path),
                {"command": "git status"},
                _runner(stdout=b"clean"),
            )
            assert result.is_error is False
            text = result.content[0].text if isinstance(result.content[0], ToolContent) else ""
            assert "git_status" in text
            assert "git_log" in text

        def test_grep_appends_explore_hint(self, tmp_path: Path) -> None:
            result = handle_unsafe_exec(
                MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
                MockWorkspaceRoot(tmp_path),
                {"command": "grep needle file"},
                _runner(stdout=b"file:1:needle"),
            )
            assert result.is_error is False
            text = result.content[0].text if isinstance(result.content[0], ToolContent) else ""
            assert "grep_files" in text or "explore" in text.lower()

    # ─────────────────────────────────────────────────────────────────────────────
    # Helpers


def _tmp() -> Path:
    """Return a stable Path-like sentinel for the workspace root in tests
    that never actually invoke a real subprocess."""
    import tempfile

    return Path(tempfile.mkdtemp(prefix="ralph-vcs-test-"))


def _ok_runner(stdout: bytes = b"") -> exec_tool.CommandRunner:
    """Build a fake runner that returns the given stdout and exit 0."""

    def _run(
        argv: list[str],
        cwd: object,
        timeout_seconds: float | None,
    ) -> exec_completed_process._CompletedProcessAdapter:
        del argv, cwd, timeout_seconds
        return exec_completed_process._CompletedProcessAdapter(
            stdout=stdout, stderr=b"", returncode=0
        )

    return _run


def _runner(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    truncated: bool = False,
    capture: list[object] | None = None,
) -> ExecRunDeps:
    """Mirror the unsafe_exec-test helper: an injected ExecRunDeps whose
    runner returns a fixed completed-process adapter."""

    def _run(
        _argv: list[str],
        _cwd: Path,
        timeout_seconds: float | None,
    ) -> exec_completed_process._CompletedProcessAdapter:
        if capture is not None:
            capture.append(timeout_seconds)
        return exec_completed_process._CompletedProcessAdapter(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            truncated=truncated,
        )

    return ExecRunDeps(runner=_run)


def _first_text(result: object) -> str:
    content = result.content[0]
    if isinstance(content, ToolContent):
        return content.text
    # Defensive: tests should always get a ToolContent back from the handler.
    return getattr(content, "text", str(content))
