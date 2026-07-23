"""Pure behavior tests for the plan markdown artifact specification."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import edit_plan_step_markdown


def _plan_document() -> str:
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
- [SUM-1] {"context":"Implement markdown validation.","scope_items":[{"text":"Add parser","category":"feature"},{"text":"Add tests","category":"test"},{"text":"Run tests","category":"test"}]}
## Skills MCP
- [SK-1] {"skills":["test-driven-development"],"mcps":[]}
## Steps
- [S-1] {"title":"Implement spec","content":"Add the markdown spec.","step_type":"file_change","targets":[{"path":"ralph/spec.py","action":"modify"}],"depends_on":[]}
- [S-2] {"title":"Verify spec","content":"Run focused tests.","step_type":"verify","verify_command":"pytest tests/mcp/test_md_plan_spec.py -q","depends_on":["S-1"]}
## Critical Files
- [CF-1] {"primary_files":[{"path":"ralph/spec.py","action":"modify"}]}
## Risks Mitigations
- [R-1] {"risk":"Validation drift","mitigation":"Use the canonical normalizer.","severity":"medium"}
## Verification
- [V-1] {"method":"pytest tests/mcp/test_md_plan_spec.py -q","expected_outcome":"Focused tests pass."}
"""


def test_plan_spec_maps_step_ids_to_canonical_number_references_and_registers() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), PLAN_SPEC)

    assert diagnostics == []
    assert content["schema_version"] == 1
    steps = content["steps"]
    assert isinstance(steps, list)
    assert isinstance(steps[1], dict)
    assert steps[1]["depends_on"] == [1]
    assert get_spec("plan") is PLAN_SPEC


def test_plan_spec_warns_and_coerces_non_security_vocabulary() -> None:
    document = _plan_document().replace("intent_verb: add", "intent_verb: invented").replace(
        '"category":"feature"', '"category":"invented"'
    ).replace('"step_type":"file_change"', '"step_type":"invented"')

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    summary = content["summary"]
    steps = content["steps"]
    assert isinstance(summary, dict)
    assert isinstance(steps, list)
    assert isinstance(summary["scope_items"], list)
    assert isinstance(summary["scope_items"][0], dict)
    assert isinstance(steps[0], dict)
    assert summary["intent_verb"] == "add"
    assert summary["scope_items"][0]["category"] == "other"
    assert steps[0].get("step_type", "action") == "action"
    assert {diagnostic.rule_id for diagnostic in diagnostics} >= {"SPEC009", "PLAN002", "PLAN003"}


def test_plan_step_move_reindexes_stable_ids_and_rewrites_dependencies() -> None:
    edited = edit_plan_step_markdown(_plan_document(), "move", "S-2", index=1)
    content, diagnostics = parse_and_validate(edited, PLAN_SPEC)

    assert diagnostics == []
    steps = content["steps"]
    assert isinstance(steps, list)
    assert isinstance(steps[0], dict)
    assert steps[0]["depends_on"] == [2]
