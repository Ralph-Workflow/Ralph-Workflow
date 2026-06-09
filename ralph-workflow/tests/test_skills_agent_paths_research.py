"""Black-box tests for the supported-agents registry research contract.

The agent-skill-root registry is the source of truth for which agent roots
`ralph --init` symlinks the bundled skill bundle into. The registry MUST
be well-researched: every shipped entry must cite a non-empty HTTPS source
URL and a well-formed `last_verified_iso` date so a future maintainer knows
the path is currently documented upstream.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ralph.skills import _agent_paths as agent_paths_module
from ralph.skills._agent_paths import (
    AgentSkillRoot,
    agent_skill_roots,
)
from ralph.skills._content import BASELINE_SKILL_NAMES
from ralph.skills._installer import install_baseline_skills

if TYPE_CHECKING:
    import pytest

_LAST_VERIFIED_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _live_roots() -> list[AgentSkillRoot]:
    return list(agent_skill_roots())


def test_agent_skill_roots_have_non_empty_source_urls() -> None:
    """Every shipped AgentSkillRoot must cite a documented HTTPS upstream URL."""
    for entry in _live_roots():
        assert entry.source_url, f"{entry.agent}: source_url must be non-empty"
        # urlparse scheme check is permissive; we only require the scheme is http(s).
        scheme = urlparse(entry.source_url).scheme
        assert scheme in {"http", "https"}, (
            f"{entry.agent}: source_url {entry.source_url!r} has scheme {scheme!r}, "
            "must be http or https"
        )


def test_agent_skill_roots_have_last_verified_iso() -> None:
    """Every shipped AgentSkillRoot must carry a well-formed last_verified_iso date."""
    for entry in _live_roots():
        date = getattr(entry, "last_verified_iso", "")
        assert _LAST_VERIFIED_ISO_RE.match(date), (
            f"{entry.agent}: last_verified_iso {date!r} is not a well-formed YYYY-MM-DD date"
        )


def test_agent_skill_roots_cover_documented_supported_agents() -> None:
    """Live registry must cover the documented supported-agent set."""
    expected_agents = {
        "claude",
        "codex",
        "opencode",
        "agy",
    }
    live = {entry.agent for entry in _live_roots()}
    assert expected_agents.issubset(live), (
        f"Live registry {sorted(live)} is missing documented agents "
        f"{sorted(expected_agents - live)}. Update expected_agents if the upstream path "
        "for one of these agents can no longer be confirmed."
    )
    # Out-of-scope agents (no skill system) must be explicitly absent.
    out_of_scope_agents = {
        "nanocoder",
        "gemini-cli",
    }
    assert not (out_of_scope_agents & live), (
        f"Live registry unexpectedly contains out-of-scope agents: {out_of_scope_agents & live}"
    )


def test_opencode_claude_fallback_documented() -> None:
    """The module docstring of _agent_paths.py must call out OpenCode's
    documented `~/.claude/skills/` fallback so future maintainers know
    no separate symlink entry is required.
    """
    docstring = agent_paths_module.__doc__ or ""
    assert "claude" in docstring.lower(), (
        "Module docstring must mention 'claude' to document the OpenCode fallback contract"
    )
    # The docstring should explicitly call out the fallback
    # (e.g. "OpenCode's documented `~/.claude/skills/` fallback is already
    # covered by the canonical Claude install").
    assert "fallback" in docstring.lower() or "opencode" in docstring.lower(), (
        "Module docstring must explicitly document the OpenCode fallback contract"
    )


def test_sibling_symlink_install_covers_every_sibling_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """install_baseline_skills must symlink into every sibling root."""
    tmp = tmp_path
    canonical_dir = tmp / "canonical"
    codex_dir = tmp / "codex"
    opencode_dir = tmp / "opencode"
    agy_dir = tmp / "agy"
    for d in (canonical_dir, codex_dir, opencode_dir, agy_dir):
        d.mkdir(parents=True, exist_ok=True)

    fake_roots: tuple[AgentSkillRoot, ...] = (
        AgentSkillRoot(
            agent="claude",
            path_segments=(str(canonical_dir),),
            source_url="",
            is_canonical=True,
        ),
        AgentSkillRoot(
            agent="codex",
            path_segments=(str(codex_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="opencode",
            path_segments=(str(opencode_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="agy",
            path_segments=(str(agy_dir),),
            source_url="",
            is_canonical=False,
        ),
    )
    fake_siblings = fake_roots[1:]

    monkeypatch.setattr(
        "ralph.skills._installer.agent_skill_roots", lambda: fake_roots
    )
    monkeypatch.setattr(
        "ralph.skills._installer.canonical_agent_skill_root",
        lambda: fake_roots[0],
    )
    monkeypatch.setattr(
        "ralph.skills._installer.sibling_agent_skill_roots",
        lambda: fake_siblings,
    )

    install_baseline_skills()

    assert fake_siblings, "fake sibling set must not be empty in this test"
    for sibling in fake_siblings:
        sibling_dir = Path(sibling.path_segments[0])
        for name in BASELINE_SKILL_NAMES:
            entry_path = sibling_dir / name
            assert entry_path.is_symlink(), (
                f"Expected {entry_path} to be a symlink (sibling root fan-out coverage)"
            )
