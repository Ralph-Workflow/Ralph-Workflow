from __future__ import annotations

import json
import uuid
from pathlib import Path

from ralph.mcp.multimodal.artifacts import MODALITY_PDF
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.tools.workspace._media_handlers import handle_read_media
from ralph.prompts.debug_dump import media_registry_path
from ralph.workspace.fs import FsWorkspace
from tests.mock_session_with_manifest import MockSessionWithManifest

_MEDIA_READ_CAPABILITY = "media.read"
_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n"
_CLAUDE = MultimodalModelIdentity(
    provider="claude",
    model_id="claude-opus-5",
    transport="claude",
)


def _persist_row(root: Path, *, block_type: str) -> str:
    artifact_id = str(uuid.uuid4())
    source = root / "doc.pdf"
    source.write_bytes(_PDF_BYTES)
    registry = root / media_registry_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "artifact_id": artifact_id,
                        "cache_path": "",
                        "source_path": "doc.pdf",
                        "modality": MODALITY_PDF,
                        "mime_type": "application/pdf",
                        "title": "doc.pdf",
                        "block_type": block_type,
                        "uri": f"ralph://media/{artifact_id}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return f"ralph://media/{artifact_id}"


def _block_types(result: object) -> list[str | None]:
    content = getattr(result, "content", [])
    return [getattr(block, "type", None) for block in content]


def test_a_registry_row_naming_the_wrong_block_type_is_overruled(tmp_path: Path) -> None:
    uri = _persist_row(tmp_path, block_type="video")
    session = MockSessionWithManifest(_MEDIA_READ_CAPABILITY, model_identity=_CLAUDE)

    result = handle_read_media(session, FsWorkspace(tmp_path), {"path": uri})

    assert result.is_error is False, result.content
    assert "video" not in _block_types(result), result.content
    assert _block_types(result) == ["pdf"], result.content


def test_a_registry_row_that_agrees_still_replays_normally(tmp_path: Path) -> None:
    uri = _persist_row(tmp_path, block_type="pdf")
    session = MockSessionWithManifest(_MEDIA_READ_CAPABILITY, model_identity=_CLAUDE)

    result = handle_read_media(session, FsWorkspace(tmp_path), {"path": uri})

    assert result.is_error is False, result.content
    assert _block_types(result) == ["pdf"], result.content
