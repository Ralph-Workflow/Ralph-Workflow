"""Agent/user-facing Markdown handoff helpers.

Structured JSON artifacts remain Ralph's machine-readable source of truth for
validation and routing. This module mirrors selected artifact payloads into
Markdown files so downstream agents and users consume a stable, human-readable
handoff instead of raw JSON.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan import render_plan_markdown

HANDOFF_PATHS: dict[str, str] = {
    "plan": ".agent/PLAN.md",
    "issues": ".agent/ISSUES.md",
    "development_result": ".agent/DEVELOPMENT_RESULT.md",
    # parallel_development_summary reuses DEVELOPMENT_RESULT.md so the analysis
    # phase picks it up through the same fallback path without code changes.
    "parallel_development_summary": ".agent/DEVELOPMENT_RESULT.md",
    "fix_result": ".agent/FIX_RESULT.md",
    "development_analysis_decision": ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
    "planning_analysis_decision": ".agent/PLANNING_ANALYSIS_DECISION.md",
    "review_analysis_decision": ".agent/REVIEW_ANALYSIS_DECISION.md",
}


def handoff_path_for_artifact(artifact_type: str) -> str | None:
    """Return the Markdown handoff path for an artifact type, if any."""
    return HANDOFF_PATHS.get(artifact_type)


def sync_markdown_handoff(
    workspace_root: Path,
    artifact_type: str,
    content: Mapping[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """Write the Markdown handoff for a machine artifact and return its path."""
    relative_path = handoff_path_for_artifact(artifact_type)
    if relative_path is None:
        return None

    markdown = render_markdown_handoff(artifact_type, content)
    destination = workspace_root / relative_path
    backend.mkdir(destination.parent, parents=True, exist_ok=True)
    backend.write_text(destination, markdown, encoding="utf-8")
    return relative_path


def delete_markdown_handoff(
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove a mirrored Markdown handoff if the artifact write rolls back."""
    relative_path = handoff_path_for_artifact(artifact_type)
    if relative_path is None:
        return
    backend.unlink(workspace_root / relative_path, missing_ok=True)


def render_markdown_handoff(artifact_type: str, content: Mapping[str, object]) -> str:
    """Render an artifact payload into the Markdown handoff users/agents consume."""
    return _render_by_artifact_type(artifact_type, content)


_RESULT_ARTIFACT_SPECS: dict[str, tuple[str, list[str]]] = {
    "development_result": (
        "# Development Result",
        [
            "status",
            "files_changed",
            "next_steps",
        ],
    ),
    "fix_result": ("# Fix Result", ["files_changed", "next_steps"]),
}


def _render_by_artifact_type(artifact_type: str, content: Mapping[str, object]) -> str:
    if artifact_type == "plan":
        return render_plan_markdown(content)
    if artifact_type == "issues":
        return _render_issues_markdown(content)
    if artifact_type == "parallel_development_summary":
        return _render_parallel_summary_markdown(content)
    if artifact_type in _RESULT_ARTIFACT_SPECS:
        title, field_names = _RESULT_ARTIFACT_SPECS[artifact_type]
        sections: list[tuple[str, str | None]] = [
            (field.replace("_", " ").title(), _string_value(content.get(field)))
            for field in field_names
        ]
        # Add proof sections for development_result
        if artifact_type == "development_result":
            proof_sections = _build_development_result_proof_sections(content)
            sections.extend(proof_sections)
        return _render_key_value_markdown(
            title=title,
            summary=_string_value(content.get("summary")),
            sections=sections,
        )
    if artifact_type.endswith("_analysis_decision"):
        title = f"# {artifact_type.replace('_', ' ').title()}"
        return _render_analysis_decision_markdown(title, content)
    return _render_key_value_markdown(
        title=f"# {artifact_type.replace('_', ' ').title()}",
        summary=None,
        sections=[
            (
                "Content",
                json.dumps(cast("dict[str, object]", dict(content)), indent=2, sort_keys=True),
            )
        ],
    )


def ensure_markdown_handoff_from_artifact(
    workspace_root: Path,
    artifact_type: str,
    artifact_content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """Ensure a Markdown handoff exists from a persisted JSON artifact payload."""
    relative_path = handoff_path_for_artifact(artifact_type)
    if relative_path is None:
        return None

    parsed_obj = cast("object", json.loads(artifact_content))
    if not isinstance(parsed_obj, dict):
        return None
    parsed = cast("dict[str, object]", parsed_obj)
    content = parsed.get("content") if isinstance(parsed.get("content"), dict) else parsed
    if not isinstance(content, dict):
        return None

    sync_markdown_handoff(
        workspace_root,
        artifact_type,
        cast("Mapping[str, object]", content),
        backend=backend,
    )
    return str(workspace_root / relative_path)


def _render_parallel_summary_markdown(content: Mapping[str, object]) -> str:
    """Render the parallel development summary for analysis agent consumption."""
    lines = ["# Parallel Development Summary"]

    workers = content.get("workers")
    if isinstance(workers, list) and workers:
        lines.extend(["", "## Workers"])
        for w in workers:
            if not isinstance(w, dict):
                continue
            uid = w.get("unit_id", "?")
            status = w.get("status", "unknown")
            artifact_count = w.get("artifact_count", 0)
            final_message = w.get("final_message")
            entry = f"- **{uid}**: {status} ({artifact_count} artifact(s))"
            if final_message:
                entry += f" — {final_message}"
            lines.append(entry)

    any_failed = content.get("any_failed", False)
    all_succeeded = content.get("all_succeeded", False)
    lines.extend(
        [
            "",
            "## Status",
            "",
            f"- any_failed: {str(any_failed).lower()}",
            f"- all_succeeded: {str(all_succeeded).lower()}",
        ]
    )

    verification = content.get("verification")
    if isinstance(verification, dict):
        ran = verification.get("ran", False)
        passed = verification.get("passed")
        exit_code = verification.get("exit_code")
        lines.extend(["", "## Verification"])
        if ran:
            result = "passed" if passed else f"failed (exit code {exit_code})"
            lines.extend(["", f"Ran: yes — {result}"])
        else:
            lines.extend(["", "Ran: no"])

    return "\n".join(lines).rstrip() + "\n"


def _render_analysis_decision_markdown(title: str, content: Mapping[str, object]) -> str:
    return _render_key_value_markdown(
        title=title,
        summary=_string_value(content.get("summary")),
        sections=[
            ("Status", _string_value(content.get("status") or content.get("decision"))),
            (
                "What Came Up Short",
                _join_string_list(content.get("what_came_up_short")),
            ),
            ("How To Fix", _join_string_list(content.get("how_to_fix"))),
        ],
    )


def _render_issues_markdown(content: Mapping[str, object]) -> str:
    lines = ["# Review Issues"]
    summary = _string_value(content.get("summary"))
    if summary:
        lines.extend(["", "## Summary", "", summary])

    status = _string_value(content.get("status"))
    if status:
        lines.extend(["", f"Status: {status}"])

    issues = content.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues"])
        for issue in issues:
            if isinstance(issue, dict):
                path = _string_value(issue.get("path"))
                severity = _string_value(issue.get("severity"))
                issue_summary = _string_value(issue.get("summary") or issue.get("description"))
                label = issue_summary or "Issue"
                prefix = f"[{severity}] " if severity else ""
                suffix = f" (`{path}`)" if path else ""
                lines.extend(["", f"- {prefix}{label}{suffix}"])
            elif isinstance(issue, str) and issue.strip():
                lines.extend(["", f"- {issue.strip()}"])

    lines.extend(
        _render_string_list_section("## What Came Up Short", content.get("what_came_up_short"))
    )
    lines.extend(_render_string_list_section("## How To Fix", content.get("how_to_fix")))
    return "\n".join(lines).rstrip() + "\n"


def _render_key_value_markdown(
    *,
    title: str,
    summary: str | None,
    sections: list[tuple[str, str | None]],
) -> str:
    lines = [title]
    if summary:
        lines.extend(["", "## Summary", "", summary])
    for heading, value in sections:
        if not value:
            continue
        lines.extend(["", f"## {heading}", "", value])
    return "\n".join(lines).rstrip() + "\n"


def _render_proof_list(value: object) -> str | None:
    """Render a proof list (plan_items_proven or analysis_items_addressed) as Markdown.

    Each item is expected to be a dict with 'plan_item'/'how_to_fix_item' and 'proof' keys.
    """
    if not isinstance(value, list) or not value:
        return None
    lines: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        # Get the item identifier (plan_item or how_to_fix_item)
        item_key = item.get("plan_item") or item.get("how_to_fix_item")
        proof = item.get("proof")
        if item_key and proof:
            lines.append(f"- **{item_key}**: {proof}")
        elif isinstance(item_key, str) and item_key.strip():
            lines.append(f"- {item_key.strip()}")
    return "\n".join(lines) if lines else None


def _build_development_result_proof_sections(
    content: Mapping[str, object],
) -> list[tuple[str, str | None]]:
    """Build proof sections for development_result artifact."""
    sections: list[tuple[str, str | None]] = []
    plan_proofs = content.get("plan_items_proven")
    if isinstance(plan_proofs, list) and plan_proofs:
        rendered = _render_proof_list(plan_proofs)
        if rendered:
            sections.append(("Plan Items Proven", rendered))
    analysis_proofs = content.get("analysis_items_addressed")
    if isinstance(analysis_proofs, list) and analysis_proofs:
        rendered = _render_proof_list(analysis_proofs)
        if rendered:
            sections.append(("Analysis Items Addressed", rendered))
    return sections


def _join_string_list(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    items = [f"- {item.strip()}" for item in value if isinstance(item, str) and item.strip()]
    return "\n".join(items) if items else None


def _render_string_list_section(heading: str, value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return []
    lines = ["", heading]
    for item in value:
        if isinstance(item, str) and item.strip():
            lines.extend(["", f"- {item.strip()}"])
    return lines


def _string_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "HANDOFF_PATHS",
    "delete_markdown_handoff",
    "ensure_markdown_handoff_from_artifact",
    "handoff_path_for_artifact",
    "render_markdown_handoff",
    "sync_markdown_handoff",
]
