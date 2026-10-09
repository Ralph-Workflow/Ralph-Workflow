"""Black-box contract tests for the sanity-only plan spec.

The receipt matrix covers acceptance; this module retains the shared document
fixture and a compact public extraction observation.
"""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC


def _complete_plan() -> str:
    return """---
type: plan
---
## Skills MCP
Skills: test-driven-development

## Steps
### [S-1] Update the plan validator
Change the markdown plan validator and prove the focused behavior.
Type: file_change
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
- create tests/mcp/test_md_plan_spec.py
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py
Expect: the focused plan-contract tests pass with exit code 0

### [S-2] Verify the complete focused contract
Run the focused plan suites after the validator change.
Type: verify
Depends on: S-1
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py tests/mcp/test_md_plan_validator_parity.py
Expect: the focused plan suites pass with exit code 0
"""


# Backward-compatible alias used by other test modules.
_plan_document = _complete_plan


def test_plan_spec_extracts_usable_steps_and_dependencies() -> None:
    content, diagnostics = parse_and_validate(_complete_plan(), PLAN_SPEC)

    assert diagnostics == []
    steps = content["steps"]
    assert [step["id"] for step in steps] == ["S-1", "S-2"]
    assert steps[1]["depends_on"] == [1]
