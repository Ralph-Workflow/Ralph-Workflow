"""Work-units policy check helpers used during markdown artifact validation.

These helpers are extracted from ``md_artifact`` so that ``md_artifact`` can
stay under the workspace file-size audit cap. They depend only on already
imported symbols in ``md_artifact`` and a small handful of Ralph policy /
workspace modules; importing them from here keeps their public behaviour
identical to the inlined version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.policy.loader as policy_loader
from ralph.mcp.artifacts.markdown import Diagnostic, parse_markdown_document
from ralph.mcp.tools.artifact import _workspace_root
from ralph.pipeline.work_units import (
    WorkUnitsValidationError,
    parse_work_units_from_artifact,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_work_units_against_policy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import WorkspaceLike
    from ralph.policy.models import PipelinePolicy


def work_units_policy_check(
    workspace: WorkspaceLike,
    artifact_type: str,
    parsed_content: dict[str, object],
    content: str,
) -> list[Diagnostic]:
    """Validate plan work_units against the workspace's effective pipeline policy.

    Returns an empty list when the artifact is not a plan, when no work_units
    are declared, when the structural validator already rejected them, or
    when the workspace's policy cannot be loaded. The latter matches the
    fail-open stance of ``_resolve_history_enabled`` so artifact
    verification does not depend on policy I/O.

    Single diagnostic rule (``WUPOL001``) covers every per-unit and cap
    violation: the validator's error message names the violated limit and
    its value, and the diagnostic anchors on the line of the
    ``## Work Units`` section heading.
    """
    diagnostics: list[Diagnostic] = []
    if artifact_type != "plan":
        return diagnostics
    raw = parsed_content.get("work_units")
    if not raw:
        return diagnostics

    try:
        parsed = parse_work_units_from_artifact(parsed_content)
    except WorkUnitsValidationError:
        # Structural checks already surfaced; do not double-report.
        return diagnostics
    if parsed is None:
        return diagnostics

    workspace_root = _workspace_root(workspace)
    pipeline = _load_policy_pipeline(workspace_root)
    if pipeline is None:
        return diagnostics

    section_line = _section_line(content, "Work Units") or 1

    try:
        validate_work_units_against_policy(parsed, pipeline, phase="development")
    except PolicyValidationError as exc:
        diagnostics.append(
            Diagnostic(
                section_line,
                "Work Units",
                "WUPOL001",
                str(exc),
            )
        )
    return diagnostics


def _section_line(content: str, section_name: str) -> int | None:
    """Return the 1-based line of the first ``## {section_name}`` heading, or None."""
    try:
        document, _ = parse_markdown_document(content, allow_nested_headings=False)
    except Exception:
        return None
    section = document.section(section_name)
    if section is None:
        return None
    return section.line


def _load_policy_pipeline(workspace_root: Path) -> PipelinePolicy | None:
    """Load the workspace's effective pipeline policy. Fail-open on I/O errors.

    Looks up ``load_policy`` through the public module attribute so callers
    (and tests) can monkeypatch the public ``ralph.policy.loader.load_policy``
    instead of importing this private helper.
    """
    try:
        bundle = policy_loader.load_policy(workspace_root / ".agent")
    except Exception:
        return None
    return bundle.pipeline
