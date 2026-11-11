from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import PINNED_CONFIG_ARGS
from ralph.git.rebase import subprocess_executor
from ralph.git.rebase.subprocess_executor import SubprocessExecutor

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest

    from ralph.git.subprocess_runner import GitRunOptions


def test_rebase_executor_pins_hostile_config_per_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []

    def run_git(
        args: Sequence[str],
        *,
        options: GitRunOptions,
        **_kwargs: object,
    ) -> GitRunResult:
        observed.append((tuple(args), options.env))
        return GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess_executor, "run_git", run_git)

    result = SubprocessExecutor().execute(
        "git",
        (
            "rebase",
            "--no-autostash",
            "--no-autosquash",
            "--no-update-refs",
            "--empty=drop",
            "--",
            "develop",
            "feature",
        ),
        env={
            "GIT_DIR": "/hostile/repo",
            "GIT_WORK_TREE": "/hostile/tree",
            "GIT_INDEX_FILE": "/hostile/index",
            "GIT_COMMON_DIR": "/hostile/common",
            "SAFE_VALUE": "preserved",
        },
        cwd=Path("/repo"),
    )

    assert result.succeeded
    assert observed == [
        (
            (
                *PINNED_CONFIG_ARGS,
                "rebase",
                "--no-autostash",
                "--no-autosquash",
                "--no-update-refs",
                "--empty=drop",
                "--",
                "develop",
                "feature",
            ),
            {"SAFE_VALUE": "preserved"},
        )
    ]


def test_pinned_config_closes_each_hostile_config_hazard() -> None:
    expected = [
        ("rerere.enabled=false", "recorded resolution replay"),
        ("commit.gpgsign=false", "commit signing prompt"),
        ("tag.gpgsign=false", "tag signing prompt"),
        ("rebase.backend=merge", "backend-dependent recovery state"),
    ]

    for token, hazard in expected:
        assert token in PINNED_CONFIG_ARGS, hazard


def test_rebase_shape_explicitly_disables_user_config_behavior() -> None:
    expected = [
        ("--no-autostash", "stranded stash"),
        ("--no-autosquash", "editor-opening autosquash"),
        ("--no-update-refs", "movement of another branch"),
        ("--empty=drop", "interactive empty-commit stop"),
    ]
    replay_argv = (
        "rebase",
        "--no-autostash",
        "--no-autosquash",
        "--no-update-refs",
        "--empty=drop",
        "--",
        "develop",
        "feature",
    )

    for flag, hazard in expected:
        assert flag in replay_argv, hazard
