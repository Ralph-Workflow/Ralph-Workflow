"""Always-capable mock session for coordination tool tests."""

from __future__ import annotations


class MockSession:
    session_id = "session-1"
    run_id = "run-1"
    broker_secret = None

    def check_capability(self, capability: str) -> object:
        return True
