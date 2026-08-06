"""Durable completion-signal evaluation shared by every agent transport.

Required-artifact phases complete only when two independent, run-scoped facts
exist: a canonical artifact receipt and the sentinel written by
``declare_complete``. Optional-artifact and artifact-free phases still require
the sentinel, but do not require a receipt. Plain transcript markers are useful
for diagnostics only and never replace durable evidence.
"""

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
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance, absent, grade_verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.artifact import ArtifactHandlerDeps
    from ralph.phases.required_artifacts import RequiredArtifact

from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"
_COMPLETION_SENTINEL_RELPATHFMT = COMPLETION_SENTINEL_RELPATHFMT


def _bool_evidence(value: bool, *, holding_detail: str, absent_detail: str) -> Evidence:
    """Map a bare ``bool`` contract fact to a graded ``Evidence`` value.

    Used by ``CompletionSignals.__post_init__`` to populate the
    graded ``required_artifact_evidence`` and
    ``completion_sentinel_evidence`` fields from the legacy bool
    fields. The provenance carried by the upgrade is deliberately
    conservative: a bool-only fact (no wire-ledger match in scope)
    grades ``WORKSPACE_EFFECT`` when it holds (a receipt row
    stamped by the canonical-submit path, not a tools/call ledger
    match), ``ABSENT`` when it does not.

    Sites that know a stronger provenance (e.g. ``smoke_evidence``
    grading with the wire ledger) construct the ``Evidence`` directly
    and pass it to the graded field; sites that only know the bool
    pick up the conservative default and never appear as ``WIRE``.
    """
    if value:
        return Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=holding_detail,
        )
    return absent(absent_detail)


@dataclass(frozen=True)
class CompletionSignals:
    """Signals that indicate whether an agent run actually completed its work.

    Attributes:
        explicit_complete: True when the agent called the declare_complete MCP
            tool successfully (independent of artifact presence).
        required_artifact_present: ``Evidence`` value for the artifact-present
            fact. ``holds=True`` only when the run-scoped canonical submission
            receipt exists. ``Evidence``-typed (S-4): a bare ``bool`` cannot
            construct this field; ``__post_init__`` coerces a legacy ``bool``
            to ``Evidence`` at ``WORKSPACE_EFFECT`` provenance to keep the
            older call sites compiling, but new code MUST pass an ``Evidence``
            directly.
        artifact_types: Tuple of artifact type names found.
        completion_sentinel_present: ``Evidence`` value for the sentinel-
            present fact. ``holds=True`` only when the run-scoped completion
            sentinel written by ``handle_declare_complete`` exists on disk and
            HMAC-verifies when ``sentinel_secret`` is in scope. The
            plain-text marker alone can be spoofed by agent output and so
            never grades above ``WORKSPACE_EFFECT``. ``Evidence``-typed (S-4).
        artifact_required: True when policy requires a receipt for the phase's
            artifact before the durable completion sentinel can terminate the
            run.
        unsubmitted_draft_present: True when a retained draft differs from the
            canonical artifact, proving authored content was not submitted.
        required_artifact_evidence: Alias for ``required_artifact_present``;
            kept for backward compat with code that reads
            ``signals.required_artifact_evidence``. New code should read
            ``required_artifact_present`` directly.
        completion_sentinel_evidence: Alias for ``completion_sentinel_present``;
            same compat rationale.
    """

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
        # Coerce legacy bool contract fields to ``Evidence`` at the
        # conservative ``WORKSPACE_EFFECT`` provenance. New code MUST
        # pass ``Evidence`` directly; this coercion is purely to keep
        # the ~50+ legacy test/execution_state call sites compiling
        # while the type annotation is tightened. Sites that know a
        # stronger provenance (the smoke gate with the wire ledger)
        # construct ``Evidence`` directly and pass it to
        # ``required_artifact_present`` instead.
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
        # Keep the alias fields in sync with the contract fields when
        # the caller did not pass them explicitly. The default-detail
        # sentinel distinguishes "caller omitted" from "caller passed
        # the default".
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
    """Return whether durable evidence satisfies the phase completion contract.

    ``declare_complete`` is mandatory for every completion-enforced phase, so a
    valid sentinel is always required. Required-artifact phases additionally
    require their canonical submission receipt. Optional-artifact and
    artifact-free phases require no receipt.

    S-4: gates on ``.holds`` of the ``Evidence``-typed contract fields, not
    on a bare ``bool``. A holding fact graded below ``WIRE`` still ends the
    phase (the gate's job is binary completion, not trust grading); grading
    lives in ``graded_verdict`` / ``format_phase_verdict``.
    """
    # The dataclass field is annotated ``Evidence | bool`` so legacy
    # callers that pass ``True``/``False`` keep compiling; ``__post_init__``
    # coerces those into ``Evidence`` at ``WORKSPACE_EFFECT`` provenance.
    # mypy's static type does not see that coercion, so we re-derive the
    # graded ``Evidence`` here via the alias fields, which ARE always
    # ``Evidence`` after ``__post_init__``.
    sentinel_evidence = signals.completion_sentinel_evidence
    artifact_evidence = signals.required_artifact_evidence
    if not sentinel_evidence.holds or signals.unsubmitted_draft_present:
        return False
    if signals.artifact_required:
        return artifact_evidence.holds
    return True


def extract_explicit_completion(raw_output: list[str]) -> bool:
    """Return True if raw output contains the completion-response marker.

    This transcript signal is diagnostic only: model-authored output can echo
    the marker. Terminal decisions must use the independently persisted
    completion sentinel through ``completion_signals_terminal``.

    Args:
        raw_output: Raw NDJSON lines from the agent subprocess stdout.

    Returns:
        True if the completion-response marker is found in any output line.
    """
    return any(_EXPLICIT_COMPLETION_MARKER in line for line in raw_output)


def _sentinel_hmac_matches(content: str, sentinel_secret: str, run_id: str) -> bool:
    """Return True when the sentinel payload's HMAC matches the broker secret."""
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
    """Open ``.agent/state.db`` and look up the sentinel hmac.

    Returns ``(db_match, db_value)``:
    - ``(True, str|None)`` when the DB has a real sentinel row
    - ``(False, None)`` when the DB has a tombstoned row
      (``hmac == CLEARED_SENTINEL_HMAC``). The cleared state is
      terminal — the caller MUST NOT fall back to the legacy file
      path because a stale legacy file would resurrect a reused
      ``run_id``'s "completed" verdict.
    - ``(None, None)`` when the DB has no row OR is unavailable.
      The caller MAY fall back to the legacy file path.

    Tombstone handling: a row whose ``hmac`` equals
    ``CLEARED_SENTINEL_HMAC`` is treated as cleared so the read path
    honours a clear attempt that could not physically remove the row
    because ``RunStateDB`` raised on ``delete_completion_sentinel``.
    Without this, a reused ``run_id`` would inherit the previous
    run's "completed" verdict (either directly via the DB hit or
    indirectly via a stale ``completion_seen_<run_id>.json`` file
    left behind from before the clear).

    Best-effort: a missing or locked DB returns ``(None, None)``.
    """
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
    """Read the legacy sentinel file (or the test seam)."""
    sentinel_path = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
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
    """Return True when the run-scoped completion sentinel exists and is valid.

    When ``sentinel_secret`` is provided the sentinel's ``hmac`` field is
    verified against ``run_id``; a sentinel that exists on disk but
    fails HMAC verification returns ``False``. This pins the sentinel
    to the broker-owned secret so a model with workspace write
    capabilities cannot forge a valid completion sentinel.

    Storage (RFC-013 P3): reads the ``.agent/state.db`` row first via
    ``RunStateDB``; falls back to the legacy
    ``.agent/completion_seen_<run_id>.json`` file when the DB has no
    row or is unavailable. The DB read is skipped entirely when an
    ``_read_fn`` test seam is provided, matching the pre-P3 file-only
    contract that the existing unit tests rely on.
    """
    if run_id is None:
        return False

    # 1) DB-first lookup (skipped when a file-only test seam is in play).
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

    # 1b) DB tombstone (CLEARED_SENTINEL_HMAC) — the cleared state is
    # terminal. Do NOT fall through to the legacy file path: a stale
    # ``completion_seen_<run_id>.json`` left from before the clear
    # would otherwise resurrect a reused ``run_id``'s "completed"
    # verdict.
    if db_match is False:
        return False

    # 2) File-fallback path (legacy file or test seam). Only reached
    # when the DB had no row or was unavailable.
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
    """Return True when a canonical receipt exists or can be promoted from fallback.

    This is the completion-signal layer's single entry point for artifact
    presence. It first checks for a receipt; if none exists it attempts to
    promote a fallback markdown document written by the agent
    (``.agent/tmp/<type>.md``) through the canonical submit path so a
    receipt is stamped.

    Args:
        receipt_secret: RFC-013 P3 broker-owned secret for HMAC verification
            of the receipt. ``None`` falls back to the pre-P3 contract (no
            HMAC verification).
    """
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
    """Resolve worker-local fallback destinations from a scoped artifact path."""
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
    """Collect durable artifact and completion evidence for one agent run.

    explicit_complete is set from scanning raw_output for the declare_complete
    MCP tool marker for diagnostics, independently of artifact presence.
    required_artifact_present is True only when a run-scoped canonical
    submission receipt exists. completion_sentinel_present is the authoritative
    declaration signal. Call ``completion_signals_terminal`` to apply the
    required receipt-plus-sentinel conjunction.

    Args:
        workspace: Workspace root path.
        raw_output: Raw NDJSON lines from agent stdout for explicit-completion detection.
        required_artifact: Policy-derived artifact metadata.
        run_id: Run id used to key the run-scoped completion sentinel.
        sentinel_secret: RFC-013 P3: broker-owned secret for HMAC verification
            of the completion sentinel. ``None`` falls back to the pre-P3
            contract (no HMAC verification; legacy fall-through path).
            Threading a secret through this parameter is what stops a
            model with workspace write capabilities from forging a
            sentinel — the matching write side must also thread the
            same secret (see ``handle_declare_complete`` in
            ``ralph.mcp.tools.coordination``).
        receipt_secret: RFC-013 P3: broker-owned secret for HMAC verification
            of the artifact submission receipt. ``None`` falls back to the
            pre-P3 contract (no HMAC verification). Threading a secret through
            this parameter stops a model with workspace write capabilities from
            forging a receipt — the matching write side must also thread the
            same secret (see ``handle_submit_md_artifact`` in
            ``ralph.mcp.tools.md_artifact``).

    Returns:
        CompletionSignals reflecting current artifact state and explicit completion.
    """
    explicit = extract_explicit_completion(raw_output or [])
    ra = required_artifact
    sentinel_present = (
        _check_completion_sentinel(workspace, run_id, sentinel_secret=sentinel_secret)
        if run_id is not None
        else False
    )
    sentinel_evidence = _bool_evidence(
        sentinel_present,
        holding_detail="completion sentinel receipt present (graded at WORKSPACE_EFFECT; upgrade to WIRE via smoke_evidence grading when wire ledger is in scope)",
        absent_detail="completion sentinel receipt not present",
    )
    if ra is None:
        return CompletionSignals(
            explicit_complete=explicit,
            required_artifact_present=absent("phase has no registered required artifact"),
            artifact_types=(),
            completion_sentinel_present=sentinel_evidence,
        )
    # A run-scoped submission receipt is the authoritative proof that the
    # artifact was persisted for this run. A raw canonical artifact file
    # is unsafe because a stale document
    # from a previous run could falsely mark the current run complete (see
    # tests/test_agy_completion_adversarial.py). Without ``run_id``, completion
    # cannot be determined from the artifact alone.
    artifact_path = workspace / ra.artifact_path
    present = (
        is_artifact_submitted(
            workspace,
            run_id,
            ra.artifact_type,
            receipt_secret=receipt_secret,
            artifact_path=ra.artifact_path,
        )
        if (run_id is not None)
        else False
    )
    artifact_evidence = _bool_evidence(
        present,
        holding_detail="artifact submission receipt present (graded at WORKSPACE_EFFECT; upgrade to WIRE via smoke_evidence grading when wire ledger is in scope)",
        absent_detail="artifact submission receipt not present",
    )
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
    """Return the graded evidence dict for ``CompletionSignals``.

    Helper for S-5 / S-6 / phase-reporting code that wants to feed the
    same evidence values the operator-facing verdict line uses. Maps the
    graded ``Evidence`` fields on the dataclass to the stable fact
    names the lattice expects (``required_artifact_present``,
    ``completion_sentinel_present``) so the dict is interchangeable
    with ``smoke_evidence.required_facts``.
    """
    return {
        "required_artifact_present": signals.required_artifact_evidence,
        "completion_sentinel_present": signals.completion_sentinel_evidence,
    }


def graded_verdict(signals: CompletionSignals) -> tuple[str, Provenance]:
    """Grade the phase outcome against ``Provenance`` requirements.

    Analogous to ``smoke_evidence.grade_verdict``: returns
    ``(label, weakest_provenance)`` where ``label`` is one of the
    closed-vocabulary strings ``"PASS"`` or ``"DEGRADED"`` and the
    weakest provenance is the single ``Provenance`` member that
    determines the label. ``PASS`` requires every required fact to
    both hold and grade ``WIRE``; any fact below that bar demotes the
    verdict to ``"DEGRADED"``.
    """
    return grade_verdict(graded_completion_signals(signals))


def format_phase_verdict(signals: CompletionSignals) -> str:
    """Return the operator-facing verdict string for a phase.

    Used by S-5's display path. The phase verdict is a pure function
    of the graded evidence carried by the dataclass -- transcript text
    and the agent's own success claims carry no provenance and never
    influence the verdict (per F6 / DoD 14).
    """
    label, weakest = graded_verdict(signals)
    if label == "PASS":
        return "PASS"
    return f"DEGRADED ({weakest.name.lower().replace('_', '-')})"


__all__ = [
    "CompletionSignals",
    "completion_signals_terminal",
    "evaluate_completion",
    "extract_explicit_completion",
    "format_phase_verdict",
    "graded_completion_signals",
    "graded_verdict",
    "is_artifact_submitted",
]
