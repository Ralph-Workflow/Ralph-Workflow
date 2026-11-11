from __future__ import annotations

from tests.doc_roots import REPO_ROOT_README

_SPHINX_PAGES = [
    "getting-started.md",
    "concepts.md",
    "configuration.md",
    "developer-internals.md",
]

_MIN_CONTENT_CHARS = 200
_MIN_DEFINED_NOUNS = 2
_MIN_REFERENCE_CHARS = 500


class TestRepoRootCompleteness:
    def test_readme_has_install_instructions(self) -> None:
        content = REPO_ROOT_README.read_text().lower()
        has_install = any(phrase in content for phrase in ["install", "pip", "uv", "pipx", "clone"])
        assert has_install, "Repo-root README should have install instructions"

    def test_readme_has_verify_reference(self) -> None:
        content = REPO_ROOT_README.read_text().lower()
        assert "verify" in content or "test" in content, (
            "Repo-root README should reference verification"
        )
