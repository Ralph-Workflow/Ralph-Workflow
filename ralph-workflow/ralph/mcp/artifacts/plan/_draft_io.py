"""Plan-draft and plan-artifact on-disk I/O.

The draft is a JSON file at ``.agent/artifacts/.plan_draft.json`` that
accumulates section submissions during a step-wise planning session.
The finalized plan is a JSON file at ``.agent/artifacts/plan.json`` that
holds the validated ``PlanArtifact`` content. Both reads and writes go
through a ``FileBackend`` so the production code can be unit-tested
with an in-memory backend instead of the real filesystem.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan._section_registry import (
    PLAN_ARTIFACT_PATH,
    PLAN_DRAFT_PATH,
    PLAN_DRAFT_SCHEMA_VERSION,
)
from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    normalize_plan_artifact_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def new_plan_draft(*, now_iso: Callable[[], str] = _now_iso) -> dict[str, object]:
    """Return a fresh plan draft with empty sections and timestamps."""
    now = now_iso()
    return {
        "schema_version": PLAN_DRAFT_SCHEMA_VERSION,
        "started_at": now,
        "updated_at": now,
        "sections": {},
    }


def load_plan_draft(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Read the plan draft file if present and parseable. None otherwise."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return None
    try:
        raw = backend.read_text(draft_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan draft at {}: {}", draft_path, exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Plan draft at {} is not a JSON object", draft_path)
        return None
    parsed_dict = cast("dict[str, object]", parsed)
    if not isinstance(parsed_dict.get("sections"), dict):
        logger.warning("Plan draft at {} has no 'sections' object", draft_path)
        return None
    return parsed_dict


def _semantic_draft(draft: dict[str, object]) -> dict[str, object]:
    """Return ``draft`` minus the volatile ``updated_at`` bookkeeping field.

    ``updated_at`` is a clock-derived timestamp that advances on every
    write; treating it as part of the draft's semantic content would
    defeat the idempotent-skip path. The remaining keys
    (``schema_version``, ``started_at``, ``sections``) are the durable
    content that plan-tooling decisions actually read.
    """
    return {key: value for key, value in draft.items() if key != "updated_at"}


def save_plan_draft(
    artifact_dir: Path,
    draft: dict[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str] = _now_iso,
) -> None:
    """Atomically write the plan draft file when its semantic content changes.

    Skips the temp-write + replace when the existing on-disk draft's
    content (excluding the volatile ``updated_at`` field) is
    byte-identical to the draft being saved. This mirrors the
    ``atomic_write_text_if_changed`` precedent used by
    ``ralph/pipeline/checkpoint.py`` and keeps long unattended runs
    from emitting an fseventsd notification for every no-op
    plan-section re-stage.

    ``updated_at`` is treated as bookkeeping metadata only: it is not
    read by any plan-tooling decision (section staging, validation,
    finalize, load round-trip), so the no-op skip is observable only
    on the file mtime / fseventsd side. A real content change still
    rewrites the file and advances ``updated_at``.

    The atomic ``temp write + replace`` durability guarantee is
    preserved on the write path. The helper is fail-open: when
    ``load_plan_draft`` returns ``None`` (missing / corrupt /
    unparseable file) the code falls through to a real write so any
    read uncertainty self-heals.

    ``atomic_write_text_if_changed`` cannot be reused directly here
    because it compares the FULL serialized content, including the
    always-fresh ``updated_at``; a semantic comparison that excludes
    ``updated_at`` is required to make the skip work for no-op
    re-stages.
    """
    existing = load_plan_draft(artifact_dir, backend=backend)
    if existing is not None and _semantic_draft(existing) == _semantic_draft(draft):
        return
    backend.mkdir(artifact_dir, parents=True, exist_ok=True)
    draft_path = artifact_dir / ".plan_draft.json"
    tmp_path = draft_path.with_suffix(".json.tmp")
    serialized_draft = dict(draft)
    serialized_draft["updated_at"] = now_iso()
    serialized = json.dumps(serialized_draft, indent=2, sort_keys=False)
    backend.write_text(tmp_path, serialized, encoding="utf-8")
    backend.replace(tmp_path, draft_path)


def delete_plan_draft(artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND) -> bool:
    """Remove the plan draft file. Returns True if it existed."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return False
    backend.unlink(draft_path)
    return True


def load_plan_artifact_sections(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> PlanArtifactDict | None:
    """Load the normalized sections from a finalized plan artifact if present."""
    plan_path = artifact_dir / "plan.json"
    if not backend.exists(plan_path):
        return None

    result: PlanArtifactDict | None = None
    try:
        raw = backend.read_text(plan_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
        if not isinstance(parsed, dict):
            logger.warning("Plan artifact at {} is not a JSON object", plan_path)
            return None
        parsed_dict = cast("dict[str, object]", parsed)
        content = parsed_dict.get("content") if parsed_dict.get("type") == "plan" else parsed_dict
        if not isinstance(content, dict):
            logger.warning("Plan artifact at {} has no valid 'content' object", plan_path)
            return None
        normalized = normalize_plan_artifact_content(cast("dict[str, object]", content))
        if normalized.get("noop") is not True:
            result = normalized
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan artifact at {}: {}", plan_path, exc)
    except PlanArtifactValidationError as exc:
        logger.warning(
            "Plan artifact at {} failed validation for draft hydration: {}",
            plan_path,
            exc,
        )

    return result


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_DRAFT_PATH",
    "delete_plan_draft",
    "load_plan_artifact_sections",
    "load_plan_draft",
    "new_plan_draft",
    "save_plan_draft",
]
