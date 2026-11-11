from __future__ import annotations

import shlex

import pytest

from ralph.pipeline.conflict_resolution import (
    operator_command_for_paths,
)

pytestmark = pytest.mark.timeout_seconds(2)


def test_operator_command_empty_tuple() -> None:
    assert operator_command_for_paths(()) == ""


def test_operator_command_single_path() -> None:
    assert operator_command_for_paths(("src/main.py",)) == "git add -- src/main.py"


def test_operator_command_multiple_paths() -> None:
    paths = ("src/main.py", "docs/index.md", "README.md")
    assert operator_command_for_paths(paths) == "git add -- src/main.py docs/index.md README.md"


def test_operator_command_paths_with_spaces_and_quotes() -> None:
    paths = (
        "path with spaces/file name.py",
        "path$with;weird&chars.txt",
        "file'with'quotes.txt",
        "-flag_like_name.py",
    )
    expected = "git add -- " + " ".join(shlex.quote(p) for p in paths)
    assert operator_command_for_paths(paths) == expected


def test_operator_command_accepts_sequence_at_runtime() -> None:
    raw_list = ["file_a.py", "file_b.py"]
    assert operator_command_for_paths(raw_list) == "git add -- file_a.py file_b.py"
