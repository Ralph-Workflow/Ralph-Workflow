"""Shared _ArtifactSubmitSession mock for prompt materialize tests."""

from __future__ import annotations


class _ArtifactSubmitSession:
    session_id = "test-session"
    drain = "planning_analysis"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"
