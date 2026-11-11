from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest

from ralph.project_policy import markers, starters


def test_iter_starter_names_returns_twenty_two() -> None:
    names = list(starters.iter_starter_names())
    assert len(names) == 22


def test_architecture_policy_is_a_core_starter() -> None:
    assert "architecture-policy.md" in markers.CORE_POLICY_FILES
    assert "architecture-policy.md" in set(starters.iter_starter_names())


def test_security_policy_is_a_core_starter_with_threat_surfaces() -> None:
    assert "security-policy.md" in markers.CORE_POLICY_FILES
    assert "security-policy.md" in set(starters.iter_starter_names())
    assert "Threat surfaces" in markers.REQUIRED_HEADINGS["security-policy.md"]


def test_testing_policy_requires_a_suite_admission_section() -> None:
    assert "Suite admission" in markers.REQUIRED_HEADINGS["testing-policy.md"]
    content = starters.read_starter("testing-policy.md")
    assert "## Suite admission" in content


def test_verification_policy_requires_a_gate_lanes_section() -> None:
    assert "Gate lanes" in markers.REQUIRED_HEADINGS["verification-policy.md"]
    content = starters.read_starter("verification-policy.md")
    assert "## Gate lanes" in content


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


def test_read_starter_returns_cached_bundled_content_by_name() -> None:
    first = starters.read_starter("testing-policy.md")
    second = starters.read_starter("testing-policy.md")

    assert first is second


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
        assert "publisher:" in content
        assert "title:" in content
        assert "http:" in content
        assert "review date:" in content


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _extract_research_basis_section(content: str) -> str:
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
    blocks: list[str] = []
    for raw_block in research_basis_body.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        if "http:" in block:
            blocks.append(block)
    return blocks


def test_starter_citations_are_structurally_valid() -> None:
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
            assert parsed.scheme == "https", f"{name} citation URL must use https: {url!r}"
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


_RECOMMENDED_GATE_COMMANDS: dict[str, tuple[str, ...]] = {
    "execution-kernel.md": (),
    "testing-policy.md": (),
    "typechecking-policy.md": (),
    "linting-policy.md": (),
    "dependency-policy.md": (),
    "verification-policy.md": (),
    "gate-script-policy.md": ("shellcheck",),
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


def test_testing_policy_has_browser_time_budget_rule() -> None:
    content = starters.read_starter("testing-policy.md")
    assert "BROWSER-TIME BUDGET" in content
    assert "browser_budget_seconds" in content
    assert "aggregate ceiling" in content.lower()
    assert "default_budget_seconds" in content
    assert "never raised" in content or "never" in content


def test_testing_policy_browser_budget_two_states_are_unambiguous() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "BELOW the measured budget" in normalized
    assert "AT or OVER the measured budget" in normalized
    assert "WITHOUT any mandatory time offset" in normalized
    assert "BOTH a time offset" in normalized
    assert "distinct-risk justification" in normalized
    assert "never raised" in normalized
    assert "browser-time carve-out" in normalized or "carve-out" in normalized


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
    assert set(_RECOMMENDED_GATE_COMMANDS) == set(starters.iter_starter_names())


@pytest.mark.parametrize("name", sorted(starters.iter_starter_names()))
def test_starter_recommended_gate_tools_are_validator_approved(name: str) -> None:
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
    content = starters.read_starter(name)
    lines = content.splitlines()
    lang_lines = [idx for idx, line in enumerate(lines) if line.startswith(markers.LANG_MARKER)]
    if len(lang_lines) <= 1:
        return
    marker_lines = [idx for idx, line in enumerate(lines) if line.startswith("<!-- REPLACE-ME:")]
    assert marker_lines, (
        f"{name} ships {len(lang_lines)} RALPH-LANG template blocks but no "
        "<!-- REPLACE-ME: --> section marker"
    )
    assert min(marker_lines) < min(lang_lines), (
        f"{name}: the REPLACE-ME section marker must precede the first "
        "RALPH-LANG template block it governs"
    )


def test_replace_me_token_is_a_validator_placeholder() -> None:
    assert "REPLACE-ME" in markers.PLACEHOLDER_TOKENS


def _h2_sections(content: str) -> list[tuple[str, str]]:
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
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert "ralph-policy-complete" not in content, (
            f"starter {name} references the retired completion marker"
        )


def test_seed_starter_into_writes_only_when_absent() -> None:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    assert starters.seed_starter_into(ws, "testing-policy.md") is True
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
    corruption_fragments = (
        "comment identifier comment",
        "resolved).laceholder is",
        "the the ",
    )
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        for fragment in corruption_fragments:
            assert fragment not in content, (
                f"starter {name} contains known corruption fragment {fragment!r}"
            )


def test_portfolio_starter_is_bundled_and_parseable() -> None:
    from ralph.project_policy import parse_portfolio_toml

    content = starters.read_starter("policy-portfolio.toml")
    portfolio = parse_portfolio_toml(content)

    assert portfolio.schema_version == markers.PORTFOLIO_SCHEMA_VERSION
    assert portfolio.default_cost_seconds <= portfolio.default_budget_seconds


def test_seed_portfolio_into_writes_only_when_absent() -> None:
    from ralph.workspace.memory import MemoryWorkspace

    ws = MemoryWorkspace()
    assert starters.seed_starter_into(ws, "policy-portfolio.toml") is True
    original = ws.read(markers.PORTFOLIO_PATH)
    ws.write(markers.PORTFOLIO_PATH, "customized")
    assert starters.seed_starter_into(ws, "policy-portfolio.toml") is False
    assert ws.read(markers.PORTFOLIO_PATH) == "customized"
    assert original


def test_starter_names_set_equals_disk_listing() -> None:
    from pathlib import Path

    starters_dir = Path(starters.__file__).parent
    disk_md_names = {p.name for p in starters_dir.iterdir() if p.suffix == ".md"}
    assert set(starters.iter_starter_names()) == disk_md_names
    disk_all_names = {p.name for p in starters_dir.iterdir()}
    assert "policy-portfolio.toml" in disk_all_names


def test_all_starters_are_v6_schema_with_seven_column_guard_inventories() -> None:
    for name in starters.iter_starter_names():
        content = starters.read_starter(name)
        assert content.splitlines()[0] == markers.POLICY_SCHEMA_MARKER
        assert (
            "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |"
            in content
        )
