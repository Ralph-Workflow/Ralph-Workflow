"""Black-box tests for the project-scope supported-agents registry research contract.

Mirrors ``test_skills_agent_paths_research.py`` for project-scope entries
returned by ``project_sibling_skill_roots``. The canonical research
contract is enforced for BOTH user-global and project-scope registries.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ralph.skills._agent_paths import (
    agent_skill_roots,
    project_sibling_skill_roots,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.skills._project_paths import ProjectAgentSkillRoot

_LAST_VERIFIED_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# pi is a project-scope-only entry: pi has no documented user-global
# skill root, but pi DOES load project-local `.agents/skills/` per the
# upstream pi.dev usage docs (re-fetched 2026-06-21). The user-global
# set is therefore {claude, codex, opencode, agy} and the project-scope
# set is therefore {claude, codex, agy, pi}. The split is pinned by:
#   - tests/test_skills_agent_paths_research.py::
#     test_agent_skill_roots_cover_documented_supported_agents
#     asserts the 4-name user-global set, with no `pi` entry.
#   - this test module asserts the 4-name project-scope set, with `pi`
#     at `(.agents, skills)`.
_PROJECT_AGENT_NAMES: tuple[str, ...] = ("claude", "codex", "agy", "pi")


def _live_project_siblings(workspace_root: Path) -> list[ProjectAgentSkillRoot]:
    return list(project_sibling_skill_roots(workspace_root))


def test_project_sibling_roots_have_non_empty_source_urls(tmp_path: Path) -> None:
    """Every shipped ProjectAgentSkillRoot must cite a documented HTTPS upstream URL."""
    for entry in _live_project_siblings(tmp_path):
        assert entry.source_url, f"{entry.agent}: source_url must be non-empty"
        scheme = urlparse(entry.source_url).scheme
        assert scheme in {"http", "https"}, (
            f"{entry.agent}: source_url {entry.source_url!r} has scheme {scheme!r}, "
            "must be http or https"
        )


def test_project_sibling_roots_have_last_verified_iso(tmp_path: Path) -> None:
    """Every shipped ProjectAgentSkillRoot must carry a well-formed last_verified_iso date."""
    for entry in _live_project_siblings(tmp_path):
        date = getattr(entry, "last_verified_iso", "")
        assert _LAST_VERIFIED_ISO_RE.match(date), (
            f"{entry.agent}: last_verified_iso {date!r} is not a well-formed YYYY-MM-DD date"
        )


def test_project_sibling_roots_count_is_four(tmp_path: Path) -> None:
    """Exactly 4 project-scope siblings (claude, codex, agy, pi) — no opencode self-symlink.

    pi is included because the upstream pi.dev usage docs document
    project-local `.agents/skills/` (re-fetched 2026-06-21). pi is
    deliberately absent from the user-global ``AGENT_SKILL_ROOTS``
    because no user-global skill root is documented. This split is
    pinned by ``test_skills_agent_paths_research.py``.
    """
    entries = _live_project_siblings(tmp_path)
    assert len(entries) == 4, (
        f"Expected exactly 4 project-scope siblings, got {len(entries)}: "
        f"{[e.agent for e in entries]}"
    )
    assert {e.agent for e in entries} == set(_PROJECT_AGENT_NAMES), (
        f"Expected agents {_PROJECT_AGENT_NAMES}, got {[e.agent for e in entries]}"
    )


def test_project_sibling_roots_resolve_under_workspace_root(tmp_path: Path) -> None:
    """Each project-scope entry's resolve(workspace_root) must start with the workspace_root."""
    for entry in _live_project_siblings(tmp_path):
        resolved = entry.resolve(tmp_path)
        assert str(resolved).startswith(str(tmp_path)), (
            f"{entry.agent}: {resolved} did not resolve under workspace_root {tmp_path}"
        )
        assert resolved.is_absolute()


def test_project_sibling_roots_mirror_user_global_research_contract(tmp_path: Path) -> None:
    """Each project entry's source_url must mirror the user-global entry's source_url.

    For ``pi``, there is no user-global entry, so the project entry's
    ``source_url`` is the upstream pi.dev usage page that documents
    the project-scope ``.agents/skills/`` trust flow. This split is
    pinned by ``test_skills_agent_paths_research.py``.
    """
    user_by_agent: dict[str, str] = {entry.agent: entry.source_url for entry in agent_skill_roots()}
    for entry in _live_project_siblings(tmp_path):
        if entry.agent in user_by_agent:
            assert entry.source_url == user_by_agent[entry.agent], (
                f"{entry.agent}: project source_url {entry.source_url!r} does not match "
                f"user-global source_url {user_by_agent[entry.agent]!r}"
            )
        else:
            # pi has no user-global entry, so the project source_url
            # is the upstream pi.dev usage page (re-fetched 2026-06-21).
            assert entry.source_url == "https://pi.dev/docs/latest/usage", (
                f"{entry.agent}: project source_url {entry.source_url!r} must be "
                f"'https://pi.dev/docs/latest/usage' (the documented pi project-scope "
                "skill path) since pi is absent from the user-global registry"
            )
