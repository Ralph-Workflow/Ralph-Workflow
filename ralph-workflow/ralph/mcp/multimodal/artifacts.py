from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal._audio_content import AudioContent
from ralph.mcp.multimodal._document_content import DocumentContent
from ralph.mcp.multimodal._image_content import ImageContent
from ralph.mcp.multimodal._pdf_content import PdfContent
from ralph.mcp.multimodal._video_content import VideoContent

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

MIME_TYPE_MAP: dict[str, tuple[str, str]] = {
    ".png": (MODALITY_IMAGE, "image/png"),
    ".jpg": (MODALITY_IMAGE, "image/jpeg"),
    ".jpeg": (MODALITY_IMAGE, "image/jpeg"),
    ".gif": (MODALITY_IMAGE, "image/gif"),
    ".webp": (MODALITY_IMAGE, "image/webp"),
    ".pdf": (MODALITY_PDF, "application/pdf"),
    ".mp3": (MODALITY_AUDIO, "audio/mpeg"),
    ".wav": (MODALITY_AUDIO, "audio/wav"),
    ".ogg": (MODALITY_AUDIO, "audio/ogg"),
    ".m4a": (MODALITY_AUDIO, "audio/mp4"),
    ".flac": (MODALITY_AUDIO, "audio/flac"),
    ".aac": (MODALITY_AUDIO, "audio/aac"),
    ".mp4": (MODALITY_VIDEO, "video/mp4"),
    ".avi": (MODALITY_VIDEO, "video/x-msvideo"),
    ".mov": (MODALITY_VIDEO, "video/quicktime"),
    ".mkv": (MODALITY_VIDEO, "video/x-matroska"),
    ".webm": (MODALITY_VIDEO, "video/webm"),
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

INLINE_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)


def infer_modality_and_mime(extension: str) -> tuple[str, str] | None:
    return MIME_TYPE_MAP.get(extension.lower())


@dataclass(frozen=True)
class ResourceReferenceContent:
    uri: str
    mime_type: str
    title: str
    modality: str
    type: str = "resource_reference"
    delivery: str = "resource_reference"

    def to_dict(self) -> dict[str, object]:
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
