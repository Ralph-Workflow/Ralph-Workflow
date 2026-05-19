"""Ralph multimodal platform package.

Provides the shared contract for multimodal artifacts, provider/model
capability detection, resource URI handling, session-scoped manifests,
and failure taxonomy. All runtime layers that need multimodal behavior
must derive their decisions from this package rather than duplicating
provider or format knowledge.
"""

from ralph.mcp.multimodal._audio_content import AudioContent
from ralph.mcp.multimodal._document_content import DocumentContent
from ralph.mcp.multimodal._image_content import ImageContent
from ralph.mcp.multimodal._pdf_content import PdfContent
from ralph.mcp.multimodal._video_content import VideoContent
from ralph.mcp.multimodal.artifacts import (
    INLINE_IMAGE_MIME_TYPES,
    MIME_TYPE_MAP,
    MODALITY_AUDIO,
    MODALITY_DOCUMENT,
    MODALITY_IMAGE,
    MODALITY_PDF,
    MODALITY_VIDEO,
    SUPPORTED_MODALITIES,
    ResourceReferenceContent,
    infer_modality_and_mime,
)
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    CapabilityVerdict,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    get_delivery_mode,
    profile_from_payload,
    resolve_capability_profile,
)
from ralph.mcp.multimodal.errors import (
    MultimodalFailure,
    MultimodalFailureKind,
)
from ralph.mcp.multimodal.resources import (
    MEDIA_URI_TEMPLATE,
    ManifestEntry,
    MediaManifest,
    build_media_uri,
    new_artifact_id,
    parse_media_uri,
)

__all__ = [
    "INLINE_IMAGE_MIME_TYPES",
    "MEDIA_URI_TEMPLATE",
    "MIME_TYPE_MAP",
    "MODALITY_AUDIO",
    "MODALITY_DOCUMENT",
    "MODALITY_IMAGE",
    "MODALITY_PDF",
    "MODALITY_VIDEO",
    "SUPPORTED_MODALITIES",
    "UNKNOWN_IDENTITY",
    "AudioContent",
    "CapabilityVerdict",
    "DeliveryMode",
    "DocumentContent",
    "ImageContent",
    "ManifestEntry",
    "MediaManifest",
    "MultimodalFailure",
    "MultimodalFailureKind",
    "MultimodalModelIdentity",
    "PdfContent",
    "ResolvedCapabilityProfile",
    "ResourceReferenceContent",
    "VideoContent",
    "build_media_uri",
    "get_delivery_mode",
    "infer_modality_and_mime",
    "new_artifact_id",
    "parse_media_uri",
    "profile_from_payload",
    "resolve_capability_profile",
]
