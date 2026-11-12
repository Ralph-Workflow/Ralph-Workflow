from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch
from urllib.parse import urlparse

from ralph.skills._agent_paths import (
    AGENT_SKILL_ROOTS,
    agent_skill_roots,
    canonical_agent_skill_root,
    sibling_agent_skill_roots,
)

if TYPE_CHECKING:
    import pytest

_EXPECTED_AGENT_PATHS: dict[str, tuple[str, ...]] = {
    "claude": (".claude", "skills"),
    "codex": (".codex", "skills"),
    "opencode": (".config", "opencode", "skills"),
    "agy": (".gemini", "antigravity-cli", "skills"),
    "codex-agents": (".agents", "skills"),
    "agy-config": (".gemini", "config", "skills"),
}

_EXPECTED_SOURCE_URLS: dict[str, str] = {
    "claude": "https://code.claude.com/docs/en/skills",
    "codex": (
        "https://github.com/openai/codex/blob/main/"
        "codex-rs/skills/src/assets/samples/skill-installer/SKILL.md"
    ),
    "opencode": "https://opencode.ai/docs/skills/",
    "agy": "https://antigravity.google/docs/skills/",
    "codex-agents": "https://learn.chatgpt.com/docs/build-skills",
    "agy-config": "https://antigravity.google/docs/skills/",
}

_AUTHORITATIVE_SOURCE_HOSTS: frozenset[str] = frozenset(
    {
        "antigravity.google",
        "code.claude.com",
        "github.com",
        "learn.chatgpt.com",
        "opencode.ai",
    }
)


def test_agent_skill_roots_lists_canonical_first() -> None:
    roots = agent_skill_roots()
    assert roots[0].is_canonical is True
    assert roots[0].agent == "claude"


def test_sibling_agents_exclude_canonical() -> None:
    canonical = canonical_agent_skill_root()
    siblings = sibling_agent_skill_roots()
    assert canonical not in siblings
    assert all(not s.is_canonical for s in siblings)


def test_agent_skill_root_paths_use_home_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))

    with patch.object(Path, "home", return_value=fake_home):
        for entry in agent_skill_roots():
            resolved = entry.resolve()
            assert str(resolved).startswith(str(fake_home)), (
                f"{entry.agent}: {resolved} did not re-resolve under patched home"
            )
            assert resolved.is_absolute()


def test_canonical_root_matches_existing_claude_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "fake-home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    canonical = canonical_agent_skill_root()
    assert canonical.resolve() == fake_home / ".claude" / "skills"


def test_sibling_root_count_matches_supported_agents_minus_canonical() -> None:
    siblings = sibling_agent_skill_roots()
    assert len(siblings) == len(AGENT_SKILL_ROOTS) - 1
    assert {s.agent for s in siblings} == {s.agent for s in siblings}


def test_agent_skill_roots_excludes_project_local_roots() -> None:
    for entry in agent_skill_roots():
        for segment in entry.path_segments:
            assert segment, f"empty path segment in {entry.agent}: {entry.path_segments!r}"
            assert not segment.startswith("/"), (
                f"absolute path segment in {entry.agent}: {entry.path_segments!r}"
            )
            assert segment != ".", (
                f"project-relative '.' segment in {entry.agent}: {entry.path_segments!r}"
            )
        assert entry.resolve().is_absolute(), (
            f"resolved path must be absolute for {entry.agent}: {entry.resolve()}"
        )


def test_agent_skill_roots_returns_six_total_entries() -> None:
    assert len(AGENT_SKILL_ROOTS) == 6
    assert len(agent_skill_roots()) == 6


def test_agent_skill_roots_pin_documented_user_global_paths() -> None:
    live = {entry.agent: entry.path_segments for entry in agent_skill_roots()}
    assert live == _EXPECTED_AGENT_PATHS


def test_agent_skill_roots_pin_authoritative_source_urls() -> None:
    live = {entry.agent: entry.source_url for entry in agent_skill_roots()}
    assert live == _EXPECTED_SOURCE_URLS


def test_agent_skill_roots_source_urls_use_https_and_authoritative_hosts() -> None:
    for entry in agent_skill_roots():
        parsed = urlparse(entry.source_url)
        assert parsed.scheme == "https", (
            f"{entry.agent}: source_url {entry.source_url!r} must use https"
        )
        assert parsed.netloc in _AUTHORITATIVE_SOURCE_HOSTS, (
            f"{entry.agent}: source_url {entry.source_url!r} points at host "
            f"{parsed.netloc!r}, which is not an authoritative upstream host"
        )


def test_agent_skill_root_paths_resolve_to_distinct_directories() -> None:
    resolved = [entry.resolve() for entry in agent_skill_roots()]
    assert len(set(resolved)) == len(resolved)


def test_codex_user_global_roots_cover_both_documented_codex_locations() -> None:
    live = {entry.agent: entry for entry in agent_skill_roots()}
    assert live["codex"].path_segments == (".codex", "skills")
    assert live["codex-agents"].path_segments == (".agents", "skills")
    assert live["codex"].resolve() != live["codex-agents"].resolve()


def test_agent_skill_root_dataclass_is_frozen() -> None:
    canonical = canonical_agent_skill_root()
    assert canonical.__dataclass_params__.frozen is True
    assert {f.name for f in fields(canonical)} == {
        "agent",
        "path_segments",
        "source_url",
        "last_verified_iso",
        "is_canonical",
    }
