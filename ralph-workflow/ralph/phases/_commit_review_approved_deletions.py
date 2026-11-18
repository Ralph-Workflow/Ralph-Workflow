from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Final

from ralph.agents._broker_secret import parent_broker_secret
from ralph.mcp.artifacts.completion_receipts import read_latest_artifact_submission_snapshot
from ralph.mcp.artifacts.markdown import parse_markdown_document
from ralph.phases.required_artifacts import resolve_planning_artifact_type

_APPROVED_DELETIONS_HEADING_LEVEL: Final[int] = 2


def approved_deletions_for_run(
    repo_root: Path,
    run_id: str,
    artifacts_policy: object | None,
) -> frozenset[str]:
    from ralph.policy.models import ArtifactsPolicy

    if not isinstance(artifacts_policy, ArtifactsPolicy):
        return frozenset()
    artifact_type = resolve_planning_artifact_type(artifacts_policy)
    if artifact_type is None:
        return frozenset()
    receipt_snapshot = read_latest_artifact_submission_snapshot(
        repo_root,
        artifact_type,
        receipt_secret=parent_broker_secret(),
    )
    if receipt_snapshot is None:
        return frozenset()
    receipt_run_id, snapshot = receipt_snapshot
    snapshot_path, snapshot_digest = snapshot
    content = _read_valid_submission_snapshot(
        repo_root,
        receipt_run_id,
        artifact_type,
        snapshot_path,
        snapshot_digest,
    )
    if content is None:
        return frozenset()
    return _parse_approved_deletions(content)


def _read_valid_submission_snapshot(
    repo_root: Path,
    run_id: str,
    artifact_type: str,
    relative_path: str,
    expected_digest: str,
) -> bytes | None:
    canonical = PurePosixPath(".agent", "receipts", run_id, "submissions", f"{artifact_type}.md")
    snapshot_path = PurePosixPath(relative_path)
    if snapshot_path != canonical:
        return None
    path = repo_root.joinpath(*snapshot_path.parts)
    try:
        if path.is_symlink():
            return None
        receipt_dir = (repo_root / ".agent" / "receipts" / run_id).resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(receipt_dir)
        content = path.read_bytes()
        canonical_content = (
            repo_root / ".agent" / "artifacts" / f"{artifact_type}.md"
        ).read_bytes()
    except (OSError, RuntimeError, ValueError):
        return None
    if hashlib.sha256(content).hexdigest() != expected_digest:
        return None
    if hashlib.sha256(canonical_content).hexdigest() != expected_digest:
        return None
    return content


def _parse_approved_deletions(content: bytes) -> frozenset[str]:
    try:
        document, _ = parse_markdown_document(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return frozenset()
    section = document.section("Approved deletions")
    if section is None or section.level != _APPROVED_DELETIONS_HEADING_LEVEL:
        return frozenset()
    approved: set[str] = set()
    for line in section.lines:
        match = re.fullmatch(r"- `([^`]+)`", line.text)
        if match is None:
            continue
        raw: str = match.group(1)
        if "\\" in raw or raw.startswith("/") or not _is_literal_repo_path(raw):
            continue
        path_value = PurePosixPath(raw)
        if not raw or ".." in path_value.parts or path_value.as_posix() != raw:
            continue
        approved.add(raw)
    return frozenset(approved)


def _is_literal_repo_path(path: str) -> bool:
    return not any(character in path for character in "*?[]{}")
