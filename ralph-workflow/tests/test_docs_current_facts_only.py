from __future__ import annotations

from pathlib import Path

import pytest

from tests.doc_roots import REPOSITORY_ROOT

_FORBIDDEN_TEXT_BY_DOCUMENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "docs/agents/verification.md",
        (
            "Documentation Review Note",
            "What changed.",
        ),
    ),
    (
        "ralph-workflow/docs/agents/verification.md",
        ("used to hold an older variant",),
    ),
    (
        "ralph-workflow/CONTRIBUTING.md",
        ("missing docstring",),
    ),
    (
        "docs/code-style/documentation-rubric.md",
        ("Required review note",),
    ),
    (
        "docs/superpowers/specs/2026-07-14-telemetry-identity-lifecycle-design.md",
        ("Documentation review note:",),
    ),
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative_path", "forbidden_texts"),
    _FORBIDDEN_TEXT_BY_DOCUMENT,
    ids=[relative_path for relative_path, _ in _FORBIDDEN_TEXT_BY_DOCUMENT],
)
def test_documentation_states_current_facts_only(
    relative_path: str,
    forbidden_texts: tuple[str, ...],
) -> None:
    text = _read(REPOSITORY_ROOT / relative_path)
    still_present = [forbidden_text for forbidden_text in forbidden_texts if forbidden_text in text]
    assert not still_present, f"{relative_path} still carries narration text: {still_present}"
