"""Regression tests for MCP-server subprocess PYTHONPATH isolation.

The lifecycle spawns the standalone MCP server as
``sys.executable -m ralph.mcp.server``. That child MUST resolve the same
``ralph`` package tree the parent imported (``_PACKAGE_ROOT``). When an
outer launcher (observed live: an outer Ralph orchestrating a development
cycle inside a worktree) exports a ``PYTHONPATH`` pointing at a different
install of this same package (e.g. a pipx site-packages), the inherited
entry sorts ahead of the spawned server's own package and the child
imports the STALE tree — measured as Moonshot HTTP 400 on the Kimi smoke
because the stale tree predates the schema-flavor negotiation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.server import lifecycle

if TYPE_CHECKING:
    import pytest


def test_subprocess_env_replaces_foreign_ralph_pythonpath_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inherited PYTHONPATH entry that provides ``ralph`` is replaced.

    The replacement is the parent's own package root, so the spawned
    ``python -m ralph.mcp.server`` can never resolve a different install
    of this package through the inherited path.
    """
    foreign = tmp_path / "foreign-site-packages"
    (foreign / "ralph").mkdir(parents=True)
    (foreign / "ralph" / "__init__.py").write_text("", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(foreign), str(unrelated)]))

    env = lifecycle._subprocess_env(tmp_path / "session.json")

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert str(foreign) not in entries
    assert str(lifecycle._PACKAGE_ROOT) in entries
    assert entries.index(str(lifecycle._PACKAGE_ROOT)) < entries.index(str(unrelated))


def test_subprocess_env_without_foreign_entries_prepends_package_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A benign PYTHONPATH keeps its entries after the package root."""
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(unrelated))

    env = lifecycle._subprocess_env(tmp_path / "session.json")

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == str(lifecycle._PACKAGE_ROOT)
    assert str(unrelated) in entries


def test_subprocess_env_empty_pythonpath_yields_package_root_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No inherited PYTHONPATH means the child gets exactly the package root."""
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = lifecycle._subprocess_env(tmp_path / "session.json")

    assert env["PYTHONPATH"] == str(lifecycle._PACKAGE_ROOT)
