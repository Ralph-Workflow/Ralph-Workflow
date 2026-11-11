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
        self.update_ref_failures_remaining: int = 0
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
        return self._result(args, returncode=0, stdout=f"{_FULL_SHA}\n")

    def _commit_tree(self, headless_args: tuple[str, ...]) -> GitRunResult:
        return self._result(headless_args, returncode=0, stdout=f"{self.commit_tree_sha}\n")

    def _update_ref(self, headless_args: tuple[str, ...]) -> GitRunResult:
        if self.update_ref_failures_remaining > 0:
            self.update_ref_failures_remaining -= 1
            return self._result(
                headless_args,
                returncode=128,
                stderr=self.update_ref_stderr,
            )
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


def test_create_commit_recovers_stale_branch_ref_lock(monkeypatch: MonkeyPatch) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B
    script.update_ref_failures_remaining = 1
    script.update_ref_stderr = (
        "fatal: cannot lock ref 'refs/heads/main': Unable to create "
        "'/tmp/repo/.git/refs/heads/main.lock': File exists."
    )
    _patch_seam(monkeypatch, head_sha=_FULL_SHA, script=script)

    result = create_commit(
        Path("/tmp/repo"),
        "fix: recover stale branch lock",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.CREATED
    update_ref_calls = [call for call in script.calls if call and call[0] == "update-ref"]
    assert len(update_ref_calls) == 2


class _RepoSimple:
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
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
    _patch_seam(monkeypatch, head_sha=_FULL_SHA, script=script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: idempotent re-dispatch",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _FULL_SHA
    assert ("rev-parse", "--verify", f"{_FULL_SHA}^{{tree}}") in script.calls
    assert ("write-tree",) in script.calls
    assert ("commit-tree", _TREE_A, "-p", _FULL_SHA) not in script.calls


def test_create_commit_returns_already_advanced_on_rewritten_commit(
    monkeypatch: MonkeyPatch,
) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
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
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
    fake_repo = _RepoSimple(_REWRITTEN_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: NEW message text after amend",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _REWRITTEN_SHA


def test_create_commit_proceeds_when_head_advanced_with_different_tree(
    monkeypatch: MonkeyPatch,
) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B
    script.update_ref_returncode = 1
    script.update_ref_stderr = "concurrent advance"
    fake_repo = _RepoSimple(_OTHER_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: different tree, different content",
        expected_head=_FULL_SHA,
    )

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == _OTHER_SHA
    assert ("write-tree",) in script.calls
    assert any(args[0] == "commit-tree" for args in script.calls)
    assert any(args[0] == "update-ref" for args in script.calls)


def test_create_commit_proceeds_for_first_attempt(
    monkeypatch: MonkeyPatch,
) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B
    fake_repo = _RepoSimple(_FULL_SHA)
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    result = create_commit(
        Path("/tmp/repo"),
        "feat: genuine first attempt",
        expected_head=_OTHER_SHA,
    )

    assert result.status is CommitCreationStatus.CREATED
    assert result.sha == _REWRITTEN_SHA
    assert ("commit-tree", _TREE_A, "-p", _OTHER_SHA) in script.calls


def test_create_commit_idempotency_after_interrupt(
    monkeypatch: MonkeyPatch,
) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
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
    assert not any(args and args[0] == "commit-tree" for args in script.calls)
    assert not any(args and args[0] == "update-ref" for args in script.calls)


def test_create_commit_detached_head_reattaches_to_feature_branch(
    monkeypatch: MonkeyPatch,
) -> None:
    script = _FakeRunGitScript()
    script.symbolic_ref_returncode = 1
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
    assert result.sha == _REWRITTEN_SHA
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


def test_commit_seam_no_commit_review_loop(monkeypatch: MonkeyPatch) -> None:
    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_A
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

    assert commit_tree_count == 0
    assert update_ref_count == 0


def test_push_skips_when_local_sha_matches_remote_tracking_sha(
    monkeypatch: MonkeyPatch,
) -> None:
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
    assert any(args == ("rev-parse", "--verify", "main") for args in script.calls)
    assert any(
        args == ("rev-parse", "--verify", "refs/remotes/origin/main") for args in script.calls
    )


def test_push_continues_when_local_sha_does_not_match_remote_tracking_sha(
    monkeypatch: MonkeyPatch,
) -> None:
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

    def _dispatch_commit_agent(create_call: Callable[[], object]) -> object:
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
    script.head_tree_sha = _TREE_A
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    for _ in range(4):
        result = _dispatch_commit_agent(_record_dispatch)
        assert result.status is CommitCreationStatus.ALREADY_ADVANCED

    assert dispatch_count == 4


def test_commit_idempotency_dispatch_negative_redispatched_when_no_commit_present(
    monkeypatch: MonkeyPatch,
) -> None:
    agent_invocations: list[str] = []

    def _dispatch_commit_agent(message: str) -> object:
        agent_invocations.append(message)
        return create_commit(
            Path("/tmp/repo"),
            message,
            expected_head=_OTHER_SHA,
        )

    script = _FakeRunGitScript()
    script.write_tree_sha = _TREE_A
    script.head_tree_sha = _TREE_B
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_a, **_kw: _RepoSimple(_FULL_SHA))
    monkeypatch.setattr("ralph.git.operations.run_git", script)

    first = _dispatch_commit_agent("feat: first attempt")
    second = _dispatch_commit_agent("feat: second attempt")

    assert first.status is CommitCreationStatus.CREATED
    assert second.status is CommitCreationStatus.CREATED
    assert agent_invocations == ["feat: first attempt", "feat: second attempt"]


def test_commit_idempotency_display_outcome_failed(monkeypatch: MonkeyPatch) -> None:
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
    assert completed_result.outcome is CommitAgentOutcome.FAILED

    skipped_result = CommitAgentResult(skipped=True)
    assert skipped_result.failed is False
    assert skipped_result.outcome is CommitAgentOutcome.FAILED

    stamped = CommitAgentResult(message="feat: ok", outcome=CommitAgentOutcome.COMPLETED)
    assert stamped.failed is False
    assert stamped.outcome is CommitAgentOutcome.COMPLETED
