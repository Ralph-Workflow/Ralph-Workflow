"""Tests for the starter policy bundle loader."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest

from ralph.project_policy import markers, starters


def test_iter_starter_names_returns_twenty() -> None:
    names = list(starters.iter_starter_names())
    assert len(names) == 20


def test_architecture_policy_is_a_core_starter() -> None:
    assert "architecture-policy.md" in markers.CORE_POLICY_FILES
    assert "architecture-policy.md" in set(starters.iter_starter_names())


def test_security_policy_is_a_core_starter_with_threat_surfaces() -> None:
    """Security applies to every project (secrets, untrusted input), so the
    security policy ships as a CORE starter. Its content is app-type-specific
    (strcpy bans vs CSRF defenses), so the 'Threat surfaces' section that
    carries the project-specific rules is a REQUIRED heading: it must survive
    every future amendment of the project's customized policy."""
    assert "security-policy.md" in markers.CORE_POLICY_FILES
    assert "security-policy.md" in set(starters.iter_starter_names())
    assert "Threat surfaces" in markers.REQUIRED_HEADINGS["security-policy.md"]


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


# Gate tools each starter's prose recommends to the remediation agent. A
# faithful agent copies these into RALPH-COMMAND lines, so every entry MUST
# pass the validator's first-token allowlist — otherwise the starter's own
# advice burns a remediation attempt on an RWP-CMD:unapproved finding.
# Keys must cover EVERY starter (completeness is asserted) so a new or
# edited starter is forced to declare its recommendations here.
# KNOWN LIMITATION: the guard is one-directional — a NEW tool
# recommendation added to a starter's prose without an inventory entry is
# not detected automatically (prose scanning is deliberately avoided);
# reviewers must update this inventory when starter advice changes.
_RECOMMENDED_GATE_COMMANDS: dict[str, tuple[str, ...]] = {
    "testing-policy.md": (),
    "typechecking-policy.md": (),
    "linting-policy.md": (),
    "dependency-policy.md": (),
    "verification-policy.md": (),
    "agent-policy.md": (),
    "clean-code-policy.md": (),
    "documentation-policy.md": (),
    "security-policy.md": (),
    "architecture-policy.md": (),
    "design-system-policy.md": (),
    "ux-policy.md": (),
    "accessibility-policy.md": (),
    "api-compatibility-policy.md": (),
    "data-storage-policy.md": (),
    "reliability-observability-policy.md": (),
    "privacy-policy.md": (),
    "release-deployment-policy.md": (),
    "performance-policy.md": (),
    "memory-usage-policy.md": (),
}


def test_standard_quality_gates_are_mandatory_when_supported() -> None:
    testing = starters.read_starter("testing-policy.md")
    typechecking = starters.read_starter("typechecking-policy.md")
    linting = starters.read_starter("linting-policy.md")
    verification = starters.read_starter("verification-policy.md")

    assert "Automated testing is mandatory" in testing
    assert "supports a suitable maintained" in typechecking
    assert "MUST select and run one" in typechecking
    assert "supports a suitable maintained" in linting
    assert "MUST select and run one" in linting
    assert "formatting" in linting.lower()
    assert "preference" in verification.lower()
    assert "does not make a supported gate inapplicable" in verification


def test_typechecking_policy_does_not_prescribe_products_or_exclude_first_party_code() -> None:
    content = starters.read_starter("typechecking-policy.md")
    defaults = content.split("## Default requirements", 1)[1].split(
        "## Project facts to resolve", 1
    )[0]

    for product in ("mypy", "pyright", "tsc", "cargo check", "go build"):
        assert product not in defaults
    assert "migration code MUST be excluded" not in content
    assert "compatibility code MUST be excluded" not in content


def test_linting_policy_has_zero_tolerance_for_dead_code() -> None:
    content = starters.read_starter("linting-policy.md")
    normalized = " ".join(content.split())
    assert "## Dead code — zero tolerance" in content
    assert "Dead code is prohibited" in content
    assert "better to delete obsolete code and implement it again" in normalized
    assert "fake references" in content
    assert "MUST be removed" in content


def test_typechecking_dead_code_findings_cannot_be_suppressed() -> None:
    content = starters.read_starter("typechecking-policy.md")
    normalized = " ".join(content.split())
    assert "Verified dead code MUST be removed" in normalized
    assert "evidence-backed" in normalized
    assert "not a dead-code exception" in normalized


def test_recommended_gate_tools_inventory_covers_every_starter() -> None:
    """The starter-advice-vs-allowlist check must run for EVERY policy
    document: a starter absent from the inventory silently escapes it."""
    assert set(_RECOMMENDED_GATE_COMMANDS) == set(starters.iter_starter_names())


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_recommended_gate_tools_are_validator_approved(name: str) -> None:
    """Every gate tool a starter recommends must clear the validator's
    APPROVED_GATE_TOOLS first-token allowlist, and must actually appear in
    the starter text (staleness guard for this inventory)."""
    content = starters.read_starter(name)
    for command in _RECOMMENDED_GATE_COMMANDS[name]:
        assert command in content, (
            f"{name}: inventory entry {command!r} no longer appears in the "
            "starter; update _RECOMMENDED_GATE_COMMANDS"
        )
        first_token = command.split(None, 1)[0]
        assert first_token in markers.APPROVED_GATE_TOOLS, (
            f"{name} recommends {command!r} but {first_token!r} is not in "
            "markers.APPROVED_GATE_TOOLS; a remediation agent following the "
            "starter's advice would be rejected with RWP-CMD:unapproved-cmd"
        )


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_multi_language_template_blocks_carry_replace_me_marker(name: str) -> None:
    """A starter file can have MULTIPLE sections that are templates to adapt,
    not just the top-of-file banner. Every per-language template block (a
    starter shipping more than one RALPH-LANG block cannot match any single
    real project) must be preceded by a `<!-- REPLACE-ME:` comment carrying
    its own in-place instruction. `REPLACE-ME` is a validator placeholder
    token, so the marker is machine-enforced: readiness stays blocked until
    the section is adapted and the comment deleted — instructions live at
    the section instead of bloating the remediation prompt."""
    content = starters.read_starter(name)
    lines = content.splitlines()
    lang_lines = [
        idx for idx, line in enumerate(lines) if line.startswith(markers.LANG_MARKER)
    ]
    if len(lang_lines) <= 1:
        return  # single/no language block: nothing speculative to adapt.
    marker_lines = [
        idx for idx, line in enumerate(lines) if line.startswith("<!-- REPLACE-ME:")
    ]
    assert marker_lines, (
        f"{name} ships {len(lang_lines)} RALPH-LANG template blocks but no "
        "<!-- REPLACE-ME: --> section marker"
    )
    assert min(marker_lines) < min(lang_lines), (
        f"{name}: the REPLACE-ME section marker must precede the first "
        "RALPH-LANG template block it governs"
    )


def test_replace_me_token_is_a_validator_placeholder() -> None:
    """The section marker is only trustworthy if the validator enforces its
    deletion: REPLACE-ME must be a placeholder token."""
    assert "REPLACE-ME" in markers.PLACEHOLDER_TOKENS


def _h2_sections(content: str) -> list[tuple[str, str]]:
    """Split starter content into (heading, body) pairs per H2 section."""
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = line[3:].strip()
            current_body = []
        elif current_heading is not None:
            current_body.append(line)
    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_body)))
    return sections


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_every_unresolved_section_carries_replace_me_guidance(name: str) -> None:
    """Weaker remediation agents need guidance AT the point of work: every
    H2 section that still carries unresolved data (PROJECT-FACT-UNRESOLVED)
    must also carry a `<!-- REPLACE-ME:` comment with in-place instructions
    — including what to record when the project is too young for the data
    to exist yet (best current answer plus the condition that settles it).
    The comment self-destructs: REPLACE-ME is a validator placeholder, so
    readiness stays blocked until the section is resolved and the comment
    deleted."""
    content = starters.read_starter(name)
    for heading, body in _h2_sections(content):
        if "PROJECT-FACT-UNRESOLVED" not in body:
            continue
        assert "<!-- REPLACE-ME:" in body, (
            f"{name}: section '{heading}' carries unresolved data but no "
            "<!-- REPLACE-ME: --> guidance comment for the remediation agent"
        )


def test_starter_has_placeholder_fact_lines() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert markers.FACT_MARKER in content
        assert "PROJECT-FACT-UNRESOLVED" in content


def test_starter_has_command_or_inapplicable() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert (
            markers.COMMAND_MARKER in content
            or markers.REVIEW_MARKER in content
            or markers.INAPPLICABLE_MARKER in content
        )


def test_starter_never_mentions_a_completion_marker() -> None:
    """Completion is the ABSENCE of unresolved markers (banner, REPLACE-ME
    comments, placeholder tokens) — there is no completion certification
    comment for an agent to add, so no starter may reference one. This is
    simpler and more honest: a marker asserts more than the deterministic
    validator can check, while absence-of-markers is exactly what it
    checks."""
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert "ralph-policy-complete" not in content, (
            f"starter {name} references the retired completion marker"
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
