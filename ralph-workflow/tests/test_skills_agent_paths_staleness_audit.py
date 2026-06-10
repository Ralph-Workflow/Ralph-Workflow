"""Audit test: every entry in AGENT_SKILL_ROOTS must have a fresh ``last_verified_iso``.

The supported-agent registry at ``ralph/skills/_agent_paths.py`` is the single
source of truth for the user-global skill-discovery root of every supported
agent. It is critical infrastructure: if a path moves, every Ralph Workflow
user's auto-seed breaks silently. The registry carries a
``last_verified_iso`` field for each entry so a future maintainer can
see when the upstream documentation was last re-confirmed.

This test enforces a 180-day staleness ceiling (matching the contract in the
module docstring of ``_agent_paths.py``). A future maintainer who changes
``path_segments`` or ``source_url`` MUST bump ``last_verified_iso`` in the
same commit or the audit will fail and block the change.

The test also asserts that the ``source_url`` for each entry is a
non-empty http(s) URL — a maintainer who empties a URL is effectively
deleting the audit evidence, which is a regression we catch here.
"""

from __future__ import annotations

from datetime import date

from ralph.skills._agent_paths import AGENT_SKILL_ROOTS

_STALENESS_CEILING_DAYS = 180


def test_agent_skill_roots_last_verified_iso_is_within_staleness_window() -> None:
    """No registered user-global AgentSkillRoot may be more than 180 days stale."""
    today = date.today()
    for entry in AGENT_SKILL_ROOTS:
        verified = date.fromisoformat(entry.last_verified_iso)
        age_days = (today - verified).days
        assert age_days <= _STALENESS_CEILING_DAYS, (
            f"AGENT_SKILL_ROOTS entry for {entry.agent!r} has a stale "
            f"last_verified_iso={entry.last_verified_iso!r} (age={age_days} days, "
            f"ceiling={_STALENESS_CEILING_DAYS} days). Re-verify the source_url "
            f"and bump the date in ralph/skills/_agent_paths.py. The source_url "
            f"for this entry is {entry.source_url!r}."
        )


def test_agent_skill_roots_source_url_is_non_empty_http_url() -> None:
    """Every registered user-global AgentSkillRoot must carry a non-empty http(s) URL.

    A maintainer who empties the source_url is silently deleting the audit
    evidence; this assertion makes that regression visible.
    """
    for entry in AGENT_SKILL_ROOTS:
        assert entry.source_url, (
            f"AGENT_SKILL_ROOTS entry for {entry.agent!r} has an empty source_url. "
            f"Re-verify the upstream documentation and populate the URL."
        )
        assert entry.source_url.startswith(("http://", "https://")), (
            f"AGENT_SKILL_ROOTS entry for {entry.agent!r} has a non-http(s) "
            f"source_url: {entry.source_url!r}"
        )


def test_agent_skill_roots_last_verified_iso_is_well_formed() -> None:
    """The last_verified_iso must parse as a YYYY-MM-DD date for every entry."""
    for entry in AGENT_SKILL_ROOTS:
        parsed = date.fromisoformat(entry.last_verified_iso)
        assert parsed.year >= 2020, (
            f"AGENT_SKILL_ROOTS entry for {entry.agent!r} has an implausibly "
            f"old last_verified_iso: {entry.last_verified_iso!r}"
        )
