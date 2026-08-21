"""Identity dataclass for multimodal model capability detection."""

from __future__ import annotations

from dataclasses import dataclass

_UNKNOWN_PROVIDER = "unknown"


def _canonical_provider(raw: str) -> str:
    """Return the canonical spelling of a provider name."""
    return raw.strip().lower() or _UNKNOWN_PROVIDER


def _canonical_model_id(raw: str | None) -> str | None:
    """Return a model id with surrounding whitespace removed.

    STRIPPED, never lowered: a model id is a vendor's name for a
    specific model and its case can be significant, unlike a provider or
    a transport. But padding is not part of it, and this field sits
    between the two that are canonicalised while being copied verbatim
    into every verdict ``reason`` and the wire-ledger digest -- so one
    padded character produced three digests for a single run, the same
    defect the rounds before spent on its neighbours.
    """
    if raw is None:
        return None
    return raw.strip() or None


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
        object.__setattr__(self, "model_id", _canonical_model_id(self.model_id))

    def is_known(self) -> bool:
        """Return True if the provider identity is resolved (not 'unknown')."""
        return self.provider != _UNKNOWN_PROVIDER
