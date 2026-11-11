
from __future__ import annotations

import logging

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

_DEFAULT_MAX_INLINE_BYTES = 5_242_880

_MEDIA_LOGGER = logging.getLogger("ralph.config.media")

_ENABLED_IGNORED_WARNING = (
    "[media] enabled = false is accepted and ignored: the multimodal MCP "
    "endpoints (media.read, read_media, read_image) always stay enabled. "
    "See .agent/PRODUCT_CRITERIA.md (criterion 1)."
)


class MediaConfig(RalphBaseModel):

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_inline_bytes: int = Field(default=_DEFAULT_MAX_INLINE_BYTES, gt=0)

    @field_validator("enabled", mode="after")
    @classmethod
    def _coerce_enabled_to_true(cls, value: bool) -> bool:
        if value is False:
            _MEDIA_LOGGER.warning(_ENABLED_IGNORED_WARNING)
            return True
        return value


__all__ = ["MediaConfig"]
