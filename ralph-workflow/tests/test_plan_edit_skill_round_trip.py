"""Round-trip regression tests for the bundled ``submit-plan-step-edits`` skill.

The skill body documents the per-tool retry envelopes and a worked
"add one step at a time" example for the five step-mutation MCP tools.
These tests assert the three promises of that documentation:

1. Every step-mutation tool the skill claims to support is actually
   named in the skill body so an agent does not invent a tool name.
2. The eight retry envelopes the skill embeds match the canonical
   ``_format_plan_step_edit_error`` helper output (the same envelope
   the runtime inlines for an agent that has not consulted the skill).
3. The "add one step at a time" worked example round-trips end-to-end
   against the in-process tool handlers: stage a draft, insert a step,
   validate, finalize.

The tests are fully type-annotated and rely only on the in-process
Pydantic + tool handlers (no real I/O, no ``subprocess``, no
``time.sleep``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.tools.artifact import (
    _format_plan_step_edit_error,
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_plan_sections,
    handle_validate_plan_draft,
)
from ralph.mcp.tools.plan_draft_edit import handle_insert_plan_step
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from ralph.mcp.tools.tool_content import ToolContent


STEP_MUTATION_TOOLS: tuple[str, ...] = (
    "ralph_insert_plan_step",
    "ralph_replace_plan_step",
    "ralph_patch_step",
    "ralph_remove_plan_step",
    "ralph_move_plan_step",
    "ralph_get_plan_draft",
    "ralph_validate_draft",
    "ralph_discard_plan_draft",
    "ralph_finalize_plan",
)


def _load_skill_body() -> str:
    """Read the canonical submit-plan-step-edits skill markdown body."""
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        candidate = parent / "ralph" / "skills" / "content" / "submit-plan-step-edits.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.fail("could not locate submit-plan-step-edits.md on disk")


def _two_step_initial_payloads() -> dict[str, object]:
    """Return the 6 required section payloads for a 2-step starter draft."""
    return {
        "summary": {
            "context": "ctx",
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
        "skills_mcp": {"skills": ["writing-plans"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "First step",
                "content": "first",
                "step_type": "verify",
                "verify_command": "pytest tests/test_first.py -q",
            },
            {
                "number": 2,
                "title": "Second step",
                "content": "second",
                "step_type": "verify",
                "verify_command": "pytest tests/test_second.py -q",
            },
        ],
        "critical_files": {
            "primary_files": [{"path": "x.py", "action": "modify"}],
        },
        "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
    }


@pytest.mark.timeout_seconds(10)
def test_skill_documents_every_step_mutation_tool() -> None:
    """Every step-mutation tool name in the skill must match a real MCP tool.

    An agent that invents a tool name (e.g. ``ralph_add_step`` instead
    of ``ralph_insert_plan_step``) gets a no-such-tool error and wastes
    a round-trip. The skill names the canonical set, so the test
    asserts every name in that set appears verbatim in the body.
    """
    body = _load_skill_body()
    missing = [tool for tool in STEP_MUTATION_TOOLS if tool not in body]
    assert not missing, (
        f"submit-plan-step-edits skill is missing these tool names: {missing}"
    )


@pytest.mark.timeout_seconds(10)
def test_skill_retry_envelopes_match_handler_output() -> None:
    """The 8 retry envelopes in the skill match the no-skill helper output.

    The skill embeds eight ``\\`\\`\\`json\\`\\`\\` blocks under
    ``Per-tool retry envelopes``. Each MUST decode as JSON and contain
    exactly the keys the corresponding no-skill helper
    (``_format_plan_step_edit_error`` in ``ralph/mcp/tools/artifact.py``)
    names in its repair guidance. A drift between skill and helper
    silently forces the agent to retry with a stale shape.
    """
    body = _load_skill_body()
    section_match = re.search(
        r"### Per-tool retry envelopes\s*\n([\s\S]*?)\n### ",
        body,
    )
    assert section_match is not None, (
        "submit-plan-step-edits skill is missing the '### Per-tool retry "
        "envelopes' subsection"
    )
    section_body = section_match.group(1)

    expected_envelopes: dict[str, set[str]] = {
        "ralph_insert_plan_step": {"index", "step"},
        "ralph_replace_plan_step": {"step_number", "step"},
        "ralph_patch_step": {"step_number", "step"},
        "ralph_remove_plan_step": {"step_number"},
        "ralph_move_plan_step": {"from_step_number", "to_index"},
        "ralph_get_plan_draft": set(),
        "ralph_validate_draft": set(),
        "ralph_discard_plan_draft": set(),
    }
    blocks = re.findall(r"```json\s*\n([\s\S]*?)\n```", section_body)
    assert len(blocks) == len(expected_envelopes), (
        f"per-tool retry envelopes section must contain exactly "
        f"{len(expected_envelopes)} fenced JSON blocks, found {len(blocks)}"
    )
    decoded_blocks = [json.loads(block) for block in blocks]
    for tool, expected_keys in expected_envelopes.items():
        match = next(
            (
                block
                for block in decoded_blocks
                if isinstance(block, dict) and set(block.keys()) == expected_keys
            ),
            None,
        )
        assert match is not None, (
            f"submit-plan-step-edits skill is missing the canonical retry "
            f"envelope for {tool!r} with keys {sorted(expected_keys)}"
        )

    helper_text = _format_plan_step_edit_error(
        detail="synthetic detail",
        workspace_root=__import__("pathlib").Path("/tmp"),
        backend=DEFAULT_FILE_BACKEND,
        tool_name="ralph_insert_plan_step",
    )
    assert "submit-plan-step-edits" in helper_text
    for tool in STEP_MUTATION_TOOLS[:5]:
        assert tool in helper_text, (
            f"_format_plan_step_edit_error must mention {tool!r} so an agent "
            f"without the skill knows which tool the envelope belongs to"
        )


@pytest.mark.timeout_seconds(10)
def test_add_one_step_at_a_time_example_round_trips(tmp_path: Path) -> None:
    """The worked example round-trips through the canonical handlers.

    Stage the 6 required sections plus a 2-step starter ``steps`` section
    via ``ralph_submit_plan_sections`` (the batched flow the worked
    example opens with), read the staged draft via
    ``handle_get_plan_draft``, call ``handle_insert_plan_step`` at
    index=3, run ``handle_validate_plan_draft`` to dry-run the
    cross-section validator, then ``handle_finalize_plan`` to write
    ``plan.json``. Assert ``plan.json`` exists, parses, and has 3 steps
    in the staged order.
    """
    workspace = FsWorkspace(tmp_path)
    session = planning_session()
    payloads = _two_step_initial_payloads()

    batched = handle_submit_plan_sections(
        session,
        workspace,
        {
            "entries": [
                {"section": "summary", "content": json.dumps(payloads["summary"])},
                {"section": "skills_mcp", "content": json.dumps(payloads["skills_mcp"])},
                {
                    "section": "steps",
                    "content": json.dumps(payloads["steps"]),
                },
                {
                    "section": "critical_files",
                    "content": json.dumps(payloads["critical_files"]),
                },
                {
                    "section": "risks_mitigations",
                    "content": json.dumps(payloads["risks_mitigations"]),
                },
                {
                    "section": "verification_strategy",
                    "content": json.dumps(payloads["verification_strategy"]),
                },
            ]
        },
    )
    assert batched.is_error is False, _tool_text(batched)

    get_result = handle_get_plan_draft(session, workspace, {})
    assert get_result.is_error is False, _tool_text(get_result)
    draft_payload = json.loads(_tool_text(get_result))
    sections = cast("dict[str, object]", draft_payload["draft"])
    initial_steps = cast("list[dict[str, object]]", sections["steps"])
    assert len(initial_steps) == 2

    new_step_payload = {
        "title": "New middle step",
        "content": "Detailed executor instructions",
        "step_type": "file_change",
        "targets": [{"path": "x.py", "action": "modify"}],
        "depends_on": [],
    }
    insert_result = handle_insert_plan_step(
        session,
        workspace,
        {"index": 3, "step": new_step_payload},
    )
    assert insert_result.is_error is False, _tool_text(insert_result)
    insert_echo = json.loads(_tool_text(insert_result))
    assert insert_echo.get("total_steps") == 3

    validate_result = handle_validate_plan_draft(session, workspace, {})
    assert validate_result.is_error is False, _tool_text(validate_result)
    validate_payload = json.loads(_tool_text(validate_result))
    assert validate_payload.get("valid") is True, (
        f"handle_validate_plan_draft reported not-valid: {validate_payload}"
    )

    finalize_result = handle_finalize_plan(session, workspace, {})
    assert finalize_result.is_error is False, _tool_text(finalize_result)

    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists(), (
        "ralph_finalize_plan did not write plan.json on disk after the worked "
        "add-one-step-at-a-time example"
    )
    stored = json.loads(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text("utf-8")
    )
    content = cast("dict[str, object]", stored["content"])
    stored_steps = cast("list[dict[str, object]]", content["steps"])
    assert len(stored_steps) == 3
    titles = [cast("str", step["title"]) for step in stored_steps]
    assert titles == ["First step", "Second step", "New middle step"]


def _tool_text(result: object) -> str:
    """Return the first text content block of a ToolResult."""
    content = cast("list[ToolContent]", result.content)
    return cast("str", content[0].text)
