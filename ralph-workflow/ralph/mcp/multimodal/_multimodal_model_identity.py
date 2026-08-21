"""Identity dataclass for multimodal model capability detection."""

from __future__ import annotations

from dataclasses import dataclass

_UNKNOWN_PROVIDER = "unknown"


def _canonical_provider(raw: str) -> str:
    """Return the canonical spelling of a provider name."""
    return raw.strip().lower() or _UNKNOWN_PROVIDER


def _canonical_transport(raw: str | None) -> str | None:
    """Return the canonical spelling of a transport, or ``None``."""
    if raw is None:
        return None
    return raw.strip().lower() or None


@dataclass(frozen=True)
class MultimodalModelIdentity:
    """Identifies the provider and model for capability detection."""

    provider: str
    model_id: str | None = None
    transport: str | None = None

    def __post_init__(self) -> None:
        """Canonicalise the two fields every guard matches on.

        Nine separate seams write a transport spelling and each was
        fixed in turn; the tenth field, ``provider``, was never
        normalised at all -- and ``get_delivery_mode`` lowercases when
        it matches but does not strip, so ``' claude '`` missed every
        entry in the capability matrix and silently fell through to
        "unknown provider" delivery. One padded character in a persisted
        payload changed what an agent was served, and produced a
        different wire-ledger digest for the same run.

        Normalising HERE makes those seams belt-and-braces instead of
        the guarantee: an identity cannot be constructed carrying a
        spelling its own matcher will not recognise.

        A blank provider is ``unknown``, not a resolved provider named
        the empty string -- ``is_known()`` gates the
        identity-unknown degradation warning, so ``{"provider": ""}``
        read as resolved and suppressed it.
        """
        object.__setattr__(self, "provider", _canonical_provider(self.provider))
        object.__setattr__(self, "transport", _canonical_transport(self.transport))

    def is_known(self) -> bool:
        """Return True if the provider identity is resolved (not 'unknown')."""
        return self.provider != _UNKNOWN_PROVIDER
