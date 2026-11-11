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

_PROJECT_AGENT_NAMES: tuple[str, ...] = ("claude", "codex", "agy", "pi")


def _live_project_siblings(workspace_root: Path) -> list[ProjectAgentSkillRoot]:
    return list(project_sibling_skill_roots(workspace_root))


def test_project_sibling_roots_have_non_empty_source_urls(tmp_path: Path) -> None:
    for entry in _live_project_siblings(tmp_path):
        assert entry.source_url, f"{entry.agent}: source_url must be non-empty"
        scheme = urlparse(entry.source_url).scheme
        assert scheme in {"http", "https"}, (
            f"{entry.agent}: source_url {entry.source_url!r} has scheme {scheme!r}, "
            "must be http or https"
        )


def test_project_sibling_roots_have_last_verified_iso(tmp_path: Path) -> None:
    for entry in _live_project_siblings(tmp_path):
        date = getattr(entry, "last_verified_iso", "")
        assert _LAST_VERIFIED_ISO_RE.match(date), (
            f"{entry.agent}: last_verified_iso {date!r} is not a well-formed YYYY-MM-DD date"
        )


def test_project_sibling_roots_count_is_four(tmp_path: Path) -> None:
    entries = _live_project_siblings(tmp_path)
    assert len(entries) == 4, (
        f"Expected exactly 4 project-scope siblings, got {len(entries)}: "
        f"{[e.agent for e in entries]}"
    )
    assert {e.agent for e in entries} == set(_PROJECT_AGENT_NAMES), (
        f"Expected agents {_PROJECT_AGENT_NAMES}, got {[e.agent for e in entries]}"
    )


def test_project_sibling_roots_resolve_under_workspace_root(tmp_path: Path) -> None:
    for entry in _live_project_siblings(tmp_path):
        resolved = entry.resolve(tmp_path)
        assert str(resolved).startswith(str(tmp_path)), (
            f"{entry.agent}: {resolved} did not resolve under workspace_root {tmp_path}"
        )
        assert resolved.is_absolute()


def test_project_sibling_roots_mirror_user_global_research_contract(tmp_path: Path) -> None:
    user_by_agent: dict[str, str] = {entry.agent: entry.source_url for entry in agent_skill_roots()}
    for entry in _live_project_siblings(tmp_path):
        if entry.agent in user_by_agent:
            assert entry.source_url == user_by_agent[entry.agent], (
                f"{entry.agent}: project source_url {entry.source_url!r} does not match "
                f"user-global source_url {user_by_agent[entry.agent]!r}"
            )
        else:
            assert entry.source_url == "https://pi.dev/docs/latest/usage", (
                f"{entry.agent}: project source_url {entry.source_url!r} must be "
                f"'https://pi.dev/docs/latest/usage' (the documented pi project-scope "
                "skill path) since pi is absent from the user-global registry"
            )
