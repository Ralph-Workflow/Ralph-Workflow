"""Tests for autowiring agent chains when targeting agents configuration files."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.config.agent_detection import autowire_chains_to_detected_agent

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
    """Create a fresh git repo with one initial empty commit so HEAD exists."""
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def _git_log_subjects(repo_root: Path) -> list[str]:
    """Return git log subject lines (newest first) for the test repo."""
    repo = Repo(repo_root)
    try:
        return [str(c.message).splitlines()[0] for c in repo.iter_commits()]
    finally:
        repo.close()


def _mock_no_binary(_name: str) -> str | None:
    """Mock `shutil.which` to simulate absent agent binaries."""
    return None


def test_autowire_agents_toml_routes_through_write_agents_toml_with_backup_and_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An agents config file rewritten by autowiring creates a .bak backup and trigger log."""
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

    # Log output names trigger, .bak path, and config path
    assert "ralph --init: autowire agent chains" in caplog.text
    assert str(bak_path) in caplog.text
    assert str(agents_path) in caplog.text


def test_autowire_agents_toml_in_git_repo_creates_no_chore_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agents file inside a git repo does NOT create a chore auto-commit when autowired."""
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
    """A main config file (not agents config) still creates a chore auto-commit and no .bak file."""
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
    """All agent config filename variants route through write_agents_toml with .bak backup."""
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
