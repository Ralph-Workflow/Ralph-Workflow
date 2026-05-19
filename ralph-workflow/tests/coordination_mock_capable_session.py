"""Approved-capability mock session for coordination tool tests."""

from __future__ import annotations


class MockCapableSession:
    session_id = "session-env"

    def check_capability(self, cap: object) -> object:
        return "approved"
