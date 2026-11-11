from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_no_skills_sync_tooling_is_shipped() -> None:
    assert not (PACKAGE_ROOT / "skills-package").exists()
    assert not (PACKAGE_ROOT / "bin" / "sync-upstream-skills.js").exists()
