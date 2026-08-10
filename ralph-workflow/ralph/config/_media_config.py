"""MediaConfig model — multimodal media support config.

The ``enabled`` flag is retained on the schema for backward compatibility
(an existing ``mcp.toml`` must still parse), but it is ignored: ``media.read``
is granted to every drain and ``read_media`` / ``read_image`` are always
listed in ``tools/list``. Specifying ``[media] enabled = false`` is
permitted and emits a single ``logger.warning`` naming the ignored key;
the canonical config-resolution path always reads ``enabled=True``.

This is enforcement of
``.agent/PRODUCT_CRITERIA.md`` (criterion 1): the MCP endpoints that
carry multimodal data must always stay enabled. ``max_inline_bytes``
remains a genuine tunable.
"""

from __future__ import annotations

import logging

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

_DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB

_MEDIA_LOGGER = logging.getLogger("ralph.config.media")

#: Warning message emitted when a config sets ``[media] enabled = false``.
#: The key is accepted and ignored: ``media.read`` is granted to every
#: drain and both ``read_media`` / ``read_image`` are always listed in
#: ``tools/list``. See ``.agent/PRODUCT_CRITERIA.md`` (criterion 1).
_ENABLED_IGNORED_WARNING = (
    "[media] enabled = false is accepted and ignored: the multimodal MCP "
    "endpoints (media.read, read_media, read_image) always stay enabled. "
    "See .agent/PRODUCT_CRITERIA.md (criterion 1)."
)


class MediaConfig(RalphBaseModel):
    """Multimodal media support config in ``mcp.toml``.

    The ``enabled`` field is retained on the schema for backward
    compatibility but is *ignored*: the multimodal endpoints always
    stay enabled (criterion 1). Setting ``enabled = false`` triggers a
    single :func:`logging.warning` naming the ignored key on
    construction.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_inline_bytes: int = Field(default=_DEFAULT_MAX_INLINE_BYTES, gt=0)

    @field_validator("enabled", mode="after")
    @classmethod
    def _coerce_enabled_to_true(cls, value: bool) -> bool:
        """Coerce ``enabled`` to ``True`` and emit one warning when asked for false.

        The ``enabled`` field is retained on the schema so an
        existing ``mcp.toml`` can still parse without a hard
        failure, but the value is IGNORED: ``media.read`` is always
        granted to every drain and ``read_media`` / ``read_image``
        are always listed in ``tools/list`` (criterion 1). When a
        config asked for ``enabled = false`` we log a single
        ``logger.warning`` naming the ignored key and return
        ``True`` so the resolved model carries the canonical value.

        ``logging.warning`` is used (not ``warnings.warn``) because
        ``pytest.ini`` sets ``filterwarnings = error`` and a
        ``warnings.warn`` here would turn every config load in the
        suite into an error. ``logging`` has its own level config
        (Pytest does not promote it to error), so the warning stays
        a single observable signal rather than a fatal failure.
        """
        if value is False:
            _MEDIA_LOGGER.warning(_ENABLED_IGNORED_WARNING)
            return True
        return value


__all__ = ["MediaConfig"]
