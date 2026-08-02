from __future__ import annotations


class _DeniedSession:
    session_id = "denied-session"

    def check_capability(self, capability: str) -> object:
        return "denied"
