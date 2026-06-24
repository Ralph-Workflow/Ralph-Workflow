"""Round-trip regression tests for the bundled ``submit-plan-step-edits`` skill.

The skill body documents the per-tool retry envelopes and a worked
"add one step at a time" example for the five step-mutation MCP tools.
These tests assert the three promises of that documentation:

1. Every step-mutation tool the skill claims to support is actually
   named in the skill body so an agent does not invent a tool name.
2. The eight retry envelopes the skill embeds match the canonical
   ``_format_plan_step_edit_error`` helper output verbatim (the same
   envelope the runtime inlines for an agent that has not consulted
   the skill). Drift between the skill and the helper silently forces
   the agent to retry with a stale shape.
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
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.tools.artifact import (
    _format_plan_step_edit_error,
    handle_discard_plan_draft,
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_plan_sections,
    handle_validate_plan_draft,
)
from ralph.mcp.tools.plan_draft_edit import (
    handle_insert_plan_step,
    handle_move_plan_step,
    handle_patch_step,
    handle_remove_plan_step,
    handle_replace_plan_step,
)
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import CoordinationSessionLike, ToolResult
    from ralph.mcp.tools.tool_content import ToolContent

PlanToolHandler = Callable[
    ["CoordinationSessionLike", FsWorkspace, dict[str, object]],
    "ToolResult",
]


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


def _loads_json_object(text: str) -> dict[str, object]:
    decoded = cast("object", json.loads(text))
    assert isinstance(decoded, dict), (
        f"expected JSON object but decoded {type(decoded).__name__}"
    )
    return cast("dict[str, object]", decoded)


def _planning_session() -> CoordinationSessionLike:
    return cast("CoordinationSessionLike", planning_session())


def _extract_retry_envelopes(body: str) -> dict[str, dict[str, object]]:
    """Parse the eight fenced `````json`` retry envelopes out of the skill body.

    The ``### Per-tool retry envelopes`` subsection embeds one fenced
    JSON block per step-mutation / step-utility tool, with the tool
    name declared via a ``**`<tool>`**`` bold marker immediately above
    the block. Returns a dict mapping each tool name to its parsed
    envelope dict. Used to verify the skill and helper stay in sync.
    """
    section_match = re.search(
        r"### Per-tool retry envelopes\s*\n([\s\S]*?)\n### ",
        body,
    )
    assert section_match is not None, (
        "submit-plan-step-edits skill is missing the '### Per-tool retry "
        "envelopes' subsection"
    )
    section_body = section_match.group(1)
    envelopes: dict[str, dict[str, object]] = {}
    pattern = re.compile(
        r"\*\*`(?P<tool>[a-z_]+)`\*\*[\s\S]*?```json\s*\n(?P<block>[\s\S]*?)\n```"
    )
    for match in pattern.finditer(section_body):
        tool = match.group("tool")
        block_text = match.group("block")
        payload = _loads_json_object(block_text)
        envelopes[tool] = payload
    return envelopes


def _extract_call_1_submit_plan_sections_payload(body: str) -> dict[str, object]:
    """Parse the documented Call 1 batch envelope out of the skill body."""
    return _extract_json_object_after_marker(
        body,
        "**Call 1 — `ralph_submit_plan_sections`**",
    )


def _extract_json_object_after_marker(body: str, marker: str) -> dict[str, object]:
    """Parse the first fenced JSON object after a stable markdown marker."""
    assert marker in body, f"submit-plan-step-edits skill is missing marker {marker!r}"
    section = body.split(marker, maxsplit=1)[1]
    match = re.search(r"```json\s*\n(?P<block>[\s\S]*?)\n```", section)
    assert match is not None, f"{marker!r} is missing a fenced JSON block"
    return _loads_json_object(match.group("block"))


def _extract_core_flow_retry_payloads(body: str) -> dict[str, dict[str, object]]:
    """Parse the Core Flow worked retry envelopes in their documented order."""
    end_marker = "### Add one step at a time"
    assert end_marker in body, "Core Flow section is missing the Add-one-step marker"
    core_flow_body = body.split(end_marker, maxsplit=1)[0]
    block_pattern = re.compile(r"```json\s*\n(?P<block>[\s\S]*?)\n```")
    blocks = [match.group("block") for match in block_pattern.finditer(core_flow_body)]
    tools = (
        "ralph_insert_plan_step",
        "ralph_replace_plan_step",
        "ralph_patch_step",
        "ralph_remove_plan_step",
        "ralph_move_plan_step",
    )
    assert len(blocks) == len(tools), (
        "Core Flow must document one executable JSON block for each "
        "step-mutation tool"
    )
    return {
        tool: _loads_json_object(block)
        for tool, block in zip(tools, blocks, strict=True)
    }


def _stage_documented_call_1(
    *,
    body: str,
    session: CoordinationSessionLike,
    workspace: FsWorkspace,
) -> None:
    """Stage the skill's documented 2-step starter draft in a temp workspace."""
    payload = _extract_call_1_submit_plan_sections_payload(body)
    batched = handle_submit_plan_sections(session, workspace, payload)
    assert batched.is_error is False, _tool_text(batched)


def _assert_payload_executes(
    *,
    body: str,
    tmp_path: Path,
    tool: str,
    handler: PlanToolHandler,
    payload: dict[str, object],
) -> None:
    workspace = FsWorkspace(tmp_path / tool)
    session = _planning_session()
    _stage_documented_call_1(body=body, session=session, workspace=workspace)

    result = handler(session, workspace, payload)
    assert result.is_error is False, (
        f"{tool} rejected its source-extracted payload: {_tool_text(result)}"
    )


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
def test_skill_documents_insert_step_number_is_ignored_not_rejected() -> None:
    body = _load_skill_body()
    forbidden = (
        "conflicts with an existing step raises",
        "conflicting step.number raises",
        "conflicting `step.number` raises",
    )
    for fragment in forbidden:
        assert fragment not in body
    assert "A conflicting user-supplied `step.number` does not survive" in body


@pytest.mark.timeout_seconds(10)
def test_skill_documents_validation_warnings_not_silent_drops() -> None:
    body = _load_skill_body()
    assert "validation_warnings" in body
    assert "silently dropped" not in body
    assert "preserved as staged JSON" in body


def _parse_helper_envelope_fragments(
    helper_text: str,
) -> dict[str, tuple[str, ...]]:
    """Parse the inline retry-envelope fragments emitted by the no-skill helper.

    ``_format_plan_step_edit_error`` inlines a concrete JSON fragment for
    each step-mutation tool of the form ``<tool> => {"<k1>": <v1>, "<k2>":
    <v2>}``. Returns a dict mapping the tool name to the tuple of top-level
    keys the fragment declares, in the order the helper emitted them. Used
    to compare the helper output against the skill-documented envelope keys.
    """
    fragment_pattern = re.compile(
        r"\b(?P<tool>ralph_(?:insert_plan_step|replace_plan_step|patch_step"
        r"|remove_plan_step|move_plan_step))"
        r"\s*=>\s*(?P<body>\{[^;]*\})"
    )
    fragments: dict[str, tuple[str, ...]] = {}
    for match in fragment_pattern.finditer(helper_text):
        tool = match.group("tool")
        body = match.group("body")
        decoded = _loads_json_object(body)
        keys = tuple(decoded)
        fragments[tool] = keys
    return fragments


@pytest.mark.timeout_seconds(10)
def test_skill_retry_envelopes_match_handler_output() -> None:
    """The 8 retry envelopes in the skill match the no-skill helper output verbatim.

    ``_format_plan_step_edit_error`` in ``ralph/mcp/tools/artifact.py``
    inlines a concrete retry envelope per tool. The
    skill embeds the same envelope as a fenced JSON block under
    ``### Per-tool retry envelopes``. Both MUST describe the same
    payload shape so the agent can copy the skill envelope verbatim
    and have the helper accept it. The test parses the helper's inline
    envelope fragments to learn the canonical key set per tool, then
    asserts that the skill's documented envelope keys match verbatim
    (same keys, same order). Drift between the helper and the skill
    fails the test on the first mismatched key.
    """
    body = _load_skill_body()
    envelopes = _extract_retry_envelopes(body)
    expected_envelope_keys: dict[str, set[str]] = {
        "ralph_insert_plan_step": {"index", "step"},
        "ralph_replace_plan_step": {"step_number", "step"},
        "ralph_patch_step": {"step_number", "step"},
        "ralph_remove_plan_step": {"step_number"},
        "ralph_move_plan_step": {"from_step_number", "to_index"},
        "ralph_get_plan_draft": set(),
        "ralph_validate_draft": set(),
        "ralph_discard_plan_draft": set(),
    }
    missing_tools = [
        tool for tool in expected_envelope_keys if tool not in envelopes
    ]
    assert not missing_tools, (
        f"submit-plan-step-edits skill is missing retry envelopes for: "
        f"{missing_tools}"
    )
    for tool, expected_keys in expected_envelope_keys.items():
        actual_keys = set(envelopes[tool].keys())
        assert actual_keys == expected_keys, (
            f"submit-plan-step-edits retry envelope for {tool!r} has keys "
            f"{sorted(actual_keys)}, expected {sorted(expected_keys)}"
        )

    helper_text = _format_plan_step_edit_error(
        detail="synthetic detail",
        workspace_root=Path("/tmp"),
        backend=DEFAULT_FILE_BACKEND,
        tool_name="ralph_insert_plan_step",
    )
    assert "submit-plan-step-edits" in helper_text
    for tool in STEP_MUTATION_TOOLS[:5]:
        assert tool in helper_text, (
            f"_format_plan_step_edit_error must mention {tool!r} so an agent "
            f"without the skill knows which tool the envelope belongs to"
        )

    helper_fragments = _parse_helper_envelope_fragments(helper_text)
    assert set(helper_fragments) == {
        "ralph_insert_plan_step",
        "ralph_replace_plan_step",
        "ralph_patch_step",
        "ralph_remove_plan_step",
        "ralph_move_plan_step",
    }, (
        f"_format_plan_step_edit_error output is missing one or more "
        f"step-mutation envelope fragments; found {sorted(helper_fragments)}"
    )

    for tool, helper_keys in helper_fragments.items():
        skill_keys = tuple(envelopes[tool].keys())
        assert helper_keys == skill_keys, (
            f"_format_plan_step_edit_error inline envelope for {tool!r} has "
            f"keys {helper_keys!r} (order preserved), but the "
            f"submit-plan-step-edits skill documents keys "
            f"{skill_keys!r} (order preserved). The skill and helper must "
            f"agree verbatim so an agent can copy either source and have "
            f"the runtime accept the payload."
        )

    assert "{...}" not in helper_text
    assert "Clamp the foo() index" in helper_text


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
    session = _planning_session()
    body = _load_skill_body()

    _stage_documented_call_1(body=body, session=session, workspace=workspace)

    get_result = handle_get_plan_draft(session, workspace, {})
    assert get_result.is_error is False, _tool_text(get_result)
    draft_payload = _loads_json_object(_tool_text(get_result))
    sections = cast("dict[str, object]", draft_payload["draft"])
    initial_steps = cast("list[dict[str, object]]", sections["steps"])
    assert len(initial_steps) == 2

    call_3_payload = _extract_json_object_after_marker(
        body,
        "**Call 3 — `ralph_insert_plan_step`**",
    )
    insert_result = handle_insert_plan_step(
        session,
        workspace,
        call_3_payload,
    )
    assert insert_result.is_error is False, _tool_text(insert_result)
    insert_echo = _loads_json_object(_tool_text(insert_result))
    assert insert_echo.get("total_steps") == 3

    validate_result = handle_validate_plan_draft(session, workspace, {})
    assert validate_result.is_error is False, _tool_text(validate_result)
    validate_payload = _loads_json_object(_tool_text(validate_result))
    assert validate_payload.get("valid") is True, (
        f"handle_validate_plan_draft reported not-valid: {validate_payload}"
    )

    finalize_result = handle_finalize_plan(session, workspace, {})
    assert finalize_result.is_error is False, _tool_text(finalize_result)

    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists(), (
        "ralph_finalize_plan did not write plan.json on disk after the worked "
        "add-one-step-at-a-time example"
    )
    stored = _loads_json_object(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text("utf-8")
    )
    content = cast("dict[str, object]", stored["content"])
    stored_steps = cast("list[dict[str, object]]", content["steps"])
    assert len(stored_steps) == 3
    titles = [cast("str", step["title"]) for step in stored_steps]
    assert titles == [
        "Add the foo() regression test",
        "Clamp the foo() index",
        "Document the foo() clamp behavior",
    ]


@pytest.mark.timeout_seconds(10)
def test_documented_call_1_batch_example_validates_with_real_handlers(tmp_path: Path) -> None:
    """The skill's documented batch example must pass the same handlers agents call."""
    workspace = FsWorkspace(tmp_path)
    session = _planning_session()
    payload = _extract_call_1_submit_plan_sections_payload(_load_skill_body())

    batched = handle_submit_plan_sections(session, workspace, payload)
    assert batched.is_error is False, _tool_text(batched)

    validate_result = handle_validate_plan_draft(session, workspace, {})
    assert validate_result.is_error is False, _tool_text(validate_result)
    validate_payload = _loads_json_object(_tool_text(validate_result))
    assert validate_payload.get("valid") is True, validate_payload


@pytest.mark.timeout_seconds(10)
def test_documented_retry_envelope_payloads_execute_with_real_handlers(
    tmp_path: Path,
) -> None:
    """Every executable retry envelope in the skill is accepted by its handler."""
    body = _load_skill_body()
    envelopes = _extract_retry_envelopes(body)
    handlers = {
        "ralph_insert_plan_step": handle_insert_plan_step,
        "ralph_replace_plan_step": handle_replace_plan_step,
        "ralph_patch_step": handle_patch_step,
        "ralph_remove_plan_step": handle_remove_plan_step,
        "ralph_move_plan_step": handle_move_plan_step,
        "ralph_get_plan_draft": handle_get_plan_draft,
        "ralph_validate_draft": handle_validate_plan_draft,
        "ralph_discard_plan_draft": handle_discard_plan_draft,
    }
    for tool, handler in handlers.items():
        _assert_payload_executes(
            body=body,
            tmp_path=tmp_path,
            tool=tool,
            handler=handler,
            payload=envelopes[tool],
        )


@pytest.mark.timeout_seconds(10)
def test_core_flow_worked_retry_envelopes_execute_with_real_handlers(
    tmp_path: Path,
) -> None:
    """The Core Flow's first worked envelopes must be handler-valid too."""
    body = _load_skill_body()
    payloads = _extract_core_flow_retry_payloads(body)
    handlers: dict[str, PlanToolHandler] = {
        "ralph_insert_plan_step": handle_insert_plan_step,
        "ralph_replace_plan_step": handle_replace_plan_step,
        "ralph_patch_step": handle_patch_step,
        "ralph_remove_plan_step": handle_remove_plan_step,
        "ralph_move_plan_step": handle_move_plan_step,
    }
    for tool, handler in handlers.items():
        _assert_payload_executes(
            body=body,
            tmp_path=tmp_path,
            tool=f"core-flow-{tool}",
            handler=handler,
            payload=payloads[tool],
        )


@pytest.mark.timeout_seconds(10)
def test_helper_error_insert_retry_envelope_executes_with_real_handler(
    tmp_path: Path,
) -> None:
    """The `_format_plan_step_edit_error` worked insert envelope is valid."""
    body = _load_skill_body()
    payload = _extract_json_object_after_marker(
        body,
        "**Worked retry envelope** for a `_format_plan_step_edit_error` style",
    )
    _assert_payload_executes(
        body=body,
        tmp_path=tmp_path,
        tool="helper-error-insert",
        handler=handle_insert_plan_step,
        payload=payload,
    )


@pytest.mark.timeout_seconds(10)
@pytest.mark.parametrize(
    ("marker", "handler"),
    [
        (
            "### Replace a vague implementation step",
            handle_replace_plan_step,
        ),
        (
            "### Patch missing proof onto an otherwise good step",
            handle_patch_step,
        ),
        (
            "### Insert a verification step after implementation",
            handle_insert_plan_step,
        ),
    ],
)
def test_analysis_feedback_correction_examples_execute_with_real_handlers(
    tmp_path: Path,
    marker: str,
    handler: PlanToolHandler,
) -> None:
    """Analysis-feedback examples must be copy-pasteable MCP payloads."""
    body = _load_skill_body()
    workspace = FsWorkspace(tmp_path / marker.replace(" ", "_").replace("#", ""))
    session = _planning_session()
    _stage_documented_call_1(body=body, session=session, workspace=workspace)

    payload = _extract_json_object_after_marker(body, marker)
    result = handler(session, workspace, payload)
    assert result.is_error is False, (
        f"{marker} rejected its source-extracted payload: {_tool_text(result)}"
    )

    validate_result = handle_validate_plan_draft(session, workspace, {})
    assert validate_result.is_error is False, _tool_text(validate_result)
    validate_payload = _loads_json_object(_tool_text(validate_result))
    assert validate_payload.get("valid") is True, validate_payload


def _tool_text(result: ToolResult) -> str:
    """Return the first text content block of a ToolResult."""
    content = cast("list[ToolContent]", result.content)
    return content[0].text
