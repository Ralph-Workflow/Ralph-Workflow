"""Approved-capability mock session for coordination tool tests."""

from __future__ import annotations


class MockCapableSession:
    session_id = "session-env"
    run_id = "run-env"

    def check_capability(self, capability: str) -> object:
        return "approved"
