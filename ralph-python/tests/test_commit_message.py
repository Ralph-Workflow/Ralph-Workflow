"""Tests for ralph/mcp/commit_message.py — structured commit artifact helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.commit_message import (
    read_commit_message_artifact,
    read_commit_message_from_path,
    write_commit_message_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path


class FakeFileBackend:
    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.directories.add(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self.files.pop(path, None)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return []


def test_read_commit_message_artifact_formats_structured_commit_payload(tmp_path: Path) -> None:
    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {
                    "type": "commit",
                    "subject": "feat(api): add report export",
                    "body_summary": "Add CSV export for reports.",
                    "body_details": "- Supports filtered exports\n- Keeps column order stable",
                    "body_footer": "Fixes #42",
                },
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    assert read_commit_message_artifact(tmp_path) == (
        "feat(api): add report export\n\n"
        "Add CSV export for reports.\n\n"
        "- Supports filtered exports\n- Keeps column order stable\n\n"
        "Fixes #42"
    )


def test_read_commit_message_from_path_formats_structured_skip_payload(tmp_path: Path) -> None:
    message_file = tmp_path / "commit_message.json"
    message_file.write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {"type": "skip", "reason": "No relevant diff"},
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    assert read_commit_message_from_path(message_file) == "SKIP: No relevant diff"


def test_write_commit_message_artifact_uses_injected_backend(tmp_path: Path) -> None:
    backend = FakeFileBackend()

    write_commit_message_artifact(
        tmp_path,
        {"type": "commit", "subject": "feat(core): test"},
        backend=backend,
        now_iso=lambda: "STATIC-TIME",
    )

    artifact_path = tmp_path / ".agent" / "tmp" / "commit_message.json"
    payload = json.loads(backend.read_text(artifact_path))
    assert payload["created_at"] == "STATIC-TIME"


def test_write_commit_message_artifact_rejects_non_conventional_subject(tmp_path: Path) -> None:
    backend = FakeFileBackend()

    with pytest.raises(ValueError, match="conventional commit format"):
        write_commit_message_artifact(
            tmp_path,
            {"type": "commit", "subject": "update files"},
            backend=backend,
        )
