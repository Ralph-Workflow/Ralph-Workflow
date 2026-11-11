"""The skills-package sync tooling is gone, and nothing replaced it.

The feature is mostly deletion: no scheduled job, manifest or sync script is
introduced. The universal skills arrive through the runtime updater, and the
artifact skills are derived from the artifact-type registry.

# RALPH-TEST-REASON: the separate skills package and its sync script are removed by this change; the upstream skills arrive through the runtime updater covered by tests/test_skills_upstream_update.py.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_no_skills_sync_tooling_is_shipped() -> None:
    """Neither the package directory nor a replacement sync script may exist."""
    assert not (PACKAGE_ROOT / "skills-package").exists()
    assert not (PACKAGE_ROOT / "bin" / "sync-upstream-skills.js").exists()
