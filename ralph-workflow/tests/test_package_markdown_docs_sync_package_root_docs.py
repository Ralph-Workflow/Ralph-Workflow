from __future__ import annotations

from tests.doc_roots import PACKAGE_ROOT

_PACKAGE_MD_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
]


class TestPackageRootDocs:
    def test_readme_points_to_sphinx_entry(self) -> None:
        path = PACKAGE_ROOT / "README.md"
        content = path.read_text()
        assert "sphinx" in content.lower() or "docs" in content.lower()

    def test_contributing_has_verification_section(self) -> None:
        path = PACKAGE_ROOT / "CONTRIBUTING.md"
        content = path.read_text()
        assert "verify" in content.lower() or "test" in content.lower()
