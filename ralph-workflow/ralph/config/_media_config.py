"""MediaConfig model — multimodal media support config."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

_DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB


class MediaConfig(RalphBaseModel):
    """Multimodal media support config in `mcp.toml`.

    Broad multimodal support (images, PDFs, audio, video, documents) is enabled
    by default. Disable with ``[media] enabled = false`` in ``mcp.toml``.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_inline_bytes: int = Field(default=_DEFAULT_MAX_INLINE_BYTES, gt=0)


__all__ = ["MediaConfig"]
