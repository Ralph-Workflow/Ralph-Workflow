"""Tests for the starter policy bundle loader."""

from __future__ import annotations

import re
from urllib.parse import urlparse

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


# Regex for an ISO review date (YYYY-MM-DD) -- the format every starter
# citation block uses and the canonical validator checks for.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _extract_research_basis_section(content: str) -> str:
    """Return the body of the '## Research basis' section, or '' if absent."""
    lines = content.splitlines()
    start_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == "## Research basis":
            start_idx = idx + 1
            break
    if start_idx < 0:
        return ""
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        if lines[idx].startswith("## "):
            end_idx = idx
            break
    return "\n".join(lines[start_idx:end_idx])


def _extract_citation_blocks(research_basis_body: str) -> list[str]:
    """Split a '## Research basis' body on blank lines, keep blocks containing 'http:'."""
    blocks: list[str] = []
    for raw_block in research_basis_body.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        if "http:" in block:
            blocks.append(block)
    return blocks


def test_starter_citations_are_structurally_valid() -> None:
    """Offline deterministic guard: every Research basis citation is structurally valid.

    The on-demand ``make policy-citation-linkcheck`` target verifies that every
    cited URL actually resolves (HTTP < 400); this offline test guards the
    SHAPE of each citation block so silent rot -- a missing field, a non-https
    URL, a malformed date -- fails fast inside the timed 60s suite instead of
    only on a manual network gate.

    For each starter: slice the '## Research basis' section, split into
    citation blocks on blank lines, keep blocks containing 'http:', and
    assert every such block carries every required field plus an https URL
    with a non-empty host and an ISO ``YYYY-MM-DD`` review date.
    """
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        research_basis = _extract_research_basis_section(content)
        assert research_basis, f"{name} is missing the '## Research basis' section"

        blocks = _extract_citation_blocks(research_basis)
        assert blocks, f"{name} has no citation blocks in '## Research basis'"

        for block in blocks:
            for field in markers.CITATION_REQUIRED_FIELDS:
                assert field in block, (
                    f"{name} citation block is missing required field {field!r}:\n{block}"
                )

            http_line = next(
                (line.strip() for line in block.splitlines() if line.strip().startswith("http:")),
                None,
            )
            assert http_line is not None, f"{name} citation block has no http: line:\n{block}"
            url = http_line.split("http:", 1)[1].strip()
            parsed = urlparse(url)
            assert parsed.scheme == "https", (
                f"{name} citation URL must use https: {url!r}"
            )
            assert parsed.netloc, f"{name} citation URL must have a non-empty host: {url!r}"

            review_line = next(
                (
                    line.strip()
                    for line in block.splitlines()
                    if line.strip().startswith("review date:")
                ),
                None,
            )
            assert review_line is not None, (
                f"{name} citation block has no review date: line:\n{block}"
            )
            review_value = review_line.split("review date:", 1)[1].strip()
            assert _ISO_DATE_RE.match(review_value), (
                f"{name} citation review date must be ISO YYYY-MM-DD, got {review_value!r}"
            )


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
