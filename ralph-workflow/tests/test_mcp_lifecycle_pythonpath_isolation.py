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
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = lifecycle._subprocess_env(tmp_path / "session.json")

    assert env["PYTHONPATH"] == str(lifecycle._PACKAGE_ROOT)


def test_mcp_runtime_recovers_from_a_deleted_dev_generation(tmp_path: Path) -> None:
    install_root = tmp_path / "ralph-workflow-dev"
    vanished = install_root / "generations" / "generation-old"
    current_generation = install_root / "generations" / "generation-current"
    current = install_root / "current"
    interpreter = current_generation / ".venv" / "bin" / "python3"
    package_root = current_generation
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    (package_root / "ralph" / "mcp" / "server").mkdir(parents=True)
    (package_root / "ralph" / "__init__.py").write_text("", encoding="utf-8")
    current.symlink_to(current_generation, target_is_directory=True)

    runtime = lifecycle.resolve_mcp_runtime(
        executable=vanished / ".venv" / "bin" / "python3",
        package_root=vanished,
    )

    assert runtime == (interpreter.resolve(), package_root.resolve())
