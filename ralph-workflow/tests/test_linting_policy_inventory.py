"""Drift guard for the linting policy's per-file-ignores inventory.

``docs/ralph-workflow-policy/linting-policy.md`` claims its
``RALPH-FACT: excluded_paths`` line is "the complete inventory of keys" for
``[tool.ruff.lint.per-file-ignores]``. That claim silently rotted twice: two
keys added to ``pyproject.toml`` were never recorded. The shipped file
contents ARE the contract here, so both files are read as-is.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_INVENTORY_MARKER = "RALPH-FACT: excluded_paths:"


def _first_existing(*candidates: str) -> Path:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    raise AssertionError(f"none of {candidates} exist")


def _per_file_ignore_keys() -> list[str]:
    config = tomllib.loads(
        _first_existing("pyproject.toml", "ralph-workflow/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    return list(ignores)


def _inventory_line() -> str:
    policy = _first_existing(
        "../docs/ralph-workflow-policy/linting-policy.md",
        "docs/ralph-workflow-policy/linting-policy.md",
    ).read_text(encoding="utf-8")
    for line in policy.splitlines():
        if line.startswith(_INVENTORY_MARKER):
            return line
    raise AssertionError(f"{_INVENTORY_MARKER!r} line is missing from the linting policy")


def test_every_per_file_ignore_key_is_recorded_in_the_policy_inventory() -> None:
    """The documented inventory must name every pyproject per-file-ignores key."""
    line = _inventory_line()
    missing = [key for key in _per_file_ignore_keys() if f"`{key}`" not in line]
    assert missing == [], f"per-file-ignores keys absent from the documented inventory: {missing}"
