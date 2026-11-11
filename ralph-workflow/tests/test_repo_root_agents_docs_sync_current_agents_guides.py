from __future__ import annotations

import pytest

from tests.doc_roots import REPO_ROOT_DOCS_AGENTS_DIR

_CURRENT_GUIDES = [
    "verification.md",
    "testing-guide.md",
    "type-ignore-policy.md",
    "workspace-trait.md",
]


class TestCurrentAgentsGuides:
    @pytest.mark.parametrize("guide", _CURRENT_GUIDES)
    def test_guide_exists(self, guide: str) -> None:
        path = REPO_ROOT_DOCS_AGENTS_DIR / guide
        assert path.exists(), f"Current guide {guide} must exist at {path}"

    @pytest.mark.parametrize("guide", _CURRENT_GUIDES)
    def test_guide_has_python_content(self, guide: str) -> None:
        path = REPO_ROOT_DOCS_AGENTS_DIR / guide
        content = path.read_text().lower()
        assert "rust" not in content or "python" in content, (
            f"Guide {guide} appears to be Rust-only; should be Python-current"
        )
