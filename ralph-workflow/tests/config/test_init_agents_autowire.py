from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.config.agent_detection import autowire_chains_to_detected_agent

pytestmark = pytest.mark.subprocess_e2e

_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML = """\
[agents.claude]
cmd = "claude"

[agent_chains]
planning = ["claude/opus"]
development = ["claude/sonnet"]
analysis = ["claude/opus"]
commit = ["claude/haiku"]
"""


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def _git_log_subjects(repo_root: Path) -> list[str]:
    repo = Repo(repo_root)
    try:
        return [str(c.message).splitlines()[0] for c in repo.iter_commits()]
    finally:
        repo.close()


def _mock_no_binary(_name: str) -> str | None:
    return None


def test_autowire_agents_toml_routes_through_write_agents_toml_with_backup_and_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agents_path = tmp_path / "ralph-workflow-agents.toml"
    agents_path.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")
    bak_path = agents_path.with_name(f"{agents_path.name}.bak")

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    with caplog.at_level(logging.INFO):
        result = autowire_chains_to_detected_agent(agents_path, detected=["codex"])

    assert sorted(result or []) == ["claude"]
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML

    new_content = agents_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(new_content)
    assert isinstance(parsed, dict)
    chains = parsed.get("agent_chains")
    assert isinstance(chains, dict)
    assert chains["planning"] == ["codex"]
    assert chains["development"] == ["codex"]
    assert chains["analysis"] == ["codex"]
    assert chains["commit"] == ["codex"]

    assert "ralph --init: autowire agent chains" in caplog.text
    assert str(bak_path) in caplog.text
    assert str(agents_path) in caplog.text


def test_autowire_agents_toml_in_git_repo_creates_no_chore_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    agents_path = tmp_path / "ralph-workflow-agents.toml"
    agents_path.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow-agents.toml"])
        repo.index.commit(
            "seed agents config",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    result = autowire_chains_to_detected_agent(agents_path, detected=["codex"])
    assert sorted(result or []) == ["claude"]

    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed agents config"
    assert "chore(config): update agent configuration" not in subjects

    bak_path = agents_path.with_name(f"{agents_path.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML


def test_autowire_main_config_in_git_repo_still_creates_chore_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    defaults = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    main_config = tmp_path / "ralph-workflow.toml"
    main_config.write_text(
        (defaults / "ralph-workflow.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed main config",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    result = autowire_chains_to_detected_agent(main_config, detected=["codex"])
    assert sorted(result or []) == ["claude"]

    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration"
    assert not main_config.with_name(f"{main_config.name}.bak").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "ralph-workflow-agents.toml",
        "agents.toml",
        "team-agents.toml",
    ],
)
def test_autowire_agents_filename_variants_route_through_safe_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    agents_path = tmp_path / filename
    agents_path.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")
    bak_path = agents_path.with_name(f"{filename}.bak")

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    result = autowire_chains_to_detected_agent(agents_path, detected=["codex"])
    assert sorted(result or []) == ["claude"]
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML


def test_autowire_symlink_alias_to_agents_target_uses_validated_atomic_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")
    alias_path = tmp_path / "custom.toml"
    alias_path.symlink_to(target_path)
    resolved_target = target_path.resolve()
    target_inode_before = resolved_target.stat().st_ino

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    publications: list[tuple[Path, Path, bytes | None, bytes | None]] = []
    real_replace = os.replace

    def observing_replace(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
    ) -> None:
        source = Path(src)
        destination = Path(dst)
        publications.append(
            (
                source,
                destination,
                destination.read_bytes() if destination.is_file() else None,
                source.read_bytes() if source.is_file() else None,
            )
        )
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", observing_replace)

    with caplog.at_level(logging.INFO):
        result = autowire_chains_to_detected_agent(alias_path, detected=["codex"])

    assert sorted(result or []) == ["claude"]

    parsed = tomllib.loads(resolved_target.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    chains = parsed.get("agent_chains")
    assert isinstance(chains, dict)
    assert chains["planning"] == ["codex"]
    assert chains["development"] == ["codex"]
    assert chains["analysis"] == ["codex"]
    assert chains["commit"] == ["codex"]

    assert alias_path.is_symlink()
    assert alias_path.resolve() == resolved_target

    bak_path = resolved_target.with_name(f"{resolved_target.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML

    target_publications = [entry for entry in publications if entry[1] == resolved_target]
    assert len(target_publications) == 1
    source, _destination, before, staged = target_publications[0]
    assert before == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML.encode("utf-8")
    assert staged == resolved_target.read_text(encoding="utf-8").encode("utf-8")
    assert source.parent == resolved_target.parent
    assert source.name.startswith(f".{resolved_target.name}.tmp.")
    assert source.name.removeprefix(f".{resolved_target.name}.tmp.") != ""
    assert resolved_target.stat().st_ino != target_inode_before

    backup_publications = [entry for entry in publications if entry[1] == bak_path]
    assert len(backup_publications) == 1
    assert publications.index(backup_publications[0]) < publications.index(target_publications[0])

    assert [entry.name for entry in tmp_path.iterdir() if entry.name.startswith(".")] == []

    assert "ralph --init: autowire agent chains" in caplog.text


def test_autowire_symlink_alias_to_main_config_keeps_deterministic_auto_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    defaults = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    main_config = tmp_path / "ralph-workflow.toml"
    main_config.write_text(
        (defaults / "ralph-workflow.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    alias_path = tmp_path / "custom.toml"
    alias_path.symlink_to(main_config)

    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow.toml"])
        repo.index.commit(
            "seed main config",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    result = autowire_chains_to_detected_agent(alias_path, detected=["codex"])
    assert sorted(result or []) == ["claude"]

    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "chore(config): update agent configuration"
    assert not main_config.with_name(f"{main_config.name}.bak").exists()
    assert not alias_path.with_name(f"{alias_path.name}.bak").exists()


def test_autowire_hardlink_alias_to_agents_file_routes_through_safe_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hardlink alias pointing to an agents file routes through write_agents_toml."""
    _init_repo_with_initial_commit(tmp_path)
    agents_path = tmp_path / "ralph-workflow-agents.toml"
    agents_path.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")
    hardlink_alias = tmp_path / "custom-hardlink.toml"
    os.link(agents_path, hardlink_alias)

    repo = Repo(tmp_path)
    try:
        repo.index.add(["ralph-workflow-agents.toml", "custom-hardlink.toml"])
        repo.index.commit(
            "seed agents configs",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    with caplog.at_level(logging.INFO):
        result = autowire_chains_to_detected_agent(hardlink_alias, detected=["codex"])

    assert sorted(result or []) == ["claude"]

    # Valid TOML in both the canonical agents path and the hardlink alias
    parsed = tomllib.loads(agents_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    chains = parsed.get("agent_chains")
    assert isinstance(chains, dict)
    assert chains["planning"] == ["codex"]
    assert chains["development"] == ["codex"]
    assert chains["analysis"] == ["codex"]
    assert chains["commit"] == ["codex"]

    parsed_hardlink = tomllib.loads(hardlink_alias.read_text(encoding="utf-8"))
    assert parsed_hardlink.get("agent_chains") == chains

    # Contractually correct .bak next to the target with recoverable old bytes
    bak_path = agents_path.with_name(f"{agents_path.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML
    assert not hardlink_alias.with_name(f"{hardlink_alias.name}.bak").exists()

    # Safe-writer trigger log
    assert "ralph --init: autowire agent chains" in caplog.text
    assert str(bak_path) in caplog.text
    assert str(agents_path) in caplog.text

    # No agents-file chore commit created
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed agents configs"
    assert "chore(config): update agent configuration" not in subjects


def test_autowire_custom_named_symlink_alias_to_agents_target_uses_safe_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A custom-named symlink pointing to an agents backing file uses write_agents_toml."""
    _init_repo_with_initial_commit(tmp_path)
    custom_backing = tmp_path / "custom-shared-backing.toml"
    custom_backing.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")

    agents_path = tmp_path / "ralph-workflow-agents.toml"
    agents_path.symlink_to(custom_backing)

    symlink_alias = tmp_path / "custom-alias.toml"
    symlink_alias.symlink_to(custom_backing)

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                "custom-shared-backing.toml",
                "ralph-workflow-agents.toml",
                "custom-alias.toml",
            ]
        )
        repo.index.commit(
            "seed symlink configs",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    with caplog.at_level(logging.INFO):
        result = autowire_chains_to_detected_agent(symlink_alias, detected=["codex"])

    assert sorted(result or []) == ["claude"]

    # Valid TOML published through the symlink to the backing file
    parsed = tomllib.loads(custom_backing.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    chains = parsed.get("agent_chains")
    assert isinstance(chains, dict)
    assert chains["planning"] == ["codex"]
    assert chains["development"] == ["codex"]
    assert chains["analysis"] == ["codex"]
    assert chains["commit"] == ["codex"]

    alias_parsed = tomllib.loads(symlink_alias.read_text(encoding="utf-8"))
    assert alias_parsed.get("agent_chains") == chains

    # Contractually correct .bak next to the agents target
    bak_path = agents_path.with_name(f"{agents_path.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML
    assert not symlink_alias.with_name(f"{symlink_alias.name}.bak").exists()

    # Safe-writer trigger log
    assert "ralph --init: autowire agent chains" in caplog.text
    assert str(bak_path) in caplog.text

    # No agents-file chore commit created
    subjects = _git_log_subjects(tmp_path)
    assert subjects[0] == "seed symlink configs"
    assert "chore(config): update agent configuration" not in subjects


def test_autowire_custom_named_symlink_alias_to_xdg_agents_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A custom-named symlink alias to the user-global XDG agents config uses write_agents_toml."""
    xdg_dir = tmp_path / "xdg-config"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    global_agents = xdg_dir / "ralph-workflow-agents.toml"
    global_agents.write_text(_UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML, encoding="utf-8")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _init_repo_with_initial_commit(project_dir)

    alias_path = project_dir / "custom-alias.toml"
    alias_path.symlink_to(global_agents)

    repo = Repo(project_dir)
    try:
        repo.index.add(["custom-alias.toml"])
        repo.index.commit(
            "seed custom alias",
            author=Actor("Test Author", "test@example.com"),
            committer=Actor("Test Author", "test@example.com"),
        )
    finally:
        repo.close()

    monkeypatch.setattr(
        "ralph.config.agent_detection.shutil.which",
        _mock_no_binary,
    )

    with caplog.at_level(logging.INFO):
        result = autowire_chains_to_detected_agent(alias_path, detected=["codex"])

    assert sorted(result or []) == ["claude"]

    # Valid TOML in the global agents file
    parsed = tomllib.loads(global_agents.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    chains = parsed.get("agent_chains")
    assert isinstance(chains, dict)
    assert chains["planning"] == ["codex"]

    # Contractually correct .bak next to the global agents config
    bak_path = global_agents.with_name(f"{global_agents.name}.bak")
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == _UNTOUCHED_DEFAULT_CHAINS_AGENTS_TOML
    assert not alias_path.with_name(f"{alias_path.name}.bak").exists()

    # Safe-writer trigger log
    assert "ralph --init: autowire agent chains" in caplog.text
    assert str(bak_path) in caplog.text

    # No chore commit in project repo
    subjects = _git_log_subjects(project_dir)
    assert subjects[0] == "seed custom alias"
    assert "chore(config): update agent configuration" not in subjects
