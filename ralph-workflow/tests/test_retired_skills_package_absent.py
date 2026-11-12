from importlib.resources import files
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_mirror_freshness_audit_is_not_shipped() -> None:
    assert not files("ralph.testing").joinpath("audit_skill_mirror_freshness.py").is_file()


def test_artifact_skill_types_audit_is_shipped() -> None:
    assert files("ralph.testing").joinpath("audit_artifact_skill_types.py").is_file()


def test_skills_package_is_not_shipped() -> None:
    assert not (PACKAGE_ROOT / "skills-package").exists()


def test_no_skills_sync_tooling_is_shipped() -> None:
    assert not (PACKAGE_ROOT / "bin" / "sync-upstream-skills.js").exists()
