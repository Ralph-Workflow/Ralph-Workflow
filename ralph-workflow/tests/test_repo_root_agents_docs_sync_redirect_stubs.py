from __future__ import annotations

_CURRENT_GUIDES = [
    "verification.md",
    "testing-guide.md",
    "type-ignore-policy.md",
    "workspace-trait.md",
]

_REDIRECT_STUBS: list[str] = []


class TestRedirectStubs:
    def test_redirect_stub_class_is_empty(self) -> None:
        assert _REDIRECT_STUBS == [], (
            "Redirect-stub list must stay empty; if a new stub is "
            "added intentionally, update the wt-026 consolidation note."
        )
