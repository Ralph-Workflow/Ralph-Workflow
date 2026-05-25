"""Standard capability-based mock session for tool tests."""

from __future__ import annotations


class MockSession:
    session_id = "test-session"

    def __init__(self, *args: object) -> None:
        self.run_id = "test-run"
        if not args:
            self._caps: set[str] = set()
        elif len(args) == 1 and isinstance(args[0], set):
            self._caps = {s for s in args[0] if isinstance(s, str)}
        else:
            self._caps = {s for s in args if isinstance(s, str)}

    def check_capability(self, capability: str) -> object:
        return capability in self._caps
