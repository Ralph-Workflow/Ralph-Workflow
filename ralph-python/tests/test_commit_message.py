"""Tests for ralph/mcp/commit_message.py — structured commit artifact helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.commit_message import read_commit_message_artifact, read_commit_message_from_path

if TYPE_CHECKING:
    from pathlib import Path


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
