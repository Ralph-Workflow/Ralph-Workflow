import shutil
from importlib.resources import files
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

UNIVERSAL_SKILL_NAMES = (
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
)

RETIRED_BUNDLE_TERMS = (
    "29 skills",
    "29 default skills",
    "skills-package",
    "sync-upstream-skills",
)

OWNING_DOCS = ("README.md", "CONTRIBUTING.md", "CHANGELOG.md")

CURRENT_FACING_DOCS = ("README.md", "CONTRIBUTING.md")


def test_mirror_freshness_audit_is_not_shipped() -> None:
    assert not files("ralph.testing").joinpath("audit_skill_mirror_freshness.py").is_file()


def test_artifact_skill_types_audit_is_shipped() -> None:
    assert files("ralph.testing").joinpath("audit_artifact_skill_types.py").is_file()


def test_skills_package_is_not_shipped() -> None:
    assert not (PACKAGE_ROOT / "skills-package").exists()


def test_no_skills_sync_tooling_is_shipped() -> None:
    assert not (PACKAGE_ROOT / "bin" / "sync-upstream-skills.js").exists()


def _package_doc(tmp_path: Path, name: str) -> str:
    snapshot = tmp_path / name
    shutil.copyfile(PACKAGE_ROOT / name, snapshot)
    return snapshot.read_text(encoding="utf-8")


def test_owning_docs_state_the_current_default_skill_model(tmp_path: Path) -> None:
    for name in OWNING_DOCS:
        text = " ".join(_package_doc(tmp_path, name).split())
        assert "adding a skill now means adding an artifact type" in text, (
            f"{name} must state that adding a skill means adding an artifact type"
        )
        for skill_name in UNIVERSAL_SKILL_NAMES:
            assert skill_name in text, f"{name} must list the universal skill {skill_name}"


def test_current_facing_docs_do_not_name_the_retired_skill_bundle(tmp_path: Path) -> None:
    for name in CURRENT_FACING_DOCS:
        text = _package_doc(tmp_path, name)
        for term in RETIRED_BUNDLE_TERMS:
            assert term not in text, (
                f"{name} must not present the retired skill bundle as current: {term}"
            )
