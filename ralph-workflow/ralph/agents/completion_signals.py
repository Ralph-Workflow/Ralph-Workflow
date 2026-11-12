from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.md_draft_io import unsubmitted_draft_divergence
from ralph.mcp.artifacts.state_db import CLEARED_SENTINEL_HMAC, MISSING, RunStateDB
from ralph.pipeline.plumbing.smoke_evidence import (
    Evidence,
    Provenance,
    absent,
    grade_artifact_submission_evidence,
    grade_completion_sentinel_evidence,
    grade_verdict,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.artifact import ArtifactHandlerDeps
    from ralph.phases.required_artifacts import RequiredArtifact

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"


def _bool_evidence(value: bool, *, holding_detail: str, absent_detail: str) -> Evidence:
    if value:
        return Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=holding_detail,
        )
    return absent(absent_detail)


@dataclass(frozen=True)
class CompletionSignals:
    explicit_complete: bool
    required_artifact_present: Evidence | bool
    artifact_types: tuple[str, ...]
    completion_sentinel_present: Evidence | bool = False
    artifact_required: bool = False
    unsubmitted_draft_present: bool = False
    required_artifact_evidence: Evidence = field(
        default_factory=lambda: absent("required_artifact_evidence not yet evaluated")
    )
    completion_sentinel_evidence: Evidence = field(
        default_factory=lambda: absent("completion_sentinel_evidence not yet evaluated")
    )

    def __post_init__(self) -> None:
        if not isinstance(self.required_artifact_present, Evidence):
            coerced = _bool_evidence(
                bool(self.required_artifact_present),
                holding_detail=(
                    "required artifact receipt present (graded at "
                    "WORKSPACE_EFFECT from legacy bool field; upgrade "
                    "to WIRE via smoke_evidence grading when wire "
                    "ledger is in scope)"
                ),
                absent_detail="required artifact receipt not present",
            )
            object.__setattr__(self, "required_artifact_present", coerced)
        if not isinstance(self.completion_sentinel_present, Evidence):
            coerced = _bool_evidence(
                bool(self.completion_sentinel_present),
                holding_detail=(
                    "completion sentinel present (graded at "
                    "WORKSPACE_EFFECT from legacy bool field; upgrade "
                    "to WIRE via smoke_evidence grading when wire "
                    "ledger is in scope)"
                ),
                absent_detail="completion sentinel not present",
            )
            object.__setattr__(self, "completion_sentinel_present", coerced)
        default_artifact_detail = "required_artifact_evidence not yet evaluated"
        default_sentinel_detail = "completion_sentinel_evidence not yet evaluated"
        if self.required_artifact_evidence.detail == default_artifact_detail:
            object.__setattr__(
                self,
                "required_artifact_evidence",
                self.required_artifact_present,
            )
        if self.completion_sentinel_evidence.detail == default_sentinel_detail:
            object.__setattr__(
                self,
                "completion_sentinel_evidence",
                self.completion_sentinel_present,
            )


def completion_signals_terminal(signals: CompletionSignals) -> bool:
    sentinel_evidence = signals.completion_sentinel_evidence
    artifact_evidence = signals.required_artifact_evidence
    if not sentinel_evidence.holds or signals.unsubmitted_draft_present:
        return False
    if signals.artifact_required:
        return artifact_evidence.holds
    return True


def extract_explicit_completion(raw_output: list[str]) -> bool:
    return any(_EXPLICIT_COMPLETION_MARKER in line for line in raw_output)


def _sentinel_hmac_matches(content: str, sentinel_secret: str, run_id: str) -> bool:
    try:
        parsed = cast("object", json.loads(content))
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    stored = cast("dict[str, object]", parsed).get("hmac")
    if not isinstance(stored, str):
        return False
    expected = hmac.new(
        sentinel_secret.encode(),
        run_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(stored, expected)


def _db_sentinel_lookup(workspace: Path, run_id: str) -> tuple[bool | None, str | None]:
    try:
        db = RunStateDB(workspace)
    except (OSError, RuntimeError, sqlite3.Error):
        return None, None
    try:
        try:
            stored = db.get_completion_sentinel_hmac(run_id)
        except (OSError, RuntimeError, sqlite3.Error):
            return None, None
    finally:
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()
    if stored is MISSING:
        return None, None
    if isinstance(stored, str) and stored == CLEARED_SENTINEL_HMAC:
        return False, None
    return True, stored if isinstance(stored, str) else None


def _check_legacy_file_sentinel(
    workspace: Path,
    run_id: str,
    *,
    _read_fn: Callable[[Path], str] | None,
    sentinel_secret: str | None,
) -> bool:
    from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT

    sentinel_path = workspace / COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    read_fn = _read_fn or (lambda path: path.read_text(encoding="utf-8"))
    try:
        content = read_fn(sentinel_path)
    except (FileNotFoundError, OSError):
        return False
    if sentinel_secret is None:
        return True
    return _sentinel_hmac_matches(content, sentinel_secret, run_id)


def _check_completion_sentinel(
    workspace: Path,
    run_id: str | None,
    *,
    _read_fn: Callable[[Path], str] | None = None,
    sentinel_secret: str | None = None,
) -> bool:
    if run_id is None:
        return False

    db_match: bool | None = None
    db_value: str | None = None
    if _read_fn is None:
        db_match, db_value = _db_sentinel_lookup(workspace, run_id)

    if db_match is True:
        if sentinel_secret is None:
            return True
        if db_value is None:
            return False
        expected = hmac.new(sentinel_secret.encode(), run_id.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(db_value, expected)

    if db_match is False:
        return False

    return _check_legacy_file_sentinel(
        workspace,
        run_id,
        _read_fn=_read_fn,
        sentinel_secret=sentinel_secret,
    )


def is_artifact_submitted(
    workspace: Path,
    run_id: str,
    artifact_type: str,
    *,
    deps: ArtifactHandlerDeps | None = None,
    receipt_secret: str | None = None,
    artifact_path: str | None = None,
) -> bool:
    if artifact_receipt_present(workspace, run_id, artifact_type, receipt_secret=receipt_secret):
        return True

    fallback_path, artifact_dir, handoff_dir = _worker_submission_paths(
        workspace,
        artifact_type,
        artifact_path,
    )
    result = promote_fallback_artifact(
        workspace,
        artifact_type,
        deps=deps,
        run_id=run_id,
        receipt_secret=receipt_secret,
        fallback_path=fallback_path,
        artifact_dir=artifact_dir,
        handoff_dir=handoff_dir,
    )
    return result is not None and result.receipt_path is not None


def _worker_submission_paths(
    workspace: Path,
    artifact_type: str,
    artifact_path: str | None,
) -> tuple[Path | None, Path | None, Path | None]:
    if artifact_path is None:
        return None, None, None
    candidate = Path(artifact_path)
    absolute = candidate if candidate.is_absolute() else workspace / candidate
    resolved = absolute.resolve(strict=False)
    workers_root = (workspace / ".agent" / "workers").resolve(strict=False)
    try:
        resolved.relative_to(workers_root)
    except ValueError:
        return None, None, None
    if resolved.name != f"{artifact_type}.md" or resolved.parent.name != "artifacts":
        return None, None, None
    worker_namespace = resolved.parent.parent
    return (
        worker_namespace / "tmp" / f"{artifact_type}.md",
        resolved.parent,
        worker_namespace / "handoffs",
    )


def evaluate_completion(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: RequiredArtifact | None = None,
    run_id: str | None = None,
    sentinel_secret: str | None = None,
    receipt_secret: str | None = None,
) -> CompletionSignals:
    explicit = extract_explicit_completion(raw_output or [])
    ra = required_artifact
    if run_id is not None:
        sentinel_present = _check_completion_sentinel(
            workspace, run_id, sentinel_secret=sentinel_secret
        )
        sentinel_evidence = grade_completion_sentinel_evidence(
            workspace,
            run_id,
            present=sentinel_present,
            host_synthesized=False,
            secret=sentinel_secret,
        )
    else:
        sentinel_evidence = absent("completion sentinel receipt not present (no run_id)")
    if ra is None:
        return CompletionSignals(
            explicit_complete=explicit,
            required_artifact_present=absent("phase has no registered required artifact"),
            artifact_types=(),
            completion_sentinel_present=sentinel_evidence,
        )
    artifact_path = workspace / ra.artifact_path
    if run_id is not None:
        present = is_artifact_submitted(
            workspace,
            run_id,
            ra.artifact_type,
            receipt_secret=receipt_secret,
            artifact_path=ra.artifact_path,
        )
        artifact_evidence = grade_artifact_submission_evidence(
            workspace,
            run_id,
            submitted=present,
            secret=receipt_secret,
        )
    else:
        present = False
        artifact_evidence = absent("artifact submission receipt not present (no run_id)")
    divergence = unsubmitted_draft_divergence(
        artifact_path.parent,
        ra.artifact_type,
        artifact_path,
    )
    return CompletionSignals(
        explicit_complete=explicit,
        required_artifact_present=artifact_evidence,
        artifact_types=(ra.artifact_type,) if present else (),
        completion_sentinel_present=sentinel_evidence,
        artifact_required=ra.artifact_required,
        unsubmitted_draft_present=divergence is not None,
    )


def graded_completion_signals(signals: CompletionSignals) -> dict[str, Evidence]:
    return {
        "required_artifact_present": signals.required_artifact_evidence,
        "completion_sentinel_present": signals.completion_sentinel_evidence,
    }


def graded_verdict(signals: CompletionSignals) -> tuple[str, Provenance]:
    return grade_verdict(graded_completion_signals(signals))


def format_phase_verdict(signals: CompletionSignals) -> str:
    label, weakest = graded_verdict(signals)
    if label == "PASS":
        return "PASS"
    return f"DEGRADED ({weakest.name.lower().replace('_', '-')})"


def graded_phase_verdict(
    signals: CompletionSignals,
    *,
    required_artifact: RequiredArtifact | None = None,
) -> tuple[str, Provenance, str]:
    if signals.artifact_required and not signals.required_artifact_evidence.holds:
        if required_artifact is not None:
            detail = (
                f"no receipt for '{required_artifact.artifact_type}'; "
                f"{required_artifact.artifact_path} absent"
            )
        else:
            detail = signals.required_artifact_evidence.detail
        return "FAILED", Provenance.ABSENT, detail
    label, weakest = graded_verdict(signals)
    return label, weakest, ""


_COORDINATION_EXPORTS = frozenset({"COMPLETION_SENTINEL_RELPATHFMT"})


def __getattr__(name: str) -> object:
    if name in _COORDINATION_EXPORTS:
        from ralph.mcp.tools import coordination

        return cast("object", getattr(coordination, name))
    if name == "_COMPLETION_SENTINEL_RELPATHFMT":
        from ralph.mcp.tools import coordination

        return cast("object", coordination.COMPLETION_SENTINEL_RELPATHFMT)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CompletionSignals",
    "completion_signals_terminal",
    "evaluate_completion",
    "extract_explicit_completion",
    "format_phase_verdict",
    "graded_completion_signals",
    "graded_phase_verdict",
    "graded_verdict",
    "is_artifact_submitted",
]
