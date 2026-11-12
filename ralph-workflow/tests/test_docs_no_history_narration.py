from __future__ import annotations

from pathlib import Path

import pytest

from tests.doc_roots import REPOSITORY_ROOT

_FORBIDDEN_HISTORY_BY_DOCUMENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ralph-workflow/docs/agents/architecture.md",
        (
            "used to be duplicated",
            "were migrated onto those",
            "refactor is provably no-op",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/recovery.md",
        (
            "used to author",
            "used to call",
            "would have let one lifecycle fact disagree",
            "both are now closed",
            "were migrated onto the",
        ),
    ),
    (
        "ralph-workflow/docs/agents/prompt-surface-changes.md",
        (
            "previously supported a worker-orchestration model",
            "for why it was removed",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/concepts.md",
        (
            "previously supported a worker-orchestration model",
            "Parallelism is now owned",
            "were never",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/pro-support.md",
        (
            "historically assumed",
            "In practice, an engine instance may",
        ),
    ),
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative_path", "forbidden_texts"),
    _FORBIDDEN_HISTORY_BY_DOCUMENT,
    ids=[relative_path for relative_path, _ in _FORBIDDEN_HISTORY_BY_DOCUMENT],
)
def test_documentation_states_no_history_narration(
    relative_path: str,
    forbidden_texts: tuple[str, ...],
) -> None:
    text = _read(REPOSITORY_ROOT / relative_path)
    still_present = [forbidden_text for forbidden_text in forbidden_texts if forbidden_text in text]
    assert not still_present, f"{relative_path} still carries history narration: {still_present}"
