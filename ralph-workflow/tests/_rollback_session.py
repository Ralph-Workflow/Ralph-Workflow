from __future__ import annotations


class _Session:
    session_id = "sess-1"
    broker_secret = None

    def check_capability(self, cap: str) -> object:
        assert cap == "artifact.submit"
        return "approved"
