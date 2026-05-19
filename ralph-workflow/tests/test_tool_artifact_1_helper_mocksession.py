from __future__ import annotations


class MockSession:
    session_id: str = "test-session"

    def __init__(self, drain: str = "development") -> None:
        self.drain = drain

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"
