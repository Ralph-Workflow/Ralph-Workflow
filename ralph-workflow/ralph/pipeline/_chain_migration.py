"""Legacy state field migration mappings for checkpoint backward compatibility."""

from __future__ import annotations

LEGACY_CHAIN_FIELD_TO_PHASE: dict[str, str] = {
    "planning_chain": "planning",
    "dev_chain": "development",
    "dev_analysis_chain": "development_analysis",
    "rev_chain": "review",
    "review_analysis_chain": "review_analysis",
    "fix_chain": "fix",
}
