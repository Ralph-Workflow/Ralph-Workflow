"""MockSession helper for test_artifact_format_docs.py."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MockSession:
    session_id: str = "test-session"
    drain: str = "development"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"
