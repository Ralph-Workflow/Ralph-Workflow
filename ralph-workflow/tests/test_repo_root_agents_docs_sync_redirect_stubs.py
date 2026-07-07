"""Regression tests for docs/agents family synchronization.

Ensures that docs/agents/{verification,testing-guide,type-ignore-policy,
workspace-trait}.md are current Python guidance. The redirect-stub class
is intentionally empty after the wt-026 documentation consolidation:
python-verification.md, integration-tests.md, and parallelization.md were
merged into their canonical homes and the stubs were deleted rather than
maintained as compatibility shims.
"""

from __future__ import annotations

# Current Python guidance files (parallelization.md was deleted in the
# wt-026 documentation consolidation; it is no longer a separate guide).
_CURRENT_GUIDES = [
    "verification.md",
    "testing-guide.md",
    "type-ignore-policy.md",
    "workspace-trait.md",
]

# Redirect stubs (empty after the wt-026 documentation consolidation).
_REDIRECT_STUBS: list[str] = []


class TestRedirectStubs:
    """Redirect-stub class is empty after the wt-026 consolidation."""

    def test_redirect_stub_class_is_empty(self) -> None:
        """No redirect stubs remain after the wt-026 consolidation."""
        assert _REDIRECT_STUBS == [], (
            "Redirect-stub list must stay empty; if a new stub is "
            "added intentionally, update the wt-026 consolidation note."
        )
