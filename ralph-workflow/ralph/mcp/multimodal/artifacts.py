"""Normalized multimodal artifact types for the MCP surface.

Defines the resource_reference content block shape that represents media
artifacts that cannot be delivered inline (PDFs, audio, video, large images),
as well as the modality class constants used across the multimodal platform.
"""

from __future__ import annotations

from dataclasses import dataclass

MODALITY_IMAGE = "image"
MODALITY_PDF = "pdf"
MODALITY_DOCUMENT = "document"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

SUPPORTED_MODALITIES: frozenset[str] = frozenset(
    {
        MODALITY_IMAGE,
        MODALITY_PDF,
        MODALITY_DOCUMENT,
        MODALITY_AUDIO,
        MODALITY_VIDEO,
    }
)

# MIME types for each file extension, with associated modality.
MIME_TYPE_MAP: dict[str, tuple[str, str]] = {
    # Images (modality, mime_type)
    ".png": (MODALITY_IMAGE, "image/png"),
    ".jpg": (MODALITY_IMAGE, "image/jpeg"),
    ".jpeg": (MODALITY_IMAGE, "image/jpeg"),
    ".gif": (MODALITY_IMAGE, "image/gif"),
    ".webp": (MODALITY_IMAGE, "image/webp"),
    # PDFs
    ".pdf": (MODALITY_PDF, "application/pdf"),
    # Audio
    ".mp3": (MODALITY_AUDIO, "audio/mpeg"),
    ".wav": (MODALITY_AUDIO, "audio/wav"),
    ".ogg": (MODALITY_AUDIO, "audio/ogg"),
    ".m4a": (MODALITY_AUDIO, "audio/mp4"),
    ".flac": (MODALITY_AUDIO, "audio/flac"),
    ".aac": (MODALITY_AUDIO, "audio/aac"),
    # Video
    ".mp4": (MODALITY_VIDEO, "video/mp4"),
    ".avi": (MODALITY_VIDEO, "video/x-msvideo"),
    ".mov": (MODALITY_VIDEO, "video/quicktime"),
    ".mkv": (MODALITY_VIDEO, "video/x-matroska"),
    ".webm": (MODALITY_VIDEO, "video/webm"),
    # Visually meaningful documents
    ".docx": (
        MODALITY_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".pptx": (
        MODALITY_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".xlsx": (
        MODALITY_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}

# Inline-capable image MIME types (supported by read_image compatibility tool).
INLINE_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)


def infer_modality_and_mime(extension: str) -> tuple[str, str] | None:
    """Return (modality, mime_type) for a file extension, or None if unknown."""
    return MIME_TYPE_MAP.get(extension.lower())


@dataclass(frozen=True)
class ImageContent:
    """Inline image content block delivered as base64-encoded bytes."""

    data: str
    mime_type: str
    type: str = "image"
    delivery: str = "inline_image"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {"type": self.type, "data": self.data, "mimeType": self.mime_type}


@dataclass(frozen=True)
class PdfContent:
    """Typed PDF content block referencing a manifest artifact."""

    uri: str
    mime_type: str
    title: str
    type: str = "pdf"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }


@dataclass(frozen=True)
class DocumentContent:
    """Typed document content block referencing a manifest artifact."""

    uri: str
    mime_type: str
    title: str
    type: str = "document"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }


@dataclass(frozen=True)
class AudioContent:
    """Typed audio content block referencing a manifest artifact."""

    uri: str
    mime_type: str
    title: str
    type: str = "audio"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }


@dataclass(frozen=True)
class VideoContent:
    """Typed video content block referencing a manifest artifact."""

    uri: str
    mime_type: str
    title: str
    type: str = "video"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }


@dataclass(frozen=True)
class ResourceReferenceContent:
    """Content block representing a media artifact via resource reference.

    The ``delivery`` field distinguishes two cases:

    - ``'resource_reference_replay'``: a Ralph-owned ``ralph://media/...``
      artifact stored in the session manifest. The agent calls ``read_media``
      with the URI to retrieve or replay the artifact. Always pass
      ``delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY`` explicitly when
      constructing these blocks.

    - ``'resource_reference'`` (default): a URI-preserving upstream reference.
      The URI points to an external resource, not a Ralph-owned artifact.
      Used when an upstream MCP tool returns a URI-backed media block.
    """

    uri: str
    mime_type: str
    title: str
    modality: str
    type: str = "resource_reference"
    delivery: str = "resource_reference"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "modality": self.modality,
            "delivery": self.delivery,
        }


__all__ = [
    "INLINE_IMAGE_MIME_TYPES",
    "MIME_TYPE_MAP",
    "MODALITY_AUDIO",
    "MODALITY_DOCUMENT",
    "MODALITY_IMAGE",
    "MODALITY_PDF",
    "MODALITY_VIDEO",
    "SUPPORTED_MODALITIES",
    "AudioContent",
    "DocumentContent",
    "ImageContent",
    "PdfContent",
    "ResourceReferenceContent",
    "VideoContent",
    "infer_modality_and_mime",
]
