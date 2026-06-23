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

The tests are fully type-annotated and rely only on the in-process Pydantic
+ tool handlers (no real I/O, no ``subprocess``, no ``time.sleep``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.tools.artifact import (
    handle_finalize_plan,
    handle_submit_plan_section,
    handle_submit_plan_sections,
)
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session


def _load_skill_body() -> str:
    """Read the canonical submit-plan-artifact skill markdown body."""
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        candidate = parent / "ralph" / "skills" / "content" / "submit-plan-artifact.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.fail("could not locate submit-plan-artifact.md on disk")


def _minimal_section_payloads() -> dict[str, object]:
    """Return the six minimum-valid section payloads the skill documents."""
    return {
        "summary": {
            "context": "What is being changed and why",
            "scope_items": [
                {"text": "Concrete outcome 1"},
                {"text": "Concrete outcome 2"},
                {"text": "Concrete outcome 3"},
            ],
        },
        "skills_mcp": {"skills": ["writing-plans"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "Concrete step title",
                "content": "Detailed executor instructions",
                "step_type": "file_change",
                "targets": [{"path": "path/to/file.py", "action": "modify"}],
                "depends_on": [],
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "path/to/file.py", "action": "modify"}],
            "reference_files": [],
        },
        "risks_mitigations": [
            {
                "risk": "Specific failure mode",
                "mitigation": "How to avoid it",
                "severity": "medium",
            }
        ],
        "verification_strategy": [
            {
                "method": "pytest tests/test_x.py -q",
                "expected_outcome": "All tests pass",
            }
        ],
    }


@pytest.mark.timeout_seconds(10)
def test_skill_documents_every_validator_error_string() -> None:
    """Every canonical error string from ``_validation.py`` must appear verbatim.

    The skill body MUST quote the canonical error strings the agent will
    see so the agent can pattern-match the failure back to the fix without
    re-reading the source. Missing a quote is the single most common cause
    of a stuck retry loop, so every line in the canonical set is checked.
    """
    body = _load_skill_body()
    canonical_errors: list[str] = [
        "plan step depends_on cycle detected at step ",
        "plan cannot declare both parallel_plan and work_units; pick one",
        "verification method must not invoke a shell interpreter directly; "
        "use the executable path",
        "skills_mcp.skills must contain at least one skill name unless "
        "design.planning_profile == 'minimal'",
        "references unknown step number",
        "satisfied_by_steps cannot reference a research or verify step",
        "plan envelope has no valid 'content' object",
        "plan payload must decode to a JSON object",
        "plan draft is missing a 'sections' object",
    ]
    for needle in canonical_errors:
        assert needle in body, (
            f"submit-plan-artifact skill body is missing the canonical error "
            f"substring: {needle!r}. Agents pattern-match on this text to pick a fix."
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
    templates_match = re.search(
        r"## Per-section minimal payload templates\s*\n([\s\S]*?)\n## Dumb-proof checklist",
        body,
    )
    assert templates_match is not None, (
        "submit-plan-artifact skill is missing the '## Per-section minimal payload "
        "templates' section before '## Dumb-proof checklist'"
    )
    templates_body = templates_match.group(1)
    blocks = re.findall(r"```json\s*\n([\s\S]*?)\n```", templates_body)
    assert len(blocks) == len(expected_keys), (
        f"per-section minimal payload templates section must contain exactly "
        f"{len(expected_keys)} fenced JSON blocks, found {len(blocks)}"
    )
    decoded_blocks = [json.loads(block) for block in blocks]
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

    Stage every required section via the single-section
    ``ralph_submit_plan_section`` handler (with one batched
    ``ralph_submit_plan_sections`` to mirror the documented batched
    flow), then ``ralph_finalize_plan`` writes ``plan.json`` whose
    decoded ``content`` matches the staged payload. A green finalize
    is the contract that proves the documented shapes are accepted.
    """
    workspace = FsWorkspace(tmp_path)
    session = planning_session()
    payloads = _minimal_section_payloads()

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
    content = cast("list[object]", result.content)
    first = content[0]
    return cast("str", getattr(first, "text", str(first)))
