"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import base64
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.artifacts import (
    AudioContent,
    DocumentContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
)
from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.errors import MultimodalFailureKind
from ralph.mcp.multimodal.resources import MediaManifest, build_media_uri
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ImageContent,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    handle_read_media,
)
from ralph.workspace.fs import FsWorkspace

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadMedia:

    class MockSession:
        session_id = "test-session"

        def __init__(self, *args: object) -> None:
            if not args:
                self._caps: set[str] = set()
            elif len(args) == 1 and isinstance(args[0], set):
                self._caps = {s for s in args[0] if isinstance(s, str)}
            else:
                self._caps = {s for s in args if isinstance(s, str)}

        def check_capability(self, capability: str) -> object:
            return capability in self._caps

    @dataclass
    class MockSessionWithManifest:
        allowed_capability: str | None = None
        session_id: str = "test-session"
        media_manifest: MediaManifest = field(default_factory=MediaManifest)
        model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

        def check_capability(self, capability: str) -> object:
            return capability == self.allowed_capability

        def check_edit_area(self, path: str) -> object:
            return True

    def test_no_manifest_returns_explicit_error(self) -> None:
        """When no session manifest is available, resource-reference delivery returns an error."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            # MockSession has no media_manifest attribute
            session = MockSession(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is True
            msg = cast("ToolContent", result.content[0]).text
            assert "no active session manifest" in msg
            assert "report.pdf" in msg
        finally:
            Path(temp_path).unlink()

    def test_requires_media_read_capability(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError) as exc_info:
            handle_read_media(MockSession(), ws, {"path": "image.png"})

        assert "media.read" in str(exc_info.value)

    def test_returns_error_for_unsupported_format(self) -> None:
        ws = MagicMock()

        result = handle_read_media(
            MockSession(MEDIA_READ_CAPABILITY),
            ws,
            {"path": "file.txt"},
        )

        assert result.is_error is True
        assert "Unsupported media format" in cast("ToolContent", result.content[0]).text

    def test_inline_image_returns_image_content_block(self) -> None:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            )

            result = handle_read_media(session, ws, {"path": "test.png"})

            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ImageContent)
            assert content.type == "image"
            assert content.mime_type == "image/png"
        finally:
            Path(temp_path).unlink()

    def test_pdf_returns_resource_reference_block(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake pdf content"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.type == "resource_reference"
            assert content.modality == "pdf"
            assert content.mime_type == "application/pdf"
            assert content.title == "report.pdf"
            assert content.delivery == "resource_reference_replay"
            assert content.uri.startswith("ralph://media/")
        finally:
            Path(temp_path).unlink()

    def test_pdf_stored_in_manifest(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake pdf content"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})
            content = cast("ResourceReferenceContent", result.content[0])

            # The artifact must be stored in the manifest
            assert not session.media_manifest.is_empty()
            entries = session.media_manifest.list_entries()
            assert len(entries) == 1
            entry = entries[0]
            assert entry.uri == content.uri
            assert entry.mime_type == "application/pdf"
            assert entry.modality == "pdf"
            assert entry.raw_bytes == pdf_bytes
        finally:
            Path(temp_path).unlink()

    def test_audio_returns_resource_reference_block(self) -> None:
        mp3_bytes = b"ID3" + b"\x00" * 50  # Minimal fake MP3

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "clip.mp3"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "audio"
            assert content.mime_type == "audio/mpeg"
            assert content.uri.startswith("ralph://media/")
        finally:
            Path(temp_path).unlink()

    def test_video_returns_resource_reference_block(self) -> None:
        mp4_bytes = b"\x00" * 100  # Fake video bytes

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(mp4_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "video.mp4"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "video"
            assert content.mime_type == "video/mp4"
        finally:
            Path(temp_path).unlink()

    def test_oversized_image_returns_resource_reference_block(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG" + b"\x00" * 100)  # Fake PNG
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "large.png"}, max_inline_bytes=10)

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "image"
            assert content.mime_type == "image/png"
        finally:
            Path(temp_path).unlink()

    def test_resource_reference_to_dict_shape(self) -> None:
        ref = ResourceReferenceContent(
            uri="ralph://media/test-id",
            mime_type="application/pdf",
            title="report.pdf",
            modality="pdf",
        )
        d = ref.to_dict()
        assert d["type"] == "resource_reference"
        assert d["uri"] == "ralph://media/test-id"
        assert d["mimeType"] == "application/pdf"
        assert d["title"] == "report.pdf"
        assert d["modality"] == "pdf"
        assert d["delivery"] == "resource_reference"

    def test_resource_reference_persists_to_session_index(self, tmp_path: Path) -> None:
        """handle_read_media must write artifact metadata to the session media index."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        media_file = tmp_path / "report.pdf"
        media_file.write_bytes(pdf_bytes)

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        session = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)

        result = handle_read_media(session, ws, {"path": "report.pdf"})

        assert result.is_error is False
        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        assert index_path.exists(), (
            "Media session index must be written after resource_reference delivery"
        )
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "2"
        assert data["phase"] == "development"
        artifacts = data["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["modality"] == "pdf"
        assert artifacts[0]["mime_type"] == "application/pdf"
        assert artifacts[0]["title"] == "report.pdf"
        assert artifacts[0]["delivery"] == "resource_reference_replay"
        assert artifacts[0]["uri"].startswith("ralph://media/")
        assert artifacts[0]["source_path"] == "report.pdf"
        assert artifacts[0]["cache_path"].startswith(".agent/tmp/media/")
        assert artifacts[0]["source_uri"] == ""
        assert artifacts[0]["block_type"] == ""
        # Verify durable cache was written
        artifact_id = artifacts[0]["uri"].rsplit("/", 1)[-1]
        cache_file = tmp_path / ".agent" / "tmp" / "media" / artifact_id
        assert cache_file.exists(), "Durable cache file must be written alongside session index"
        assert cache_file.read_bytes() == pdf_bytes
        # Verify centralized registry entry
        registry_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
        assert registry_path.exists(), "Centralized media registry must be written"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["schema_version"] == "2"
        reg_artifacts = registry["artifacts"]
        reg_entry = next(a for a in reg_artifacts if a["artifact_id"] == artifact_id)
        assert reg_entry["source_path"] == "report.pdf"
        assert reg_entry["cache_path"].startswith(".agent/tmp/media/")

    def test_resource_reference_accumulates_entries_in_session_index(self, tmp_path: Path) -> None:
        """Multiple read_media calls must append entries to the session index."""

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        session = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)

        pdf1 = tmp_path / "a.pdf"
        pdf2 = tmp_path / "b.pdf"
        pdf1.write_bytes(b"%PDF-1.4 doc1")
        pdf2.write_bytes(b"%PDF-1.4 doc2")

        handle_read_media(session, ws, {"path": "a.pdf"})
        handle_read_media(session, ws, {"path": "b.pdf"})

        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        artifacts = data["artifacts"]
        assert len(artifacts) == 2
        titles = {a["title"] for a in artifacts}
        assert "a.pdf" in titles
        assert "b.pdf" in titles

    def test_resource_reference_repeated_same_file_replaces_live_session_entry(
        self, tmp_path: Path
    ) -> None:
        """Repeated reads of the same artifact must not grow the live session set."""

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        media_file = tmp_path / "report.pdf"
        media_file.write_bytes(b"%PDF-1.4 repeatable")

        session = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)

        first = handle_read_media(session, ws, {"path": "report.pdf"})
        second = handle_read_media(session, ws, {"path": "report.pdf"})

        assert first.is_error is False
        assert second.is_error is False
        assert len(session.media_manifest.list_entries()) == 1

        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        artifacts = data["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["source_path"] == "report.pdf"

    # -------------------------------------------------------------------------
    # Typed-block delivery tests (Claude provider)
    # -------------------------------------------------------------------------

    def test_claude_pdf_returns_typed_pdf_block(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(
                    provider="claude", model_id="claude-3-5-sonnet-20241022"
                ),
            )
            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, PdfContent)
            assert content.type == "pdf"
            assert content.delivery == "typed_block"
            assert content.uri.startswith("ralph://media/")
            assert content.mime_type == "application/pdf"
            assert content.title == "report.pdf"
        finally:
            Path(temp_path).unlink()

    def test_gemini_audio_returns_typed_audio_block(self) -> None:
        mp3_bytes = b"ID3" + b"\x00" * 50
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="gemini"),
            )
            result = handle_read_media(session, ws, {"path": "clip.mp3"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, AudioContent)
            assert content.type == "audio"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    def test_gemini_video_returns_typed_video_block(self) -> None:
        mp4_bytes = b"\x00" * 100
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(mp4_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="gemini"),
            )
            result = handle_read_media(session, ws, {"path": "video.mp4"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, VideoContent)
            assert content.type == "video"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    def test_claude_document_returns_typed_document_block(self) -> None:
        docx_bytes = b"PK\x03\x04" + b"\x00" * 50  # Fake DOCX (zip magic)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(docx_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            )
            result = handle_read_media(session, ws, {"path": "doc.docx"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, DocumentContent)
            assert content.type == "document"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    # -------------------------------------------------------------------------
    # Replay handle tests (ralph://media/{artifact_id})
    # -------------------------------------------------------------------------

    def test_replay_handle_precedence_before_filesystem_lookup(self) -> None:
        """A ralph://media/... handle must be resolved from manifest before any filesystem check."""
        png_bytes = b"\x89PNG" + b"\x00" * 20
        session = MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(provider="claude"),
        )
        entry = session.media_manifest.add(
            title="capture.png",
            mime_type="image/png",
            modality="image",
            raw_bytes=png_bytes,
        )

        ws = MagicMock()
        result = handle_read_media(session, ws, {"path": entry.uri})

        # Should succeed from manifest without touching the filesystem
        assert result.is_error is False
        ws.absolute_path.assert_not_called()
        content = result.content[0]
        assert isinstance(content, ImageContent)

    def test_replay_handle_returns_typed_pdf_block_from_manifest(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake"
        session = MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(provider="claude"),
        )
        entry = session.media_manifest.add(
            title="report.pdf",
            mime_type="application/pdf",
            modality="pdf",
            raw_bytes=pdf_bytes,
        )

        ws = MagicMock()
        result = handle_read_media(session, ws, {"path": entry.uri})

        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, PdfContent)
        assert content.uri == entry.uri

    def test_replay_invalid_handle_returns_invalid_replay_handle_error(self) -> None:
        session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
        ws = MagicMock()

        result = handle_read_media(session, ws, {"path": "ralph://media/not-a-valid-uuid"})

        assert result.is_error is True
        text = cast("ToolContent", result.content[0]).text
        assert MultimodalFailureKind.INVALID_REPLAY_HANDLE in text

    def test_replay_unknown_artifact_id_returns_missing_replay_source_error(self) -> None:
        unknown_uri = build_media_uri(str(uuid.uuid4()))
        session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
        ws = MagicMock()

        result = handle_read_media(session, ws, {"path": unknown_uri})

        assert result.is_error is True
        text = cast("ToolContent", result.content[0]).text
        assert MultimodalFailureKind.MISSING_REPLAY_SOURCE in text

    def test_cross_session_replay_from_persisted_cache(self, tmp_path: Path) -> None:
        """A replay handle must work across sessions using the persisted registry."""

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(
                default_factory=lambda: MultimodalModelIdentity(provider="claude")
            )

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        pdf_bytes = b"%PDF-1.4 fake pdf for cross-session"
        media_file = tmp_path / "doc.pdf"
        media_file.write_bytes(pdf_bytes)

        # Session 1: read the file and persist to registry
        session1 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)
        result1 = handle_read_media(session1, ws, {"path": "doc.pdf"})
        assert result1.is_error is False
        # The artifact URI from session 1
        content1 = result1.content[0]
        assert isinstance(content1, PdfContent)
        artifact_uri = content1.uri

        # Session 2: new empty manifest (simulates new session)
        session2 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        assert session2.media_manifest.get(artifact_uri.rsplit("/", 1)[-1]) is None

        # Replay from persisted registry
        result2 = handle_read_media(session2, ws, {"path": artifact_uri})
        assert result2.is_error is False
        content2 = result2.content[0]
        assert isinstance(content2, PdfContent)
        assert content2.uri == artifact_uri

    def test_cross_session_replay_fails_when_cache_deleted(self, tmp_path: Path) -> None:
        """Replay returns missing_replay_source when cache bytes are gone."""

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(
                default_factory=lambda: MultimodalModelIdentity(provider="claude")
            )

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        pdf_bytes = b"%PDF-1.4 fake pdf for cache-deleted test"
        media_file = tmp_path / "gone.pdf"
        media_file.write_bytes(pdf_bytes)

        session1 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)
        result1 = handle_read_media(session1, ws, {"path": "gone.pdf"})
        assert result1.is_error is False
        content1 = result1.content[0]
        assert isinstance(content1, PdfContent)
        artifact_uri = content1.uri
        artifact_id = artifact_uri.rsplit("/", 1)[-1]

        # Delete both the durable cache and the source file
        cache_file = tmp_path / ".agent" / "tmp" / "media" / artifact_id
        cache_file.unlink()
        media_file.unlink()

        # Session 2: replay should fail with missing_replay_source
        session2 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        result2 = handle_read_media(session2, ws, {"path": artifact_uri})
        assert result2.is_error is True
        text = cast("ToolContent", result2.content[0]).text
        assert MultimodalFailureKind.MISSING_REPLAY_SOURCE in text

    def test_typed_block_to_dict_shapes(self) -> None:
        pdf = PdfContent(uri="ralph://media/x", mime_type="application/pdf", title="r.pdf")
        doc = DocumentContent(
            uri="ralph://media/y",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            title="d.docx",
        )
        audio = AudioContent(uri="ralph://media/a", mime_type="audio/mpeg", title="c.mp3")
        video = VideoContent(uri="ralph://media/v", mime_type="video/mp4", title="v.mp4")

        assert pdf.to_dict()["type"] == "pdf"
        assert pdf.to_dict()["mimeType"] == "application/pdf"
        assert pdf.to_dict()["delivery"] == "typed_block"
        assert doc.to_dict()["type"] == "document"
        assert audio.to_dict()["type"] == "audio"
        assert video.to_dict()["type"] == "video"


MockSession = TestHandleReadMedia.MockSession
MockSessionWithManifest = TestHandleReadMedia.MockSessionWithManifest
