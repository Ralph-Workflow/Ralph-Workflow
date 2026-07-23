"""Deterministic coverage for opt-in auto-integration remote pushes.

Remote enumeration and push outcomes are subprocess-boundary behavior, so the
tests inject ``run_git`` results rather than constructing repositories. One
parameterized aggregation test replaces the duplicated landing and recovery
setups: both paths call ``maybe_push_target`` after local success.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import auto_integrate_ff
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_push_updates_a_real_bare_remote(tmp_git_repo: Path, tmp_path: Path) -> None:
    """The Git boundary updates the target ref in a real bare repository."""
    bare = tmp_path / "origin.git"
    subprocess.run(
        ("git", "init", "--bare", str(bare)),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    branch = subprocess.run(
        ("git", "symbolic-ref", "--short", "HEAD"),
        cwd=tmp_git_repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    subprocess.run(
        ("git", "remote", "add", "origin", str(bare)),
        cwd=tmp_git_repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    summary = remote_push.push_branch_to_all_remotes(
        tmp_git_repo,
        branch,
        timeout_seconds=2.0,
    )
    remote_sha = subprocess.run(
        ("git", "rev-parse", f"refs/heads/{branch}"),
        cwd=bare,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    local_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=tmp_git_repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()

    assert remote_sha == local_sha
    assert summary == f"pushed {branch} to 1/1 remotes"


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> GitRunResult:
    return GitRunResult(
        args=("git", *args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _remote_runner(
    push_results: dict[str, int],
    calls: list[tuple[str, ...]],
) -> Callable[[Sequence[str]], GitRunResult]:
    def run_git(
        args: Sequence[str],
        **_kwargs: object,
    ) -> GitRunResult:
        call = tuple(args)
        calls.append(call)
        if call == ("remote",):
            return _result(call, stdout="origin\nbackup\n")
        remote = call[2]
        return _result(
            call,
            returncode=push_results[remote],
            stderr="unreachable" if push_results[remote] else "",
        )

    return run_git


def test_push_aggregates_every_remote_without_gating_local_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every remote is attempted and partial failure remains operator-visible."""
    cases = [
        ({"origin": 0, "backup": 0}, "pushed develop to 2/2 remotes"),
        (
            {"origin": 1, "backup": 0},
            "pushed develop to 1/2 remotes (origin failed)",
        ),
        ({"origin": 1, "backup": 1}, "pushed develop to 0/2 remotes"),
    ]

    for push_results, expected in cases:
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            remote_push,
            "run_git",
            _remote_runner(push_results, calls),
        )

        assert (
            remote_push.push_branch_to_all_remotes(
                Path("/repo"),
                "develop",
                timeout_seconds=2.5,
            )
            == expected
        )
        assert calls == [
            ("remote",),
            ("push", "--", "origin", "refs/heads/develop:refs/heads/develop"),
            ("push", "--", "backup", "refs/heads/develop:refs/heads/develop"),
        ]


def test_no_remotes_returns_canonical_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repository without remotes performs no push."""

    def run_git(
        args: Sequence[str],
        **_kwargs: object,
    ) -> GitRunResult:
        return _result(args, stdout="")

    monkeypatch.setattr(remote_push, "run_git", run_git)

    assert (
        remote_push.push_branch_to_all_remotes(
            Path("/repo"),
            "main",
            timeout_seconds=1.0,
        )
        == "no remotes configured"
    )


def _config(*, enabled: bool, timeout: float = 3.0) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_push_enabled": enabled,
                "auto_integrate_push_timeout_seconds": timeout,
            }
        }
    )


def test_successful_landing_records_push_summary_for_normal_and_recovery_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared hook annotates either landing path with the push outcome."""
    calls: list[tuple[Path, str, float]] = []

    def push(repo: Path, branch: str, *, timeout_seconds: float) -> str:
        calls.append((repo, branch, timeout_seconds))
        return "pushed release to 2/2 remotes"

    monkeypatch.setattr(auto_integrate_ff, "push_branch_to_all_remotes", push)
    state = RebaseState(
        last_action="rebased",
        last_target="release",
        fast_forwarded=True,
    )

    normal = auto_integrate_ff.maybe_push_target(
        _config(enabled=True),
        Path("/repo"),
        "release",
        state,
    )
    recovery = auto_integrate_ff.maybe_push_target(
        _config(enabled=True),
        Path("/repo"),
        "release",
        state,
    )

    assert normal.last_push == "pushed release to 2/2 remotes"
    assert recovery.last_push == "pushed release to 2/2 remotes"
    assert calls == [
        (Path("/repo"), "release", 3.0),
        (Path("/repo"), "release", 3.0),
    ]


def test_disabled_push_preserves_landing_without_contacting_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default-off hook returns the existing local-success record unchanged."""

    def unexpected_push(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("disabled push contacted a remote")

    monkeypatch.setattr(
        auto_integrate_ff,
        "push_branch_to_all_remotes",
        unexpected_push,
    )
    state = RebaseState(
        last_action="rebased",
        last_target="main",
        fast_forwarded=True,
    )

    assert (
        auto_integrate_ff.maybe_push_target(
            _config(enabled=False),
            Path("/repo"),
            "main",
            state,
        )
        is state
    )
