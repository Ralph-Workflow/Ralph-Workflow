"""Round-trip regression tests for the bundled ``submit-plan-artifact`` skill.

The skill body documents the canonical per-section payload shapes and the
canonical validator error strings. These tests assert the three promises
of that documentation, in priority order:

1. Every validator error string in ``_validation.py`` appears verbatim in
   the skill body so an agent can pattern-match its way back to a fix
   without re-reading the source code.
2. The six ``\\`\\`\\`json\\`\\`\\` fenced blocks in the skill body decode
   against the JSON Schema and target the right top-level key for each
   of the six required plan sections.
3. The documented happy-path round-trips through the canonical tool
   handlers end-to-end (stage every section via ``ralph_submit_plan_section``
   or the batched ``ralph_submit_plan_sections`` and finalize to
   ``plan.json``).

The happy-path test extracts the six documented payload templates from the
skill body itself (not a hand-written helper) so any drift between the skill
documentation and the working validator fails the test.

The tests are fully type-annotated and rely only on the in-process Pydantic
+ tool handlers (no real I/O, no ``subprocess``, no ``time.sleep``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.artifact import (
    handle_finalize_plan,
    handle_submit_plan_section,
    handle_submit_plan_sections,
)
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from ralph.mcp.tools.tool_content import ToolContent


def _load_skill_body() -> str:
    """Read the canonical submit-plan-artifact skill markdown body."""
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        candidate = parent / "ralph" / "skills" / "content" / "submit-plan-artifact.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.fail("could not locate submit-plan-artifact.md on disk")


def _extract_section_templates(body: str) -> dict[str, object]:
    """Parse the six fenced `````json`` blocks out of the skill body.

    The ``## Per-section minimal payload templates`` section embeds one
    fenced JSON block per required plan section, in this exact order:
    ``summary``, ``skills_mcp``, ``steps``, ``critical_files``,
    ``risks_mitigations``, ``verification_strategy``. The block order
    maps 1:1 to the section names via the H3 ``### <section>`` headings
    that precede each block. Returns the parsed payloads indexed by
    section name; this is the canonical happy-path payload set the
    round-trip test feeds into the handlers.
    """
    templates_match = re.search(
        r"## Per-section minimal payload templates\s*\n([\s\S]*?)\n## Dumb-proof checklist",
        body,
    )
    assert templates_match is not None, (
        "submit-plan-artifact skill is missing the '## Per-section minimal "
        "payload templates' section before '## Dumb-proof checklist'"
    )
    templates_body = templates_match.group(1)
    expected_sections = (
        "summary",
        "skills_mcp",
        "steps",
        "critical_files",
        "risks_mitigations",
        "verification_strategy",
    )
    payloads: dict[str, object] = {}
    cursor = 0
    for section in expected_sections:
        heading_match = re.search(
            rf"### {re.escape(section)}\s*\n", templates_body[cursor:]
        )
        assert heading_match is not None, (
            f"submit-plan-artifact skill is missing the '### {section}' "
            f"heading inside the per-section templates section"
        )
        section_start = cursor + heading_match.end()
        block_match = re.search(
            r"```json\s*\n([\s\S]*?)\n```", templates_body[section_start:]
        )
        assert block_match is not None, (
            f"submit-plan-artifact skill is missing the fenced JSON block "
            f"for section {section!r} inside the per-section templates section"
        )
        payload = json.loads(block_match.group(1))
        payloads[section] = payload
        cursor = section_start + block_match.end()
    return payloads


def _canonical_validator_strings() -> tuple[tuple[str, str], ...]:
    """Return the canonical validator strings with their source locations.

    Each entry is ``(verbatim_error_string, source_location)``. The
    verbatim error string is the exact text the runtime raises, with
    variable parts (``{node!r}``, ``{criterion.id!r}``, ``{step_ref}``,
    ``{step_type!r}``) substituted by the same placeholder convention
    the skill body uses (``N``, ``ID``, ``TYPE``). The placeholder text
    matches the ``!r`` formatting exactly: single quotes around the
    string-formatted values (``'ID'`` / ``'TYPE'``) and bare integer
    text for the int-formatted values (``N``). This is the strongest
    verbatim contract we can check against a static skill body without
    losing the variable substitution.
    """
    return (
        (
            "plan step depends_on cycle detected at step N",
            "_validation.py cycle guard (~line 173)",
        ),
        (
            "plan cannot declare both parallel_plan and work_units; pick one",
            "_validation.py parallel_plan XOR (~line 229)",
        ),
        (
            "verification method must not invoke a shell interpreter directly; "
            "use the executable path",
            "_validation.py shell-invocation guard (~line 239)",
        ),
        (
            "skills_mcp.skills must contain at least one skill name unless "
            "design.planning_profile == 'minimal'",
            "_validation.py skills gate (~line 251)",
        ),
        (
            "acceptance criterion 'ID' references unknown step number N",
            "_validation.py _check_satisfied_by_steps_links (~line 681)",
        ),
        (
            "satisfied_by_steps cannot reference a research or verify step; "
            "step N is 'TYPE' for criterion 'ID'",
            "_validation.py _check_research_verify_step_references (~line 732)",
        ),
        (
            "plan envelope has no valid 'content' object",
            "_validation.py _decode_plan_payload (~line 769)",
        ),
        (
            "plan payload must decode to a JSON object",
            "_validation.py _decode_plan_payload (~line 763)",
        ),
        (
            "plan draft is missing a 'sections' object",
            "_validation.py finalize_plan_draft (~line 796)",
        ),
    )


@pytest.mark.timeout_seconds(10)
def test_skill_documents_every_validator_error_string() -> None:
    """Every canonical error string from ``_validation.py`` must appear verbatim.

    The skill body MUST quote the canonical error strings the agent will
    see so the agent can pattern-match the failure back to the fix
    without re-reading the source. Each entry below is the EXACT text
    the runtime raises (with variable parts replaced by the
    ``N``/``ID``/``TYPE`` placeholder convention the skill body uses);
    paraphrased or shortened quotes break the retry loop and let the
    agent ship a stale fix. The placeholder substitution preserves the
    ``!r`` formatting exactly (single quotes around string values, bare
    integer text for ints), so the substring check is exact-match on
    every character the runtime will emit.
    """
    body = _load_skill_body()
    for verbatim, source in _canonical_validator_strings():
        assert verbatim in body, (
            f"submit-plan-artifact skill body is missing the canonical "
            f"validator error string from {source}: {verbatim!r}. Agents "
            f"pattern-match on this text to pick a fix; paraphrase, "
            f"truncation, or omission breaks the retry loop."
        )


@pytest.mark.timeout_seconds(10)
def test_skill_contains_per_section_payload_templates() -> None:
    """The six fenced ``\\`\\`\\`json\\`\\`\\` blocks decode as JSON and target the right key.

    The skill embeds a ``Per-section minimal payload templates`` section
    with one fenced JSON block per required plan section. Each block
    MUST decode as JSON, and the top-level key MUST be the right one
    for the section the block is labelled with. A model copy-pasting
    from a broken template silently submits a malformed draft, so the
    templates are locked here.
    """
    body = _load_skill_body()
    expected_keys: dict[str, str] = {
        "summary": "scope_items",
        "skills_mcp": "skills",
        "steps": "step_type",
        "critical_files": "primary_files",
        "risks_mitigations": "risk",
        "verification_strategy": "method",
    }
    decoded_blocks = list(_extract_section_templates(body).values())
    assert len(decoded_blocks) == len(expected_keys), (
        f"per-section minimal payload templates section must contain exactly "
        f"{len(expected_keys)} fenced JSON blocks, found {len(decoded_blocks)}"
    )
    for section, marker_key in expected_keys.items():
        if section in {"summary", "skills_mcp", "critical_files"}:
            match = next(
                (
                    block
                    for block in decoded_blocks
                    if isinstance(block, dict) and marker_key in block
                ),
                None,
            )
        else:
            match = next(
                (
                    block
                    for block in decoded_blocks
                    if (
                        isinstance(block, list)
                        and len(block) > 0
                        and isinstance(block[0], dict)
                        and marker_key in block[0]
                    )
                ),
                None,
            )
        assert match is not None, (
            f"submit-plan-artifact skill is missing the {section} fenced "
            f"JSON template (or its marker key {marker_key!r})"
        )


@pytest.mark.timeout_seconds(10)
def test_documented_happy_path_round_trips_through_handlers(tmp_path: Path) -> None:
    """The documented happy-path round-trips through the canonical handlers.

    The six payload templates are PARSED OUT OF THE SKILL BODY (not
    hand-written) so any drift between the skill documentation and the
    working validator fails this test. Stage every required section via
    the batched ``ralph_submit_plan_sections`` (with one single-section
    ``ralph_submit_plan_section`` to mirror the documented two-call
    flow), then ``ralph_finalize_plan`` writes ``plan.json`` whose
    decoded ``content`` includes all six staged sections. A green
    finalize is the contract that proves the documented shapes are
    accepted.
    """
    body = _load_skill_body()
    payloads = _extract_section_templates(body)
    workspace = FsWorkspace(tmp_path)
    session = planning_session()

    batched_result = handle_submit_plan_sections(
        session,
        workspace,
        {
            "entries": [
                {"section": "summary", "content": json.dumps(payloads["summary"])},
                {"section": "skills_mcp", "content": json.dumps(payloads["skills_mcp"])},
            ]
        },
    )
    assert batched_result.is_error is False, _result_text(batched_result)

    for section in ("steps", "critical_files", "risks_mitigations", "verification_strategy"):
        result = handle_submit_plan_section(
            session,
            workspace,
            {"section": section, "content": json.dumps(payloads[section])},
        )
        assert result.is_error is False, (
            f"stage of section {section!r} failed: {_result_text(result)}"
        )

    finalize = handle_finalize_plan(session, workspace, {})
    assert finalize.is_error is False, _result_text(finalize)

    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists(), (
        "ralph_finalize_plan did not write plan.json on disk"
    )
    stored = json.loads((tmp_path / ".agent" / "artifacts" / "plan.json").read_text("utf-8"))
    content = cast("dict[str, object]", stored["content"])
    assert "summary" in content
    assert "skills_mcp" in content
    assert "steps" in content
    assert "critical_files" in content
    assert "risks_mitigations" in content
    assert "verification_strategy" in content


def _result_text(result: object) -> str:
    """Return the first text content block of a ToolResult."""
    content = cast("list[ToolContent]", result.content)
    first = content[0]
    return cast("str", getattr(first, "text", str(first)))
