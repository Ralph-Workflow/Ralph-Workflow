from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_skills_package_is_not_shipped() -> None:
    assert not (PACKAGE_ROOT / "skills-package").exists()
