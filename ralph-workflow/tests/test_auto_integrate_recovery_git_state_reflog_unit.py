"""Pure unit tests for the detached-HEAD reflog recovery seam.

These tests inject ``run_git`` results via ``monkeypatch`` instead of
spawning a real Git subprocess.  They exercise the two fail-closed
invariants identified during the architectural review:

1. A malformed newest checkout/switch reflog entry must NOT fall through
   to an older valid one.  The parser must distinguish "irrelevant
   entry" (commit/merge/cherry-pick -- continue scanning) from
   "malformed checkout entry" (no `` to `` separator or empty
   destination -- fail closed, do not scan older entries).

2. A successful-but-empty ``git rev-parse --verify`` output for either
   the candidate branch tip OR the detached HEAD must be treated as
   UNKNOWN.  The pre-fix implementation accepted two empty strings as
   equal and authorized a reattachment (the ``"" == ""`` trap).

The two regressions together pin invariant 10: an UNKNOWN observation
must never authorize forward progress in recovery.  All other
contract behaviour (SHA destination, mismatched tip, real-Git
reattach, work-preservation) is exercised by the subprocess-E2E
counterpart ``test_auto_integrate_recovery_git_state_reflog.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline._auto_integrate_recovery_git_state import detached_branch_name

if TYPE_CHECKING:
    import pytest


def _reflog(*lines: str) -> GitRunResult:
    """Build a reflog ``--format=%H%x00%gs -n 50`` result from lines."""
    return GitRunResult(
        args=("git", "reflog", "--format=%H%x00%gs", "-n", "50"),
        returncode=0,
        stdout="\n".join(lines) + "\n",
        stderr="",
    )


def _ok(stdout: str = "") -> GitRunResult:
    return GitRunResult(args=("git",), returncode=0, stdout=stdout, stderr="")


def _err(returncode: int = 1) -> GitRunResult:
    return GitRunResult(args=("git",), returncode=returncode, stdout="", stderr="")


def _stub(
    reflog_result: GitRunResult,
    *,
    show_ref: GitRunResult | None = None,
    rev_parse_branch: GitRunResult | None = None,
    rev_parse_head: GitRunResult | None = None,
):
    """Build a ``run_git`` stub for one or more of the four observed commands.

    The four seams are the reflog read, the candidate branch ``show-ref``,
    the candidate branch tip ``rev-parse``, and the detached HEAD
    ``rev-parse``.  Any seam left as ``None`` falls through to a
    default non-zero exit (so an unintended call is loud).
    """

    def _run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        a = tuple(args)
        if a and a[0] == "reflog":
            return reflog_result
        if a and a[0] == "show-ref":
            return show_ref if show_ref is not None else _err()
        if a and a[0] == "rev-parse" and "HEAD" in a and "refs/heads" not in " ".join(a):
            return rev_parse_head if rev_parse_head is not None else _err()
        if a and a[0] == "rev-parse" and "refs/heads" in " ".join(a):
            return rev_parse_branch if rev_parse_branch is not None else _err()
        return _err()

    return _run_git


class TestDetachedBranchNameFailClosed:
    """Fail-closed invariants for the reflog-driven detached-HEAD recovery."""

    def test_malformed_empty_destination_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Newest checkout: ``moving from main to `` (empty destination).

        An older entry names ``feature`` whose tip equals detached HEAD.
        The pre-fix parser conflated the malformed newest entry with an
        irrelevant entry (both returned ``None``) and continued scanning,
        returning ``"feature"``.  The fix must stop on the malformed
        newest entry and return ``None``.
        """
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main to \n"  # malformed: empty dest
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_missing_to_separator_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Newest checkout: ``moving from main`` (no `` to `` separator).

        Half-line subject with no destination.  Pre-fix code conflated
        this with an irrelevant commit/merge entry and continued scanning.
        Fix must fail closed.
        """
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main\n"  # malformed: no " to "
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_whitespace_destination_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Newest checkout: ``moving from main to    `` (whitespace-only dest).

        Whitespace-only destination is also a malformed newest entry.
        Pre-fix code stripped and returned ``None`` for the destination,
        then continued scanning.  Fix must fail closed.
        """
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main to    \n"  # malformed: whitespace
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_switch_branch_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Newest ``switch branch to `` (empty dest) is also a fail-closed boundary.

        Same contract as ``checkout:`` -- a malformed newest entry must
        not be skipped in favor of an older valid one.
        """
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00switch branch to \n{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_irrelevant_entries_before_checkout_are_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commit/merge/cherry-pick lines before the first checkout MUST be skipped.

        Sanity check on the contract: a non-checkout line is "irrelevant"
        and the scan continues to older lines.  The valid checkout
        below the irrelevant lines is the recovery observation.
        """
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00commit: add some work\n"
            f"{sha}\x00merge: merge feature into main\n"
            f"{sha}\x00cherry-pick: pick a commit\n"
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) == "feature"

    def test_empty_branch_tip_and_empty_head_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both rev-parses return empty stdout.

        Pre-fix code stripped to empty strings, compared them as equal
        (``"" == ""``), and returned the candidate.  The fix must
        require non-empty OID output from BOTH probes.
        """
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(""),  # empty stdout
                rev_parse_head=_ok(""),  # empty stdout
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_empty_branch_tip_only_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the branch tip rev-parse is empty.

        Even if detached HEAD is real, an empty branch-tip observation is
        UNKNOWN and must fail closed (we cannot prove the branch tip
        equals detached HEAD without a non-empty observation).
        """
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(""),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_empty_head_only_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the HEAD rev-parse is empty.

        Symmetric: an empty detached-HEAD observation is UNKNOWN.  We
        cannot authorize a reattachment without a non-empty HEAD OID to
        compare against.
        """
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(""),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_whitespace_only_branch_tip_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace-only branch tip is also UNKNOWN.

        The pre-fix code did ``tip_check.stdout.strip()`` which
        converted whitespace to ``""``, then compared as equal to
        another stripped empty string.  The fix must treat whitespace
        as UNKNOWN too.
        """
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("   \n"),
                rev_parse_head=_ok("   \n"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_exact_tip_match_returns_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Branch tip EQUALS detached HEAD -- recovery returns the branch name."""
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok(sha),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) == "feature"

    def test_sha_destination_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SHA destination is an explicit detached checkout -- return None."""
        sha = "a" * 40
        sha_dest = "b" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to {sha_dest}\n"
        # show-ref FAILS because the "branch" is actually a SHA
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_err(),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_mismatched_tip_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Branch tip does not EQUAL detached HEAD -- return None."""
        sha = "a" * 40
        sha_other = "b" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok(sha),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha_other),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_rev_parse_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failed rev-parse (non-zero exit) is fail-closed."""
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_err(128),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None
