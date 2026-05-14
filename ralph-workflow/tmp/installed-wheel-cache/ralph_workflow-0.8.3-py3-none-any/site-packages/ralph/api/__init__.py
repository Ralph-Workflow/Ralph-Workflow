"""Public API integrations exposed by Ralph.

These helpers cover outbound integrations that are useful outside the CLI entry
point itself, such as OpenCode catalog and model lookup.
"""

from ralph.api.opencode import fetch_catalog, get_model_by_id

__all__ = [
    "fetch_catalog",
    "get_model_by_id",
]
