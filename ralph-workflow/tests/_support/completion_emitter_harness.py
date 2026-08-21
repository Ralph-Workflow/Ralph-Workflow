"""Minimal session and workspace for driving the completion emitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _CompletionSession:
    """The surface ``handle_declare_complete`` reads."""

    session_id = "sess-completion-roundtrip"
    run_id = "completion-roundtrip-run"
    drain = "development"
    broker_secret = None

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def build_completion_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, FsWorkspace]:
    """Return a session and workspace the completion emitter accepts."""
    monkeypatch.chdir(tmp_path)
    return _CompletionSession(), FsWorkspace(tmp_path)


__all__ = ["build_completion_context"]
