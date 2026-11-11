from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from loguru import logger

from ralph.config.loader import (
    ConfigTomlError,
    load_config,
    load_toml,
    reset_retired_agent_key_warnings,
)


def _head_sha(repo_root: Path) -> str:
    """Return the commit sha HEAD resolves to, read from the on-disk git metadata.

    Reading the ref files keeps the proof inside the default test lane: no
    git binary is spawned, so the test never crosses the real-Git boundary
    the test-policy audit guards.

    Args:
        repo_root: Root of the git working tree to inspect.

    Returns:
        The full commit sha HEAD points at.
    """
    git_dir = repo_root / ".git"
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head
    ref_name = head.removeprefix("ref:").strip()
    ref_file = git_dir / ref_name
    if ref_file.is_file():
        return ref_file.read_text(encoding="utf-8").strip()
    for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref_name:
            return sha
    msg = f"HEAD ref {ref_name} is missing from {git_dir}"
    raise AssertionError(msg)


def test_load_config_removes_retired_agent_key_from_explicit_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    original_text = (
        "# Keep this operator note.\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "can_commit = true\n"
        'display_name = "Claude Code"\n'
    )
    config_path.write_text(original_text, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    config = load_config(config_path=config_path)

    assert config.agents["claude"].display_name == "Claude Code"
    assert not hasattr(config.agents["claude"], "can_commit")
    assert config_path.read_text(encoding="utf-8") == original_text


def test_load_config_rejects_near_miss_agent_key_without_rewriting_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    original = '[agents.claude]\ncmd = "claude"\ncan_commmit = true\n'
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    assert exc_info.value.code == 1
    assert config_path.read_text(encoding="utf-8") == original
    assert "agents.claude.can_commmit" in "\n".join(records)


def test_load_config_migrates_retired_agent_key_from_global_agents_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reset_retired_agent_key_warnings()
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    agents_path = config_home / "ralph-workflow-agents.toml"
    original_text = (
        '# Keep this agent note.\n[agents."claude"]\ncmd = "claude"\ncan_commit = true\n'
    )
    agents_path.write_text(original_text, encoding="utf-8")
    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    def _fail_commit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("git create_commit must not be called")

    monkeypatch.setattr(
        "ralph.git.operations.create_commit",
        _fail_commit,
    )

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert config.agents["claude"].cmd == "claude"
    assert not hasattr(config.agents["claude"], "can_commit")
    # Leaves the disk file byte-identical
    assert (
        agents_path.read_text(encoding="utf-8") == original_text
    )  # RALPH-TEST-REASON: loader migration is in-memory only to guarantee agents file immutability per Req 2
    # Does not create .migration or .bak
    assert not (config_home / ".ralph-workflow-agents.toml.migration").exists()
    assert not (config_home / "ralph-workflow-agents.toml.bak").exists()
    assert list(config_home.glob("*.migration")) == []
    assert list(config_home.glob("*.bak")) == []
    # Does NOT make any git commit (create_commit not called)
    # Deduplicated warning emitted
    assert any("regenerate-config" in r and "agent_can_commit" in r for r in records)


def test_load_toml_preserves_comments_and_strings_during_retired_agent_key_migration(
    tmp_path: Path,
) -> None:
    original_text = (
        "[agents.claude] # active agent\n"
        'note = "can_commit = true"\n'
        "# can_commit = true\n"
        "can_commit = true # retired\n"
        "[general]\n"
        "can_commit = true\n"
    )
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(original_text, encoding="utf-8")

    data = load_toml(config_path)

    assert data["agents"] == {"claude": {"note": "can_commit = true"}}
    assert config_path.read_text(encoding="utf-8") == original_text


def test_load_toml_does_not_rewrite_malformed_retired_agent_source(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    original = "[agents.claude]\ncan_commit = true\n[general\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigTomlError):
        load_toml(config_path)

    assert config_path.read_text(encoding="utf-8") == original


def test_explicit_config_route_warns_once_and_keeps_disk_and_head_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_git_repo: Path
) -> None:
    """The explicit ``--config`` route names the retired key, its file, and the in-memory-only migration.

    ``load_config(config_path=...)`` is the library entry point behind
    ``ralph --config <path>``. The retired assignment is migrated in memory
    only, so the operator must see one deduplicated warning that names the
    file, the file on disk keeps its exact bytes, and no commit is created.
    """
    reset_retired_agent_key_warnings()
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    config_path = tmp_git_repo / "ralph-workflow.toml"
    original_text = (
        "# Keep this operator note.\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "can_commit = true\n"
        'display_name = "Claude Code"\n'
    )
    config_path.write_text(original_text, encoding="utf-8")
    initial_bytes = config_path.read_bytes()
    initial_sha256 = hashlib.sha256(initial_bytes).hexdigest()
    initial_dir_contents = {entry.name for entry in tmp_git_repo.iterdir()}
    initial_head = _head_sha(tmp_git_repo)

    def _fail_commit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("git create_commit must not be called")

    monkeypatch.setattr(
        "ralph.git.operations.create_commit",
        _fail_commit,
    )

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    # The retired key is gone from the in-memory config.
    assert config.agents["claude"].cmd == "claude"
    assert not hasattr(config.agents["claude"], "can_commit")

    # Exactly one operator-visible warning names the file and the
    # in-memory-only migration.
    warnings = [record for record in records if "agent_can_commit" in record]
    assert len(warnings) == 1, warnings
    warning = warnings[0]
    assert str(config_path) in warning
    assert "in memory" in warning
    assert "not modified" in warning
    assert "ralph --regenerate-config" in warning

    # The file on disk is byte-identical, and no backup, temp, or migration
    # artifact appeared next to it.
    assert config_path.read_bytes() == initial_bytes
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == initial_sha256
    assert {entry.name for entry in tmp_git_repo.iterdir()} == initial_dir_contents
    assert not (tmp_git_repo / "ralph-workflow.toml.bak").exists()
    assert not any(".migration" in entry.name for entry in tmp_git_repo.iterdir())
    assert not any(
        entry.name.startswith(".ralph-workflow.toml.tmp.") for entry in tmp_git_repo.iterdir()
    )

    # No commit was created: HEAD still points at the seeded commit.
    assert _head_sha(tmp_git_repo) == initial_head

    # A second load of the same file in the same process does not warn again.
    records.clear()
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)
    assert not [record for record in records if "agent_can_commit" in record]


def test_agents_layer_and_explicit_route_warn_once_for_the_same_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One file read by both routes produces exactly one warning.

    The agents layer and the generic explicit-config route share ONE warning
    authority keyed by the resolved path, so a file both routes read never
    warns twice for the same process.
    """
    reset_retired_agent_key_warnings()
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text(
        '[agents.claude]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=agents_path)
    finally:
        logger.remove(sink_id)

    warnings = [record for record in records if "agent_can_commit" in record]
    assert len(warnings) == 1, warnings
    assert str(agents_path) in warnings[0]
    assert not hasattr(config.agents["claude"], "can_commit")
