"""Tests for the conflict resolution operator_command_for_paths contract.

Contract:
``operator_command_for_paths(paths: tuple[str, ...]) -> str`` produces the
exact manual git command naming each conflicted path for operator action.
Empty tuples return an empty string. Paths are shell-quoted with shlex.quote
and separated after '--' to prevent flag injection.
"""

from __future__ import annotations

import shlex

import pytest

from ralph.pipeline.conflict_resolution import (
    operator_command_for_paths,
)

pytestmark = pytest.mark.timeout_seconds(2)


def test_operator_command_empty_tuple() -> None:
    """An empty tuple of paths returns an empty string."""
    assert operator_command_for_paths(()) == ""


def test_operator_command_single_path() -> None:
    """A single regular path produces 'git add -- <path>'."""
    assert operator_command_for_paths(("src/main.py",)) == "git add -- src/main.py"


def test_operator_command_multiple_paths() -> None:
    """Multiple paths are listed in order after 'git add -- '."""
    paths = ("src/main.py", "docs/index.md", "README.md")
    assert operator_command_for_paths(paths) == "git add -- src/main.py docs/index.md README.md"


def test_operator_command_paths_with_spaces_and_quotes() -> None:
    """Paths with spaces, quotes, and special shell characters are shell-quoted safely."""
    paths = (
        "path with spaces/file name.py",
        "path$with;weird&chars.txt",
        "file'with'quotes.txt",
        "-flag_like_name.py",
    )
    expected = "git add -- " + " ".join(shlex.quote(p) for p in paths)
    assert operator_command_for_paths(paths) == expected


def test_operator_command_accepts_sequence_at_runtime() -> None:
    """The function gracefully accepts sequences (e.g. lists) at runtime."""
    raw_list = ["file_a.py", "file_b.py"]
    assert operator_command_for_paths(raw_list) == "git add -- file_a.py file_b.py"
