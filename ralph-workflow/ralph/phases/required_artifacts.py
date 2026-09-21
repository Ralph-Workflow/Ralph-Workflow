"""Centralized required-artifact metadata for all pipeline phases.

Artifact metadata is split across two policy surfaces. ``artifacts.toml`` owns
artifact type, canonical artifact path, markdown handoff path, and normalizer lookup.
``pipeline.toml`` owns whether a phase's output artifact is required for
success. There are no built-in override tables — artifact paths must be
declared in ``artifacts.toml`` and requiredness must be declared on the phase
definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import normalize_development_result_content
from ralph.mcp.artifacts.typed_artifacts import (
    normalize_fix_result_content,
    normalize_issues_content,
)
from ralph.policy.models import PipelinePolicy
from ralph.recovery.retry_prompt import (
    VALIDATION_FAILURE_BANNER,
    build_retry_error_block,
    build_validation_retry_footer,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts.markdown import Diagnostic
    from ralph.policy.models import ArtifactsPolicy
    from ralph.workspace.protocol import Workspace

# Normalizers keyed by artifact_type — used by build_required_artifacts()
_ARTIFACT_TYPE_NORMALIZERS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "development_result": normalize_development_result_content,
    "fix_result": normalize_fix_result_content,
    "issues": normalize_issues_content,
}


@dataclass(frozen=True)
class RequiredArtifact:
    """Metadata about an artifact contract for a pipeline phase.

    When artifact_required is False, an absent artifact does not fail the phase;
    a present artifact is still validated.
    """

    phase: str
    artifact_type: str
    artifact_path: str
    markdown_path: str | None
    normalizer: Callable[[dict[str, object]], dict[str, object]] | None
    artifact_required: bool = True


def build_required_artifacts(
    artifacts_policy: ArtifactsPolicy,
) -> dict[str, RequiredArtifact]:
    """Build a drain-keyed artifact registry from ArtifactsPolicy.

    The registry contains artifact identity and path metadata only. Callers that
    need phase-specific requiredness must use resolve_phase_required_artifact().
    """
    result: dict[str, RequiredArtifact] = {}
    for contract in artifacts_policy.artifacts.values():
        drain = str(contract.drain)
        artifact_type = contract.artifact_type
        # Migrated artifacts are markdown source documents at one canonical path.
        artifact_path = f".agent/artifacts/{artifact_type}.md"
        markdown_path = contract.markdown_summary_path
        normalizer = _ARTIFACT_TYPE_NORMALIZERS.get(artifact_type)

        result[drain] = RequiredArtifact(
            phase=drain,
            artifact_type=artifact_type,
            artifact_path=artifact_path,
            markdown_path=markdown_path,
            normalizer=normalizer,
            artifact_required=True,
        )
    return result


def resolve_required_artifact(
    artifacts_policy: ArtifactsPolicy,
    *,
    drain: str,
) -> RequiredArtifact | None:
    """Resolve artifact identity/path metadata for a drain from artifacts.toml."""
    try:
        registry = build_required_artifacts(artifacts_policy)
        return registry.get(drain)
    except AttributeError:
        return None


def resolve_phase_required_artifact(
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    *,
    phase: str,
    drain: str | None = None,
) -> RequiredArtifact | None:
    """Resolve the artifact contract for a phase, including phase-owned requiredness."""
    phase_def = pipeline_policy.phases.get(phase)
    effective_drain = drain or (phase_def.drain if phase_def is not None else phase)
    ra = resolve_required_artifact(artifacts_policy, drain=effective_drain)
    if ra is None:
        return None
    required = phase_def.artifact_required if phase_def is not None else True
    return RequiredArtifact(
        phase=phase,
        artifact_type=ra.artifact_type,
        artifact_path=ra.artifact_path,
        markdown_path=ra.markdown_path,
        normalizer=ra.normalizer,
        artifact_required=required,
    )


def retry_hint_path(phase: str, *, pipeline_policy: object | None = None) -> str:
    """Return the workspace-relative retry-hint path keyed by the effective drain."""
    phase_def = pipeline_policy.phases.get(phase) if isinstance(pipeline_policy, PipelinePolicy) else None
    drain = phase_def.drain if phase_def is not None else phase
    return f".agent/tmp/last_retry_error_{drain}.txt"


def read_validation_retry_hint(
    workspace_root: Path,
    drain: str,
    *,
    worker_namespace: Path | None = None,
) -> str:
    """Read validation retry context without consuming phase-reentry state."""
    hint_path = (
        worker_namespace / "tmp" / f"last_retry_error_{drain}.txt"
        if worker_namespace is not None
        else workspace_root / retry_hint_path(drain)
    )
    try:
        return hint_path.read_text(encoding="utf-8") if hint_path.is_file() else ""
    except OSError:
        return ""


def clear_validation_retry_hint(
    workspace: Workspace,
    phase: str,
    *,
    pipeline_policy: PipelinePolicy | None = None,
    hint_path_override: str | None = None,
) -> None:
    """Clear a phase's retained validation context after its gate accepts the artifact."""
    hint_path = hint_path_override or retry_hint_path(phase, pipeline_policy=pipeline_policy)
    legacy_path = retry_hint_path(phase)
    try:
        if workspace.exists(hint_path):
            workspace.remove(hint_path)
        if legacy_path != hint_path and workspace.exists(legacy_path):
            workspace.remove(legacy_path)
    except Exception:
        return


_VALIDATION_RETRY_BODY_CAP = 4_096
_VALIDATION_RETRY_HISTORY_CAP = 16_384
_MINIMUM_VALIDATION_RETRY_ATTEMPTS = 2
_DEVELOPMENT_RESULT_MISSING_WORK_RULE_IDS = frozenset(
    {"DEV011", "DEV012", "DEV013", "DEV015"}
)


def validation_corrective_action(artifact_type: str, diagnostics: list[Diagnostic]) -> str:
    """Return the corrective action the retry prompt must demand."""
    if artifact_type == "commit_message":
        return (
            "Rewrite the commit message itself (subject and body) so it satisfies "
            "the diagnostics above, then resubmit it with ralph_edit_md_artifact. "
            "Do not change code to make the message fit, and do not resubmit the "
            "same message unchanged."
        )
    if artifact_type == "development_result" and any(
        d.severity == "error" and d.rule_id in _DEVELOPMENT_RESULT_MISSING_WORK_RULE_IDS
        for d in diagnostics
    ):
        return (
            "The result claims work or evidence the validator cannot find. Complete "
            "the underlying work first: implement the missing plan items, run the "
            "verification commands, and capture the proof the diagnostics name. Only "
            "then update the staged draft with ralph_edit_md_artifact and resubmit. "
            "Do not edit the result to claim proof you have not produced."
        )
    return (
        "The submitted document remains staged as the retained draft. Repair it in "
        "place with ralph_edit_md_artifact, which resubmits automatically once valid. "
        "Fix the underlying document issue before resubmitting."
    )


def build_validation_retry_hint(
    artifact_type: str,
    diagnostics: list[Diagnostic],
    *,
    prior_hint: str = "",
) -> str:
    """Build actionable retry context from canonical validator diagnostics."""
    lines = [
        "PREVIOUS ATTEMPT FAILED: artifact validation rejected the retained draft.",
        f"Artifact type: {artifact_type}",
        "Validator diagnostics:",
    ]
    for diagnostic in diagnostics:
        if diagnostic.severity != "error":
            continue
        section = diagnostic.section or "frontmatter/document"
        lines.append(
            f"- {diagnostic.rule_id} at line {diagnostic.line}, section {section}: "
            f"{diagnostic.message}"
        )
    lines.extend(["", validation_corrective_action(artifact_type, diagnostics),
                  "Do not blindly resubmit identical content. "
                  "Do not restart the task from scratch or discard prior work."])
    current_attempt = "\n".join(lines)
    attempts = [*_validation_retry_attempts(prior_hint), current_attempt]
    numbered_attempts = [
        _number_validation_retry_attempt(attempt, index)
        for index, attempt in enumerate(attempts, start=1)
    ]
    # bounded-accumulator-ok: old validation bodies share a 16384-character cap while every headline remains.
    bounded_attempts = _bound_validation_retry_attempts(numbered_attempts)
    header = (
        f"THIS VALIDATION HAS FAILED {len(attempts)} TIMES - FIX THE UNDERLYING ISSUE, "
        "DO NOT RESUBMIT UNCHANGED\n\n"
        if len(attempts) > 1
        else ""
    )
    return "\n\n".join(
        [VALIDATION_FAILURE_BANNER, header + "\n\n".join(bounded_attempts), build_validation_retry_footer()]
    )


def _validation_retry_attempts(hint: str) -> list[str]:
    """Extract complete validation attempts from a prior retry hint."""
    marker = "PREVIOUS ATTEMPT FAILED: artifact validation rejected the retained draft."
    attempt_pattern: Pattern[str] = re.compile(
        rf"ATTEMPT \d+\n({re.escape(marker)}.*?)(?=\n\nATTEMPT \d+\n|\Z)",
        flags=re.DOTALL,
    )
    numbered_attempts: list[str] = [match.group(1) for match in attempt_pattern.finditer(hint)]
    if numbered_attempts:
        return [attempt.strip() for attempt in numbered_attempts]
    return [f"{marker}{attempt.strip()}" for attempt in hint.split(marker)[1:] if attempt.strip()]


def _number_validation_retry_attempt(attempt: str, index: int) -> str:
    """Attach the current ordinal to one complete validation retry attempt."""
    marker = "PREVIOUS ATTEMPT FAILED: artifact validation rejected the retained draft."
    return f"ATTEMPT {index}\n{marker}{attempt.removeprefix(marker)}"


def _bound_validation_retry_attempts(attempts: list[str]) -> list[str]:
    """Bound old attempt bodies while retaining every current headline."""
    if len(attempts) < _MINIMUM_VALIDATION_RETRY_ATTEMPTS:
        return attempts
    newest = attempts[-1]
    old_attempts = attempts[:-1]
    headlines = [_validation_retry_headline(attempt) for attempt in old_attempts]
    remaining_body_budget = max(
        0,
        _VALIDATION_RETRY_HISTORY_CAP - len(newest) - sum(len(headline) for headline in headlines),
    )
    body_budget = min(_VALIDATION_RETRY_BODY_CAP, remaining_body_budget // len(old_attempts))
    return [
        _bound_validation_retry_attempt(headline, attempt, body_budget)
        for headline, attempt in zip(headlines, old_attempts, strict=True)
    ] + [newest]


def _validation_retry_headline(attempt: str) -> str:
    """Keep ordinal and first diagnostic visible when trimming old bodies."""
    lines = attempt.splitlines()
    return "\n".join(lines[:5])


def _bound_validation_retry_attempt(headline: str, attempt: str, body_budget: int) -> str:
    """Trim an old attempt only after its retained headline."""
    body = attempt.removeprefix(headline).lstrip("\n")
    if len(body) <= body_budget:
        return attempt
    return f"{headline}\n{body[:body_budget].rstrip()}\n[older attempt body truncated]"


def build_retry_hint(
    phase: str,
    detail: str,
    *,
    registry: dict[str, RequiredArtifact] | None = None,
    prior_output: list[str] | None = None,
    submit_tool_name: str | None = None,
    example_payload: str | None = None,
    unsubmitted_draft: bool = False,
    validation: bool = False,
) -> str:
    """Build the retry hint for an agent that failed to submit a required artifact.

    This is the SINGLE source of artifact-missing retry guidance — every caller
    (pipeline phase gates AND the commit command) routes through it, so the
    recovery cannot drift. When ``prior_output``/``submit_tool_name`` are given,
    the hint additionally echoes the agent's own prior analysis back and tells it
    to submit via the named tool, so a model that already drafted the artifact
    submits it instead of restarting.

    Args:
        phase: Pipeline phase / drain name.
        detail: Error detail message.
        registry: Optional artifact registry; when provided the hint names the
            specific artifact type and canonical path.
        prior_output: The agent's prior output lines, echoed back as context.
        submit_tool_name: The submit-artifact tool the agent must call.
        example_payload: An example submit-tool arguments payload, if available.
    """
    ra = registry.get(phase) if registry is not None else None
    if ra is None:
        block = build_retry_error_block(
            failure_summary=(
                "the required artifact was not submitted before completion was declared"
            ),
            detail=detail,
            validation=validation,
        )
        artifact_type, artifact_path = "the required artifact", None
    else:
        block = build_retry_error_block(
            failure_summary=(
                f"required artifact '{ra.artifact_type}' at '{ra.artifact_path}' "
                "was not submitted or was invalid"
            ),
            detail=detail,
            validation=validation,
        )
        artifact_type, artifact_path = ra.artifact_type, ra.artifact_path

    lines = [block, "", "Submit the artifact now. Do not restart the task from scratch."]
    if unsubmitted_draft:
        lines.append(
            "Your staged draft contains content that was never submitted. Resubmit it with "
            "ralph_edit_md_artifact (it submits when valid), or deliberately abandon it with "
            "ralph_discard_md_draft before declaring completion."
        )
    tool = submit_tool_name or "the submit-artifact MCP tool"
    if artifact_path is not None and artifact_path.endswith(".md"):
        legacy_path = f"{artifact_path[:-3]}.json"
        lines.append(
            f"Legacy JSON at '{legacy_path}' is not accepted as '{artifact_type}'. "
            f"Resubmit the artifact as Markdown at '{artifact_path}' with "
            "ralph_submit_md_artifact. "
            "Do not author or resubmit JSON."
        )
    lines.append(
        f'Call {tool} with artifact_type="{artifact_type}" and put the complete markdown document in '
        "the content field."
    )
    if example_payload:
        lines.append(f"Example MCP arguments: {example_payload}")
    if artifact_path is not None:
        fallback_path = f".agent/tmp/{artifact_type}.md"
        lines.append(
            "If the submit tool is unavailable, write the complete markdown document to "
            f"{fallback_path}."
        )
    if prior_output:
        echoed = "\n".join(prior_output[-12:])
        lines.append("Your prior analysis (submit it, do not redo it):")
        lines.append(echoed)
    return "\n".join(lines)


def build_missing_input_hint(phase: str, upstream_phase: str, artifact_path: str) -> str:
    """Build a retry hint for a phase that is missing a required upstream input artifact.

    Unlike build_retry_hint (which describes a missing *output*), this function
    describes a missing *input* — i.e., a handoff that a prior phase should have
    produced. The hint is written to the phase's retry-hint file so the agent
    sees an explanation on the next attempt, but the message correctly names the
    upstream producer rather than blaming the current agent.
    """
    return (
        f"PIPELINE INPUT MISSING: The '{upstream_phase}' phase did not produce the "
        f"required artifact at '{artifact_path}'. This artifact is a required input "
        f"for the '{phase}' phase and must be present before '{phase}' can proceed. "
        f"The upstream handoff from '{upstream_phase}' must be completed first."
    )


def build_proof_failure_hint(phase: str, detail: str, *, validation: bool = False) -> str:
    """Build a retry hint for a phase that submitted proof but failed validation."""
    return build_retry_error_block(
        failure_summary="proof entries are incomplete or invalid",
        detail=detail,
        validation=validation,
    )


__all__ = [
    "RequiredArtifact",
    "build_missing_input_hint",
    "build_proof_failure_hint",
    "build_required_artifacts",
    "build_retry_hint",
    "resolve_phase_required_artifact",
    "resolve_required_artifact",
    "retry_hint_path",
]
