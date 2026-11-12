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
    for entry in _live_roots():
        assert entry.source_url, f"{entry.agent}: source_url must be non-empty"
        scheme = urlparse(entry.source_url).scheme
        assert scheme in {"http", "https"}, (
            f"{entry.agent}: source_url {entry.source_url!r} has scheme {scheme!r}, "
            "must be http or https"
        )


def test_agent_skill_roots_have_last_verified_iso() -> None:
    for entry in _live_roots():
        date = getattr(entry, "last_verified_iso", "")
        assert _LAST_VERIFIED_ISO_RE.match(date), (
            f"{entry.agent}: last_verified_iso {date!r} is not a well-formed YYYY-MM-DD date"
        )


def test_agent_skill_roots_include_codex_current_user_global_root() -> None:
    entry = next(e for e in agent_skill_roots() if e.agent == "codex-agents")
    assert entry.path_segments == (".agents", "skills")
    assert entry.source_url == "https://learn.chatgpt.com/docs/build-skills"
    assert entry.last_verified_iso == "2026-11-11"
    assert entry.is_canonical is False


def test_agent_skill_roots_include_antigravity_all_surfaces_root() -> None:
    entry = next(e for e in agent_skill_roots() if e.agent == "agy-config")
    assert entry.path_segments == (".gemini", "config", "skills")
    assert entry.source_url == "https://antigravity.google/docs/skills/"
    assert entry.last_verified_iso == "2026-11-11"
    assert entry.is_canonical is False


def test_agent_skill_root_agent_names_are_unique() -> None:
    names = [entry.agent for entry in agent_skill_roots()]
    assert len(names) == len(set(names))


def test_agent_skill_roots_cover_documented_supported_agents() -> None:
    expected_agents = {
        "claude",
        "codex",
        "opencode",
        "agy",
        "codex-agents",
        "agy-config",
    }
    live = {entry.agent for entry in _live_roots()}
    assert expected_agents.issubset(live), (
        f"Live registry {sorted(live)} is missing documented agents "
        f"{sorted(expected_agents - live)}. Update expected_agents if the upstream path "
        "for one of these agents can no longer be confirmed."
    )
    out_of_scope_agents = {
        "nanocoder",
        "gemini-cli",
        "pi",
    }
    assert not (out_of_scope_agents & live), (
        f"Live registry unexpectedly contains out-of-scope agents: {out_of_scope_agents & live}"
    )


def test_agent_skill_roots_excludes_pi_for_no_user_global_root() -> None:
    live = {entry.agent for entry in _live_roots()}
    assert "pi" not in live, (
        f"Live user-global registry unexpectedly contains 'pi': {sorted(live)}. "
        "The pi.dev docs do not document a user-global skill discovery root; "
        "see _agent_paths.py PI SCOPE SPLIT (pi.dev) section for the rationale."
    )


def test_opencode_claude_fallback_is_encoded_in_the_registry() -> None:
    roots = agent_paths_module.agent_skill_roots()
    claude_entries = [entry for entry in roots if entry.agent == "claude"]
    assert len(claude_entries) == 1, (
        "the OpenCode fallback onto ~/.claude/skills is satisfied by the single "
        "canonical Claude install, so the registry must carry exactly one claude entry"
    )
    canonical = agent_paths_module.canonical_agent_skill_root()
    assert canonical is claude_entries[0], (
        "the canonical user-global skill root must be the Claude entry that "
        "covers the OpenCode fallback path"
    )
    assert canonical.path_segments == (".claude", "skills"), (
        "the canonical Claude install must live at ~/.claude/skills so the "
        "OpenCode fallback path is covered by the canonical install"
    )
    opencode_entries = [entry for entry in roots if entry.agent == "opencode"]
    assert len(opencode_entries) == 1, (
        "the registry must carry exactly one opencode entry for the canonical "
        "OpenCode discovery root"
    )
    opencode = opencode_entries[0]
    assert opencode.path_segments == (".config", "opencode", "skills"), (
        "the OpenCode entry must own the canonical OpenCode discovery root"
    )
    assert opencode.is_canonical is False, (
        "the OpenCode entry must stay a sibling root: the Claude fallback is "
        "satisfied by the canonical install, not by a second canonical root"
    )


def test_sibling_symlink_install_covers_every_sibling_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    monkeypatch.setattr("ralph.skills._installer.agent_skill_roots", lambda: fake_roots)
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
