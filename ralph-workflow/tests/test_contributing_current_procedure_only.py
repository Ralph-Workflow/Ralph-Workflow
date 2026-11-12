from __future__ import annotations

import pytest

from tests.doc_roots import REPOSITORY_ROOT

_CONTRIBUTING_PATH = "ralph-workflow/CONTRIBUTING.md"

_FORBIDDEN_TEXT: tuple[str, ...] = (
    "# ...make your change...",
    "# create/refresh the self-contained dev snapshot",
    "# dogfood the AGENTS.md commit rule",
    "# docs/drift",
    "# declared audit",
    "# make test under the immutable 60-second budget",
    "open a PR",
    "push your branch",
    "pull request",
    "pull-request",
    "PR description",
    "small PRs",
    "A future PR",
    "PR templates",
    "Woodpecker",
    "Update public module/package docstrings",
    "Keep exported package docstrings",
    "missing docstring",
    "POLICY COMPLETENESS` comment",
    "Project-policy schema v4",
    "Existing schema v3 installations",
    "Upgrade seeds the manifest and v4 policies",
)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT)
def test_contributing_states_current_procedure_only(forbidden_text: str) -> None:
    path = REPOSITORY_ROOT / _CONTRIBUTING_PATH
    assert path.is_file(), f"expected shipped document at {path}"
    text = path.read_text(encoding="utf-8")
    assert forbidden_text not in text, (
        f"{_CONTRIBUTING_PATH} still carries guidance the shipped policy "
        f"forbids: {forbidden_text!r}"
    )
