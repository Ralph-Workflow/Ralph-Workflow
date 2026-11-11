
from __future__ import annotations

import re
from dataclasses import dataclass

_UNKNOWN_PROVIDER = "unknown"

_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9._/-]{0,63}\Z")

_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")

_MAX_MODEL_ID_CHARS = 128


def _canonical_provider(raw: object) -> str:
    if not isinstance(raw, str):
        return _UNKNOWN_PROVIDER
    candidate = raw.strip().lower()
    if not _SLUG_PATTERN.match(candidate):
        return _UNKNOWN_PROVIDER
    return candidate


def _canonical_model_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    flattened = _CONTROL_RUN.sub(" ", raw).strip()
    if not flattened:
        return None
    return flattened[:_MAX_MODEL_ID_CHARS]


def _canonical_transport(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().lower()
    if not _SLUG_PATTERN.match(candidate):
        return None
    return candidate


@dataclass(frozen=True)
class MultimodalModelIdentity:

    provider: str
    model_id: str | None = None
    transport: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _canonical_provider(self.provider))
        object.__setattr__(self, "transport", _canonical_transport(self.transport))
        object.__setattr__(self, "model_id", _canonical_model_id(self.model_id))

    def is_known(self) -> bool:
        return self.provider != _UNKNOWN_PROVIDER
