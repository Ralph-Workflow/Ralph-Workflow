from __future__ import annotations


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"
