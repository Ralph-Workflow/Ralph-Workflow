"""Behavior tests for the action reported by the agents-config bootstrap.

The agents configuration is written through the TOML-validating writer,
which reports whether it actually changed the file on disk. The bootstrap
result mirrors that outcome:

* ``created`` -- the file did not exist and was written;
* ``regenerated`` -- the file existed and its content changed;
* ``skipped`` -- nothing was written, either because the file already
  existed and the write was not forced, or because the content already
  matched the shipped template.

An operator reads the action to learn whether their edits were replaced, so
a run that leaves an unchanged file in place must never claim it
regenerated that file.
"""

from __future__ import annotations

from pathlib import Path

from ralph.config.bootstrap import ensure_global_agents_config, regenerate_all

AGENTS_FILENAME = "ralph-workflow-agents.toml"


def test_ensure_global_agents_config_creates_the_file(tmp_path: Path) -> None:
    """A missing agents config is created and reported as created."""
    result = ensure_global_agents_config(tmp_path)

    assert result.action == "created"
    assert result.path == tmp_path / AGENTS_FILENAME
    assert result.path.is_file()
    assert result.backup is None


def test_ensure_global_agents_config_reports_regenerated_for_changed_content(
    tmp_path: Path,
) -> None:
    """An existing agents config with other content is regenerated and backed up."""
    target = tmp_path / AGENTS_FILENAME
    operator_content = '[agents.custom]\ncmd = "mine"\n'
    target.write_text(operator_content, encoding="utf-8")

    result = ensure_global_agents_config(tmp_path, force=True)

    assert result.action == "regenerated"
    assert result.backup == tmp_path / f"{AGENTS_FILENAME}.bak"
    assert result.backup is not None
    assert result.backup.read_text(encoding="utf-8") == operator_content


def test_ensure_global_agents_config_reports_skipped_for_unchanged_content(
    tmp_path: Path,
) -> None:
    """An unchanged agents config is not regenerated and keeps no backup."""
    created = ensure_global_agents_config(tmp_path)
    assert created.action == "created"
    content = created.path.read_text(encoding="utf-8")

    result = ensure_global_agents_config(tmp_path, force=True)

    assert result.action == "skipped"
    assert result.backup is None
    assert not (tmp_path / f"{AGENTS_FILENAME}.bak").exists()
    assert result.path.read_text(encoding="utf-8") == content


def test_ensure_global_agents_config_skips_an_existing_file_without_force(
    tmp_path: Path,
) -> None:
    """An existing agents config is left alone when the write is not forced."""
    target = tmp_path / AGENTS_FILENAME
    operator_content = '[agents.custom]\ncmd = "mine"\n'
    target.write_text(operator_content, encoding="utf-8")

    result = ensure_global_agents_config(tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert target.read_text(encoding="utf-8") == operator_content


def test_regenerate_all_reports_skipped_for_unchanged_agents_config(
    tmp_path: Path,
) -> None:
    """regenerate_all leaves an unchanged agents config untouched."""
    global_dir = tmp_path / "config"
    global_dir.mkdir()
    created = ensure_global_agents_config(global_dir)
    content = created.path.read_text(encoding="utf-8")

    results = regenerate_all(global_dir=global_dir)

    agents_results = [result for result in results if result.path.name == AGENTS_FILENAME]
    assert len(agents_results) == 1
    assert agents_results[0].action == "skipped"
    assert agents_results[0].backup is None
    assert not (global_dir / f"{AGENTS_FILENAME}.bak").exists()
    assert agents_results[0].path.read_text(encoding="utf-8") == content
