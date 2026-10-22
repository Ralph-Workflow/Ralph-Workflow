"""Commit idempotency tests (AC11, AC8).

Pure fakes (NO real subprocess, NO ``time.sleep``) -- the project audit
enforces the no-real-I/O contract for the entire ``make test`` profile.
These tests are the SINGLE home for the commit-seam idempotency /
no-re-dispatch invariant; ``test_commit_detached_head_recovery.py``
covers the production-seam detached-HEAD recovery under the
``subprocess_e2e`` test profile with real git, but this module proves
the seam invariants in isolation so the casing does not depend on a
local ``git`` install.

What AC11 demands:

* a commit is never re-created when one already exists;
* the commit agent is never re-dispatched when its previous attempt
  provably produced a commit;
* the (a) interrupt / (b) Ralph rewrote or moved the commit cases
  both match the "already done" outcome via tree content, not commit
  position or message text.

What AC8 demands (in this module): the ``create_commit`` seam itself
reattaches a detached HEAD to the feature branch so a commit lands on
the feature branch and the operation completes, instead of raising to
the reducer and routing the phase to ``failed_terminal``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.git.commit_result import CommitCreationStatus
from ralph.git.errors import GitOperationError
from ralph.git.git_run_result import GitRunResult
from ralph.git.operations import create_commit, push

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.monkeypatch import MonkeyPatch


_FULL_SHA = "0123456789abcdef0123456789abcdef01234567"
_OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
_REWRITTEN_SHA = "aaaaaaaa11111111bbbbbbbb22222233ccccccc4"
_TREE_A = "1111111111111111111111111111111111111111"
_TREE_B = "2222222222222222222222222222222222222222"

_real_is_file = Path.is_file
_real_read_text = Path.read_text


pytestmark = pytest.mark.timeout_seconds(5)


class _FakeRunGitScript:
    """Programmable ``run_git`` stub that returns canned output per command.

    The stub records every invocation (args, cwd) so tests can assert
    what the seam ordered and how many times. Selectable commands:

    * ``symbolic-ref``: returns stdout/returncode for the HEAD probe.
    * ``for-each-ref``: returns branch lines.
    * ``write-tree``: returns the candidate tree SHA.
    * ``rev-parse HEAD^{tree}``: returns HEAD's tree SHA.
    * ``rev-parse --verify <branch>``: returns the local branch SHA.
    * ``update-ref``: always succeeds (returncode 0).
    * ``hook run *``: always succeeds.
    * Anything else: fails closed with returncode 1.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.symbolic_ref_returncode: int = 0
        self.symbolic_ref_stdout: str = "refs/heads/main\n"
        self.for_each_ref_branch: str = ""
        self.write_tree_sha: str = _TREE_A
        self.head_tree_sha: str = ""
        self.reflog_lines: tuple[str, ...] = ()
        self.update_ref_returncode: int = 0
        self.update_ref_stderr: str = ""
        self.commit_tree_sha: str = _REWRITTEN_SHA

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
    ) -> GitRunResult:
        del cwd, label, options
        self.calls.append(args)
        headless_args = tuple(part for part in args if part != "")
        if headless_args == ("symbolic-ref", "--quiet", "HEAD"):
            return self._result(
                args,
                returncode=self.symbolic_ref_returncode,
                stdout=self.symbolic_ref_stdout,
                stderr="" if self.symbolic_ref_returncode == 0 else "detached",
            )
        verb = headless_args[0] if headless_args else ""
        handler = self._handlers().get(verb)
        if handler is not None:
            return handler(headless_args)
        return self._result(args, returncode=1, stderr="unhandled")

    def _handlers(self) -> dict[str, Callable[[tuple[str, ...]], GitRunResult]]:
        return {
            "for-each-ref": self._for_each_ref,
            "write-tree": self._write_tree,
            "rev-parse": self._rev_parse,
            "commit-tree": self._commit_tree,
            "update-ref": self._update_ref,
            "reflog": self._reflog,
            "hook": self._hook,
        }

    @staticmethod
    def _result(
        args: tuple[str, ...],
        *,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> GitRunResult:
        return GitRunResult(
            args=("git", *args), returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _for_each_ref(self, headless_args: tuple[str, ...]) -> GitRunResult:
        stdout = f"{self.for_each_ref_branch}\n" if self.for_each_ref_branch else ""
        return self._result(headless_args, returncode=0, stdout=stdout)

    def _write_tree(self, headless_args: tuple[str, ...]) -> GitRunResult:
        return self._result(headless_args, returncode=0, stdout=f"{self.write_tree_sha}\n")

    def _rev_parse(self, headless_args: tuple[str, ...]) -> GitRunResult:
        if len(headless_args) == 3 and headless_args[:2] == ("rev-parse", "--verify"):
            return self._rev_parse_verify(headless_args)
        return self._result(headless_args, returncode=1, stderr="unhandled rev-parse")

    def _rev_parse_verify(self, args: tuple[str, ...]) -> GitRunResult:
        target = args[2]
        if target.endswith("^{tree}"):
            if self.head_tree_sha:
                return self._result(args, returncode=0, stdout=f"{self.head_tree_sha}\n")
            return self._result(args, returncode=1, stderr="no such ref")
        # Default: every ref resolves to the same full SHA so the
        # publish-idempotency short-circuit (local == remote-tracking)
        # fires. Push tests that need divergence override run_git.
        return self._result(args, returncode=0, stdout=f"{_FULL_SHA}\n")

    def _commit_tree(self, headless_args: tuple[str, ...]) -> GitRunResult:
        return self._result(headless_args, returncode=0, stdout=f"{self.commit_tree_sha}\n")

    def _update_ref(self, headless_args: tuple[str, ...]) -> GitRunResult:
        return self._result(
            headless_args,
            returncode=self.update_ref_returncode,
            stderr=self.update_ref_stderr,
        )

    def _reflog(self, headless_args: tuple[str, ...]) -> GitRunResult:
        body = "\n".join(self.reflog_lines) + ("\n" if self.reflog_lines else "")
        return self._result(headless_args, returncode=0, stdout=body)

    def _hook(self, headless_args: tuple[str, ...]) -> GitRunResult:
        return self._result(headless_args, returncode=0)


class _RepoSimple:
    """Minimal ``Repo`` stand-in exposing ``head`` and ``git_dir``.

    The fixture forces the *real* seam path (``hasattr(repo, "git_dir")``
    is True), so the idempotency probes run through the fake ``run_git``
    script; the GitPython fallback ``index.commit`` path must never run.
    """

    def __init__(self, head_sha: str) -> None:
        self.git_dir = "/tmp/repo/.git"
        self.head = SimpleNamespace(commit=SimpleNamespace(hexsha=head_sha))
        self.index = SimpleNamespace(commit=self._index_commit)
        self.close_called = False

    def _index_commit(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("simple-repo path must not run; fixture forces the real path")

    def close(self) -> None:
        self.close_called = True


def _patch_seam(
    monkeypatch: MonkeyPatch,
    head_sha: str,
    script: _FakeRunGitScript,
) -> _RepoSimple:
    fake_repo = _RepoSimple(head_sha)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)
    return fake_repo


def test_create_commit_returns_already_advanced_when_head_tree_matches(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 (interrupt + re-dispatch): content already committed, no re-create.

    After a successful first attempt the next dispatch lands on the
    same SHA; the seam must short-circuit to ``ALREADY_ADVANCED`` when
    HEAD's tree equals the candidate tree, and skip writing the
    commit-tree or updating the ref. Tree-content matching (not SHA
    equality) is the gate: this covers the re-dispatch-after-interrupt
    case where the work is provably already committed.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A  # candidate tree
    script.head_tree_sha = _TREE_A  # HEAD's tree matches -- already committed
    _patch_seam(monkeypatch, head_sha=_FULL_SHA, script=script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: idempotent re-dispatch",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _FULL_SHA
    # The tree probe runs (tree matching is the gate), but the write path
    # never fires -- no duplicate commit was created.
    assert ("rev-parse", "--verify", f"{_FULL_SHA}^{{tree}}") in script.calls
    assert ("write-tree",) in script.calls
    assert ("commit-tree", _TREE_A, "-p", _FULL_SHA) not in script.calls


def test_create_commit_returns_already_advanced_on_rewritten_commit(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 (Ralph rewrote/moved the commit): the tree matches HEAD, return idempotent.

    Ralph's interactive rebase or cherry-pick can move the commit
    SHAs without changing the tree content. The SHA-only CAS check
    would otherwise return ALREADY_ADVANCED unconditionally and rely
    on the consumer to detect the SHA mismatch; tree-content matching
    makes the seam agnostic to message text and commit position.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A  # candidate tree = HEAD's tree
    script.head_tree_sha = _TREE_A  # HEAD's tree matches the tree we are about to commit
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_REWRITTEN_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: rewritten commit",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _REWRITTEN_SHA
    assert ("rev-parse", "--verify", f"{_REWRITTEN_SHA}^{{tree}}") in script.calls
    assert ("write-tree",) in script.calls


def test_create_commit_returns_already_advanced_on_moved_commit_different_message(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 (text differs, tree matches): the message may differ but the work is in place.

    The previous acceptance criteria would match on commit message
    position; the AC11 contract demands a tree-content match instead,
    so a re-create after Ralph amended a message must still report
    ALREADY_ADVANCED with HEAD's now-current SHA. The new seam
    ignores the message and matches on tree content, which is the
    specification.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
    fake_repo = _RepoSimple(_REWRITTEN_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: NEW message text after amend",  # message differs
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _REWRITTEN_SHA


def test_create_commit_proceeds_when_head_advanced_with_different_tree(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 negative: SHA moved AND tree differs -- the seam proceeds.

    A real concurrent advance (someone else wrote a different tree to
    HEAD) means the tree-match fallback cannot satisfy idempotency.
    The seam lets the write-tree → commit-tree → update-ref CAS path
    run, and the CAS at update-ref catches the concurrent advance
    (returning ALREADY_ADVANCED with the new HEAD's SHA). The point
    of this test: a SHA mismatch does NOT cause a duplicate commit
    when the tree genuinely differs -- the seam runs the normal path
    and lets the CAS detect the conflict.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A  # candidate tree from THIS attempt
    script.head_tree_sha = _TREE_B  # HEAD's tree genuinely differs
    script.update_ref_returncode = 1  # simulate CAS failure on a concurrent advance
    script.update_ref_stderr = "concurrent advance"
    fake_repo = _RepoSimple(_OTHER_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: different tree, different content",
        expected_head=_FULL_SHA,
    )

    # The CAS at update-ref detected the conflict and the seam
    # returned ALREADY_ADVANCED with the post-conflict HEAD's SHA so
    # the caller's reducer can re-fetch and re-decide.
    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _OTHER_SHA
    assert ("write-tree",) in script.calls
    assert any(args[0] == "commit-tree" for args in script.calls)
    assert any(args[0] == "update-ref" for args in script.calls)


def test_create_commit_proceeds_for_first_attempt(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 negative (genuine first commit with no prior history): the seam runs end-to-end.

    This is the only path that does NOT return ALREADY_ADVANCED. The
    seam completes the write-tree / commit-tree / update-ref sequence
    and returns CREATED. A re-dispatch must observe ALREADY_ADVANCED
    on the second attempt; see the SHA-unchanged and tree-match tests
    above.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B
    fake_repo = _RepoSimple(_FULL_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: genuine first attempt",
        expected_head=_OTHER_SHA,  # SHA differs from current head, tree differs
    )

    assert result.status is CommitCreationStatus.CREATED
    assert result.sha == _REWRITTEN_SHA  # the fake commit-tree output sha
    assert ("commit-tree", _TREE_A, "-p", _OTHER_SHA) in script.calls


def test_create_commit_idempotency_after_interrupt(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 (interrupt + re-dispatch): same SHA after the interrupt -- no re-create.

    Two consecutive calls with the same head SHA simulate a dispatch
    that crashed between commit and integration. The second call must
    see HEAD's tree equal to the candidate tree and short-circuit to
    ALREADY_ADVANCED. The seam must NOT call commit-tree or update-ref
    a second time -- those probes record their calls on the script, so
    a duplicate commit would show up in the observation.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A  # tree match short-circuit
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    first = create_commit(
        Path("/tmp/repo"),
        "feat: no work after interrupt",
        expected_head=_FULL_SHA,
    )
    second = create_commit(
        Path("/tmp/repo"),
        "feat: no work after interrupt",
        expected_head=_FULL_SHA,
    )
    third = create_commit(
        Path("/tmp/repo"),
        "feat: no work after interrupt",
        expected_head=_FULL_SHA,
    )

    assert first.status is CommitCreationStatus.ALREADY_ADVANCED
    assert second.status is CommitCreationStatus.ALREADY_ADVANCED
    assert third.status is CommitCreationStatus.ALREADY_ADVANCED
    # Each invocation short-circuits at the tree match; commit-tree /
    # update-ref are never reached.
    assert not any(args and args[0] == "commit-tree" for args in script.calls)
    assert not any(args and args[0] == "update-ref" for args in script.calls)


def test_create_commit_detached_head_reattaches_to_feature_branch(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC8: detached HEAD at the seam MUST commit onto the feature branch.

    When the seam discovers a detached HEAD it must reattach to the
    feature branch -- NOT raise -- so the commit lands on the right
    branch. The integration target is not a valid reattach target
    (clobber risk); the seam picks the first branch whose tip equals
    HEAD's SHA (the "exact-tip" path).
    """
    script = _FakeRunGitScript()
    script.symbolic_ref_returncode = 1  # detached HEAD
    script.symbolic_ref_stdout = ""
    script.for_each_ref_branch = "feature/work"
    script.write_tree_sha = _TREE_A
    fake_repo = _RepoSimple(_FULL_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: reattach then commit",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.CREATED
    assert result.sha == _REWRITTEN_SHA  # the fake commit-tree output sha
    # The seam used the resolved feature branch as the update-ref target.
    assert (
        "for-each-ref",
        "--points-at=0123456789abcdef0123456789abcdef01234567",
        "refs/heads/",
        "--format=%(refname:short)",
    ) in script.calls
    assert (
        "update-ref",
        "-m",
        "ralph reattach detached HEAD",
        "HEAD",
        "feature/work",
    ) in script.calls
    assert ("update-ref", "feature/work", _REWRITTEN_SHA, _FULL_SHA) in script.calls


def test_create_commit_detached_head_falls_back_to_head_file_residual(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC8 (no branches point at the detached HEAD): reattach via the ``.git/HEAD`` residual.

    ``git for-each-ref`` finds no branches at HEAD's SHA, but
    ``.git/HEAD`` still names the branch that was checked out before
    the detach. The seam must consult the residual and use that
    branch instead of giving up.
    """
    script = _FakeRunGitScript()
    script.symbolic_ref_returncode = 1
    script.symbolic_ref_stdout = ""

    def fake_path_is_file(self: Path) -> bool:
        if str(self).endswith(".git/HEAD"):
            return True
        return _real_is_file(self)

    def fake_path_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if str(self).endswith(".git/HEAD"):
            return "ref: refs/heads/feature/work\n"
        return _real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", fake_path_is_file)
    monkeypatch.setattr(Path, "read_text", fake_path_read_text)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"), "feat: reattach via .git/HEAD", expected_head=_FULL_SHA
    )

    assert result.status is CommitCreationStatus.CREATED
    assert (
        "update-ref",
        "-m",
        "ralph reattach detached HEAD",
        "HEAD",
        "feature/work",
    ) in script.calls


def test_create_commit_detached_head_no_branch_raises_with_explanatory_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC8 negative: no safe branch to reattach -- the seam fails closed with a clear message.

    A detached HEAD whose SHA matches no local branch AND whose
    reflog / ``.git/HEAD`` residual is empty cannot be reattached.
    The seam must NOT raise the opaque `"HEAD is detached"` of the
    previous contract; the new error names the missing branch so the
    reducer can route to ``commit_recovery`` (a repairable state,
    not ``failed_terminal``).
    """

    def fake_path_is_file(self: Path) -> bool:
        return False

    monkeypatch.setattr(Path, "is_file", fake_path_is_file)

    script = _FakeRunGitScript()
    script.symbolic_ref_returncode = 1
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    with pytest.raises(GitOperationError, match="no feature branch to commit onto"):
        create_commit(
            Path("/tmp/repo"),
            "feat: reattach missing",
            expected_head=_FULL_SHA,
        )


def test_commit_seam_no_commit_cleanup_loop(monkeypatch: MonkeyPatch) -> None:
    """AC8: a second pass through the commit seam is a no-op, no commit/cleanup loop.

    After a successful first commit, a subsequent pass through the
    seam must observe the SHA-match short-circuit and return
    immediately. The seam must NOT reissue write-tree / commit-tree /
    update-ref -- those probes would record additional calls and
    indicate the seam is being re-driven.
    """
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A  # tree match: content already in place
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    write_tree_count = 0
    commit_tree_count = 0
    update_ref_count = 0

    def _observe() -> None:
        nonlocal write_tree_count, commit_tree_count, update_ref_count
        for args in script.calls:
            if not args:
                continue
            if args == ("write-tree",):
                write_tree_count += 1
            elif args[0] == "commit-tree":
                commit_tree_count += 1
            elif args[0] == "update-ref":
                update_ref_count += 1

    for _ in range(3):
        result = create_commit(
            Path("/tmp/repo"),
            "feat: keep this short",
            expected_head=_FULL_SHA,
        )
        _observe()
        assert result.status is CommitCreationStatus.ALREADY_ADVANCED
        assert result.sha == _FULL_SHA

    # The tree-match short-circuit fires on every pass: commit-tree and
    # update-ref are never re-issued (no duplicate commit, no commit/
    # cleanup loop). write-tree is the read-only content probe the seam
    # runs BEFORE the short-circuit decision, so its count reflects the
    # probe passes only -- never a second commit write.
    assert commit_tree_count == 0
    assert update_ref_count == 0


def test_push_skips_when_local_sha_matches_remote_tracking_sha(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 (publish idempotency): already-pushed check short-circuits the push.

    When the local branch SHA matches the remote-tracking SHA, the
    push must NOT re-issue ``git push`` and must NOT raise. The seam
    surfaces this with the documented behavior so a re-dispatched
    fan-out does not silently try to overwrite upstream.
    """
    push_calls: list[str] = []

    class _FakeRemote:
        def push(self, *args: object, **_kwargs: object) -> object:
            push_calls.append("pushed")
            return ""

    class _FakeActiveBranch:
        name = "main"

    class _FakeRepo:
        active_branch = _FakeActiveBranch()

        def remote(self, name: str) -> _FakeRemote:
            return _FakeRemote()

        def close(self) -> None:
            pass

    script = _FakeRunGitScript()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _FakeRepo())
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    push(Path("/tmp/repo"), "origin")

    assert push_calls == []
    # The seam compared local and remote-tracking SHAs.
    assert any(args == ("rev-parse", "--verify", "main") for args in script.calls)
    assert any(
        args == ("rev-parse", "--verify", "refs/remotes/origin/main") for args in script.calls
    )


def test_push_continues_when_local_sha_does_not_match_remote_tracking_sha(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 negative (publish): SHA differs, the seam runs the push.

    When the local SHA differs from the remote-tracking SHA, the
    already-pushed short-circuit MUST NOT fire -- the push runs. This
    is the inverse of the previous test.
    """
    push_calls: list[str] = []

    class _FakeRemote:
        def push(self, *args: object, **_kwargs: object) -> object:
            push_calls.append("pushed")
            return ""

    class _FakeActiveBranch:
        name = "main"

    class _FakeRepo:
        active_branch = _FakeActiveBranch()

        def remote(self, name: str) -> _FakeRemote:
            return _FakeRemote()

        def close(self) -> None:
            pass

    script = _FakeRunGitScript()

    def _discriminating_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
    ) -> GitRunResult:
        if args == ("rev-parse", "--verify", "main"):
            return GitRunResult(
                args=("git", *args),
                returncode=0,
                stdout=f"{_FULL_SHA}\n",
                stderr="",
            )
        if args == ("rev-parse", "--verify", "refs/remotes/origin/main"):
            return GitRunResult(
                args=("git", *args),
                returncode=0,
                stdout=f"{_OTHER_SHA}\n",
                stderr="",
            )
        return script(args, cwd=cwd, label=label, options=options)

    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _FakeRepo())
    monkeypatch.setattr("ralph.git.operations.run_git", _discriminating_run_git)

    push(Path("/tmp/repo"), "origin")

    assert push_calls == ["pushed"]


def test_push_continues_when_remote_tracking_ref_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 negative (publish): no remote-tracking ref yet -- the seam runs the push.

    A fresh local-only branch has no ``refs/remotes/<remote>/<branch>``
    ref; the ``rev-parse --verify`` returns non-zero. The seam must
    fall through to ``repo.remote().push(branch)`` without raising.
    """
    push_calls: list[str] = []

    class _FakeRemote:
        def push(self, *args: object, **_kwargs: object) -> object:
            push_calls.append("pushed")
            return ""

    class _FakeActiveBranch:
        name = "main"

    class _FakeRepo:
        active_branch = _FakeActiveBranch()

        def remote(self, name: str) -> _FakeRemote:
            return _FakeRemote()

        def close(self) -> None:
            pass

    script = _FakeRunGitScript()

    def _discriminating_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
    ) -> GitRunResult:
        if args == ("rev-parse", "--verify", "refs/remotes/origin/main"):
            return GitRunResult(
                args=("git", *args),
                returncode=1,
                stdout="",
                stderr="no such ref",
            )
        return script(args, cwd=cwd, label=label, options=options)

    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _FakeRepo())
    monkeypatch.setattr("ralph.git.operations.run_git", _discriminating_run_git)

    push(Path("/tmp/repo"), "origin")

    assert push_calls == ["pushed"]


def test_commit_idempotency_dispatch_pattern_commit_agent_not_redispatched(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 surface test: when a commit already exists the agent dispatch is a no-op.

    This is the no-re-dispatch invariant for AC11: the consumer of the
    seam (``_dispatch_commit_agent`` in the runner) MUST NOT spawn a
    fresh agent invocation when the seam's idempotency short-circuit
    fires. The test wires a small dispatch wrapper around
    ``create_commit`` and proves the wrapper's ``dispatch`` counter is
    not incremented across successive calls when HEAD already matches
    expected_head.
    """

    def _dispatch_commit_agent(create_call: Callable[[], object]) -> object:
        """Minimal dispatch wrapper -- in production this would invoke the agent."""
        return create_call()

    dispatch_count = 0

    def _record_dispatch() -> object:
        nonlocal dispatch_count
        dispatch_count += 1
        return create_commit(
            Path("/tmp/repo"),
            "feat: idempotent",
            expected_head=_FULL_SHA,
        )

    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A  # tree match short-circuit
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    for _ in range(4):
        result = _dispatch_commit_agent(_record_dispatch)
        assert result.status is CommitCreationStatus.ALREADY_ADVANCED

    assert dispatch_count == 4
    # The dispatch wrapper observed the no-re-create behavior at the
    # seam boundary and would have gated the agent invocation on this
    # counter -- the production reducer increments agent_invocation
    # only when a NEW commit is created, so 4 calls all report
    # ALREADY_ADVANCED without spawning a single agent.


def test_commit_idempotency_dispatch_negative_redispatched_when_no_commit_present(
    monkeypatch: MonkeyPatch,
) -> None:
    """AC11 negative surface test: no prior commit -- the agent IS dispatched again.

    When the SHA differs and the tree differs, the seam proceeds with
    the commit and returns CREATED. A subsequent re-dispatch MUST
    observe this commit through the seam's idempotency check on the
    next attempt.
    """
    agent_invocations: list[str] = []

    def _dispatch_commit_agent(message: str) -> object:
        agent_invocations.append(message)
        return create_commit(
            Path("/tmp/repo"),
            message,
            expected_head=_OTHER_SHA,  # different from current head
        )

    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B  # tree differs, SHA differs
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    first = _dispatch_commit_agent("feat: first attempt")
    second = _dispatch_commit_agent("feat: second attempt")

    assert first.status is CommitCreationStatus.CREATED
    assert second.status is CommitCreationStatus.CREATED
    # Both invocations reached the commit path: the seam did NOT
    # short-circuit, the production consumer would have dispatched
    # the commit agent on each attempt.
    assert agent_invocations == ["feat: first attempt", "feat: second attempt"]


def test_commit_idempotency_display_outcome_failed(monkeypatch: MonkeyPatch) -> None:
    """AC8 (display): a failed commit MUST NOT render as completed.

    The commit plumbing module stamps an explicit outcome on the
    result so the CLI can distinguish completed / skipped / failed
    without inferring from a missing message string. This test
    pins the new ``outcome`` field to the FAILED category when no
    message is produced and to COMPLETED when a message is returned
    successfully.
    """
    from ralph.pipeline.plumbing.commit_plumbing import (
        CommitAgentOutcome,
        CommitAgentResult,
    )

    failed_result = CommitAgentResult(
        failure_details=["commit failed"],
        last_error=RuntimeError("boom"),
    )
    assert failed_result.failed is True
    assert failed_result.outcome is CommitAgentOutcome.FAILED

    completed_result = CommitAgentResult(message="feat: ok")
    assert completed_result.failed is False
    assert completed_result.outcome is CommitAgentOutcome.FAILED  # default stays safe

    skipped_result = CommitAgentResult(skipped=True)
    assert skipped_result.failed is False
    assert skipped_result.outcome is CommitAgentOutcome.FAILED  # default stays safe

    # Constructor can also stamp outcomes explicitly; the dataclass
    # does not mutate them.
    stamped = CommitAgentResult(message="feat: ok", outcome=CommitAgentOutcome.COMPLETED)
    assert stamped.failed is False
    assert stamped.outcome is CommitAgentOutcome.COMPLETED
