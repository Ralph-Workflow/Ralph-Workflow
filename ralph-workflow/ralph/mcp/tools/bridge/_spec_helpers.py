"""Helper functions and constants for building tool specs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools.bridge._types import JsonObject

_EXAMPLE_PLAN_SECTION_CONTENT = (
    '{"context":"Fix foo() out-of-range index handling after reading '
    'src/foo.py and tests/test_foo.py.","scope_items":['
    '{"text":"Add a regression test for negative and oversized indexes",'
    '"category":"test"},{"text":"Clamp the index in src/foo.py without '
    'changing the public foo() signature","category":"bugfix"},'
    '{"text":"Verify pytest tests/test_foo.py -q exits 0",'
    '"category":"test"}]}'
)
_EXAMPLE_STEPS_CONTENT = (
    '[{"number":1,"title":"Add the foo() regression test",'
    '"content":"Create tests/test_foo.py::test_clamp_handles_out_of_range_index '
    'covering negative and oversized indexes before editing production code.",'
    '"step_type":"file_change","targets":[{"path":"tests/test_foo.py",'
    '"action":"modify"}],"depends_on":[],"expected_evidence":['
    '{"kind":"test_name","ref":"tests/test_foo.py::test_clamp_handles_out_of_range_index"}]},'
    '{"number":2,"title":"Clamp the foo() index",'
    '"content":"Update src/foo.py so foo() clamps indexes at the collection '
    'bounds while preserving the public function signature and existing valid-index behavior.",'
    '"step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}],'
    '"depends_on":[1],"expected_evidence":[{"kind":"file","ref":"src/foo.py"},'
    '{"kind":"command_output","ref":"pytest tests/test_foo.py -q"}]}]'
)

_SUBMIT_ARTIFACT_DESCRIPTION = (
    "Submit a structured artifact. Required: artifact_type (string) and "
    "content (native JSON object/array or JSON-serialized string). Returns confirmation. "
    'Example: {"artifact_type": "commit_message", "content": {"type":'
    '"commit","subject":"fix(auth): prevent token expiry race"}}. '
    "See .agent/artifact-formats/<type>.md on error. Validated against the "
    "artifact's Pydantic model; artifact payloads enforce the 4 MB cap where "
    "the format defines one."
)


def _is_approved(outcome: object) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in {"approved", "allow", "allowed"}
    if isinstance(outcome, dict):
        mapping = cast("Mapping[str, object]", outcome)
        return any(
            isinstance(mapping.get(field), str)
            and cast("str", mapping[field]).strip().lower() in {"approved", "allow", "allowed"}
            for field in ("name", "value", "status")
        )
    for field in ("name", "value", "status"):
        value = cast("object", getattr(outcome, field, None))
        if isinstance(value, str) and value.strip().lower() in {"approved", "allow", "allowed"}:
            return True
    return False


def _metadata(
    *,
    name: str,
    description: str,
    input_schema: JsonObject,
    required_capability: str,
    is_multimodal: bool = False,
) -> ToolMetadata:
    return ToolMetadata(
        definition=ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        required_capability=required_capability,
        is_multimodal=is_multimodal,
    )
