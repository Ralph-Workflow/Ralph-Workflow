"""Shared _Session mock for MCP artifact rollback tests."""

from __future__ import annotations


class _Session:
    session_id = "sess-1"

    def check_capability(self, cap: str) -> object:
        assert cap == "artifact.submit"
        return "approved"
