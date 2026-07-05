from __future__ import annotations


class MockSession:
    session_id: str = "test-session"
    broker_secret = None

    def __init__(self, drain: str = "development") -> None:
        self.drain = drain

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read", "artifact.plan_write"}
