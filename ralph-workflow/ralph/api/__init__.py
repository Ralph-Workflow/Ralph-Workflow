"""Public API integrations exposed by Ralph.

This package is the canonical entry point for callers that want to use
Ralph's outbound integrations without depending on the CLI entry
point itself. The six public names cover the OpenCode catalog and
local model preflight use cases:

* :class:`ModelEntry` — the immutable record returned for every
  catalog entry. Carries the required ``id`` and optional ``name`` /
  ``provider`` fields; ``frozen=True`` so callers can hash and
  compare entries safely.

* :func:`fetch_catalog` — returns the full ``list[ModelEntry]``
  from ``https://models.dev/api.json``. The result is cached for the
  lifetime of the calling process with a 5-minute TTL; the TTL is
  rechecked on every call so a long-running orchestrator does not
  retain stale data indefinitely. ``fetch_catalog.cache_clear()``
  bypasses the TTL for explicit invalidation.

* :func:`get_model_by_id` — look up a single :class:`ModelEntry`
  by its fully-qualified ``"provider/model"`` identifier; returns
  ``None`` when the id is absent.

* :func:`list_providers` — sorted unique list of every provider
  present in the current catalog snapshot.

* :func:`search_models` — case-insensitive substring search over
  ``name``, ``provider``, and ``id``; returns ``list[ModelEntry]``.

* :func:`validate_local_model_support` — run a local OpenCode
  preflight probe (``opencode models --refresh <provider>``) and
  return ``None`` when the local binary supports ``model_id`` or a
  human-readable diagnostic string when it does not. Useful as the
  early-failure check before launching an agent that targets a
  specific model.
"""

from ralph.api.model_entry import ModelEntry
from ralph.api.opencode import (
    fetch_catalog,
    get_model_by_id,
    list_providers,
    search_models,
    validate_local_model_support,
)

__all__ = [
    "ModelEntry",
    "fetch_catalog",
    "get_model_by_id",
    "list_providers",
    "search_models",
    "validate_local_model_support",
]
