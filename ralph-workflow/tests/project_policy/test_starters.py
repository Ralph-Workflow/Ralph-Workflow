"""Tests for the starter policy bundle loader."""

from __future__ import annotations

import pytest

from ralph.project_policy import markers, starters


def test_iter_starter_names_returns_twelve() -> None:
    names = list(starters.iter_starter_names())
    assert len(names) == 12


def test_iter_starter_names_covers_all_core_and_conditional() -> None:
    names = set(starters.iter_starter_names())
    for filename in markers.CORE_POLICY_FILES:
        assert filename in names
    for filename in markers.CONDITIONAL_POLICY_FILES.values():
        assert filename in names


def test_read_starter_returns_non_empty_content() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert content.strip(), f"starter {name} is empty"
        assert markers.POLICY_SCHEMA_MARKER in content
        assert f"{markers.POLICY_ID_PREFIX} {name} -->" in content


def test_starter_has_required_sections() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        for required in markers.REQUIRED_HEADINGS[name]:
            assert required in content, f"{name} missing heading: {required}"


def test_starter_has_research_basis_section() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert "## Research basis" in content


def test_starter_has_at_least_one_research_citation() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        # Every starter must include at least one citation block with
        # publisher, title, http URL, and review date.
        assert "publisher:" in content
        assert "title:" in content
        assert "http:" in content
        assert "review date:" in content


def test_starter_has_placeholder_fact_lines() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert markers.FACT_MARKER in content
        assert "PROJECT-FACT-UNRESOLVED" in content


def test_starter_has_command_or_inapplicable() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert markers.COMMAND_MARKER in content or markers.INAPPLICABLE_MARKER in content


def test_starter_does_not_contain_completion_marker() -> None:
    """A starter must NEVER ship with the completion marker."""
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert markers.COMPLETION_MARKER not in content, (
            f"starter {name} shipped with completion marker; that's a defect"
        )


def test_seed_starter_into_writes_only_when_absent() -> None:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    assert starters.seed_starter_into(ws, "testing-policy.md") is True
    # Second call is a no-op.
    assert starters.seed_starter_into(ws, "testing-policy.md") is False
    target = f"{markers.CANONICAL_DIR}testing-policy.md"
    assert ws.exists(target)


def test_seed_starter_into_preserves_existing_file() -> None:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    ws.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    target = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(target, "# already-customized\n\nDo not overwrite.\n")
    original = ws.read(target)
    starters.seed_starter_into(ws, "testing-policy.md")
    assert ws.read(target) == original


def test_read_starter_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError):
        starters.read_starter("not-a-starter.md")


def test_seed_starter_into_raises_for_unknown_name() -> None:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    with pytest.raises(ValueError):
        starters.seed_starter_into(ws, "not-a-starter.md")


def test_starters_are_free_of_known_content_corruption() -> None:
    """Regression guard: bundled starters must not ship with corrupted completion-marker text.

    Asserts only on exact corruption fragments so legitimate required prose
    (e.g. the word ``placeholder`` or ``the theme``) is permitted.
    """
    corruption_fragments = (
        "comment identifier comment",  # garbled marker paraphrase
        "resolved).laceholder is",  # exact malformed tail in testing-policy.md
        "the the ",  # doubled-word corruption (trailing space excludes 'the theme')
    )
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        for fragment in corruption_fragments:
            assert fragment not in content, (
                f"starter {name} contains known corruption fragment {fragment!r}"
            )


def test_starter_ralph_markers_reference_completion_marker_cleanly() -> None:
    """Every starter must name the completion marker in prose WITHOUT embedding the literal token.

    Names the marker so validators can find it; the literal ``<!-- ralph-policy-complete -->``
    comment must stay absent so a freshly seeded starter still validates as INCOMPLETE by design.
    """
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert "ralph-policy-complete" in content, (
            f"starter {name} does not reference the completion marker name"
        )
        assert markers.COMPLETION_MARKER not in content, (
            f"starter {name} embedded the literal completion marker token"
        )
