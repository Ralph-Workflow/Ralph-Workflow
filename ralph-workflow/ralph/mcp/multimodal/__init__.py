"""Ralph multimodal platform package.

Provides the shared contract for multimodal artifacts, provider/model
capability detection, resource URI handling, and session-scoped manifests.
All runtime layers that need multimodal behavior must derive their decisions
from this package rather than duplicating provider or format knowledge.
"""

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
    get_delivery_mode,
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
    "CapabilityVerdict",
    "DeliveryMode",
    "ManifestEntry",
    "MediaManifest",
    "MultimodalModelIdentity",
    "ResourceReferenceContent",
    "build_media_uri",
    "get_delivery_mode",
    "infer_modality_and_mime",
    "new_artifact_id",
    "parse_media_uri",
]
