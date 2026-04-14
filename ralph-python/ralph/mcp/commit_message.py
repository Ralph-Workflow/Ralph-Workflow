"""Commit-message artifact helpers.

Canonical commit messages are stored as MCP-style JSON artifacts in
`.agent/tmp/commit_message.json`. A plain-text mirror in
`.agent/tmp/commit-message.txt` is maintained for CLI compatibility.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts import Artifact

if TYPE_CHECKING:
    from pathlib import Path

COMMIT_MESSAGE_ARTIFACT = ".agent/tmp/commit_message.json"
COMMIT_MESSAGE_TEXT = ".agent/tmp/commit-message.txt"
COMMIT_MESSAGE_TYPE = "commit_message"
COMMIT_MESSAGE_NAME = "commit_message"


def commit_message_artifact_path(repo_root: Path) -> Path:
    return repo_root / COMMIT_MESSAGE_ARTIFACT


def commit_message_text_path(repo_root: Path) -> Path:
    return repo_root / COMMIT_MESSAGE_TEXT


def write_commit_message_artifact(repo_root: Path, message: str) -> None:
    artifact_path = commit_message_artifact_path(repo_root)
    text_path = commit_message_text_path(repo_root)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)

    artifact = Artifact(
        name=COMMIT_MESSAGE_NAME,
        artifact_type=COMMIT_MESSAGE_TYPE,
        content={"message": message},
    )
    artifact_path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")
    text_path.write_text(message, encoding="utf-8")


def read_commit_message_artifact(repo_root: Path) -> str | None:
    artifact_path = commit_message_artifact_path(repo_root)
    if artifact_path.exists():
        payload = cast("dict[str, object]", json.loads(artifact_path.read_text(encoding="utf-8")))
        artifact = Artifact.from_dict(payload)
        message = artifact.content.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    text_path = commit_message_text_path(repo_root)
    if not text_path.exists():
        return None
    contents = text_path.read_text(encoding="utf-8").strip()
    return contents or None


def read_commit_message_from_path(message_file: Path) -> str | None:
    if message_file.suffix == ".json":
        if not message_file.exists():
            return None
        payload = cast("dict[str, object]", json.loads(message_file.read_text(encoding="utf-8")))
        artifact = Artifact.from_dict(payload)
        message = artifact.content.get("message")
        return message.strip() if isinstance(message, str) and message.strip() else None

    if not message_file.exists():
        return None
    contents = message_file.read_text(encoding="utf-8").strip()
    return contents or None


def delete_commit_message_artifacts(repo_root: Path) -> None:
    for path in (commit_message_artifact_path(repo_root), commit_message_text_path(repo_root)):
        if path.exists():
            path.unlink()
