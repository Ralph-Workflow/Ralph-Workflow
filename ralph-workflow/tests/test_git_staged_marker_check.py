from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git import merge as merge_module
from ralph.git.git_run_result import GitRunResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.subprocess_runner import GitRunOptions

_ROOT = Path("/repo")


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    check_report: str,
    staged: Mapping[str, str],
) -> None:

    def _fake_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        del cwd, label, options, call_class
        if args == ("diff", "--cached", "--check"):
            return GitRunResult(
                args=("git", *args),
                returncode=2 if check_report else 0,
                stdout=check_report,
                stderr="",
            )
        if args[:2] == ("cat-file", "blob") and args[2].removeprefix(":0:") in staged:
            content = staged[args[2].removeprefix(":0:")]
            return GitRunResult(args=("git", *args), returncode=0, stdout=content, stderr="")
        return GitRunResult(args=("git", *args), returncode=128, stdout="", stderr="fatal: bad")

    monkeypatch.setattr(merge_module, "run_git", _fake_run_git)


def test_a_heading_underlined_with_seven_equals_is_not_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        check_report="CHANGES.rst:2: leftover conflict marker\n",
        staged={"CHANGES.rst": "Changes\n=======\n\n- new\n"},
    )

    assert merge_module.staged_conflict_marker_paths(_ROOT) == []


def test_a_real_fence_beside_a_heading_rule_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        check_report=(
            "CHANGES.rst:2: leftover conflict marker\n"
            "CHANGES.rst:5: leftover conflict marker\n"
            "b.txt:3: leftover conflict marker\n"
        ),
        staged={
            "CHANGES.rst": "Changes\n=======\n\n- new\n<<<<<<< ours\n",
            "b.txt": "x\n=======\n>>>>>>> theirs\n",
        },
    )

    assert merge_module.staged_conflict_marker_paths(_ROOT) == ["CHANGES.rst", "b.txt"]


def test_a_reported_line_that_cannot_be_read_back_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        check_report="gone.rst:2: leftover conflict marker\nshort.rst:9: leftover conflict marker\n",
        staged={"short.rst": "Title\n=======\n"},
    )

    assert merge_module.staged_conflict_marker_paths(_ROOT) == ["gone.rst", "short.rst"]
