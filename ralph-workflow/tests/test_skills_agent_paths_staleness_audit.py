from __future__ import annotations

from datetime import date

from ralph.skills._agent_paths import AGENT_SKILL_ROOTS

_STALENESS_CEILING_DAYS = 180


def test_agent_skill_roots_last_verified_iso_is_within_staleness_window() -> None:
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
    for entry in AGENT_SKILL_ROOTS:
        parsed = date.fromisoformat(entry.last_verified_iso)
        assert parsed.year >= 2020, (
            f"AGENT_SKILL_ROOTS entry for {entry.agent!r} has an implausibly "
            f"old last_verified_iso: {entry.last_verified_iso!r}"
        )
