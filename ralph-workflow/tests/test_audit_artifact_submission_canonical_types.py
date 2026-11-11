from __future__ import annotations

import re
from pathlib import Path

from ralph.mcp.tools import artifact as artifact_tools
from ralph.mcp.tools.artifact import KNOWN_ARTIFACT_TYPES
from tests._support.typed_accessors import must_str_list

TEMPLATES_DIR = Path("ralph/prompts/templates")

_KNOWN_TYPES = KNOWN_ARTIFACT_TYPES

SINGLE_SHOT_TEMPLATES: tuple[str, ...] = (
    "commit_review.jinja",
    "commit_message.jinja",
    "commit_simplified.jinja",
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "development_analysis.jinja",
    "planning_analysis.jinja",
)

_CALL_RE = re.compile(
    r"render_artifact_submission\(\s*'([^']+)'\s*,",
    flags=re.MULTILINE,
)


def test_every_call_site_uses_canonical_artifact_type() -> None:
    for template_name in SINGLE_SHOT_TEMPLATES:
        content = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        calls = must_str_list(_CALL_RE.findall(content))
        assert calls, (
            f"{template_name} must call render_artifact_submission with an "
            f"artifact_type so the macro can render the right path / "
            f"example / do-not"
        )
        for artifact_type in calls:
            assert artifact_type in _KNOWN_TYPES, (
                f"{template_name} passes artifact_type={artifact_type!r} "
                f"to render_artifact_submission but the MCP handler does "
                f"not recognize that value. Canonical types are "
                f"{sorted(_KNOWN_TYPES)!r}. A cheap agent that follows "
                f"the macro with a non-canonical type gets a validation "
                f"error and burns a retry."
            )


def test_canonical_set_is_non_empty() -> None:
    assert len(_KNOWN_TYPES) >= 3, (
        f"KNOWN_ARTIFACT_TYPES is unexpectedly small: {sorted(_KNOWN_TYPES)!r}"
    )
    for required in (
        "commit_message",
        "plan",
        "development_result",
        "policy_remediation_analysis_decision",
    ):
        assert required in _KNOWN_TYPES, f"KNOWN_ARTIFACT_TYPES must contain {required!r}"


def test_canonical_artifact_types_are_exported_as_a_public_api() -> None:
    assert "KNOWN_ARTIFACT_TYPES" in artifact_tools.__all__
    assert not hasattr(artifact_tools, "_KNOWN_ARTIFACT_TYPES")
