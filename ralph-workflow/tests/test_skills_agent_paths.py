from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.skills._agent_paths import (
    AGENT_SKILL_ROOTS,
    agent_skill_roots,
    canonical_agent_skill_root,
    sibling_agent_skill_roots,
)

if TYPE_CHECKING:
    import pytest


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


# RALPH-TEST-REASON: two user-global roots are registered for Codex's current and Antigravity's all-surfaces skill locations


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
            # RALPH-TEST-REASON: two user-global roots are registered for Codex's current and Antigravity's all-surfaces skill locations
        )


def test_agent_skill_roots_returns_six_total_entries() -> None:
    """Four original roots plus Codex's current and Antigravity's all-surfaces roots."""
    assert len(AGENT_SKILL_ROOTS) == 6
    assert len(agent_skill_roots()) == 6


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
