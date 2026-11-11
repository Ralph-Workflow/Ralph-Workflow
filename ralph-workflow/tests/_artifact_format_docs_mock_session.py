from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_GRANTED: frozenset[str] = frozenset({"artifact.submit"})
_PLANNING_GRANTED: frozenset[str] = frozenset(
    {"artifact.submit", "artifact.plan_read", "artifact.plan_write"}
)


@dataclass
class MockSession:
    session_id: str = "test-session"
    drain: str = "development"
    broker_secret: str | None = None
    granted_capabilities: frozenset[str] = field(default_factory=lambda: _DEFAULT_GRANTED)

    def check_capability(self, capability: str) -> object:
        return capability in self.granted_capabilities


def planning_session(
    session_id: str = "planning-test-session", drain: str = "planning"
) -> MockSession:
    return MockSession(
        session_id=session_id,
        drain=drain,
        granted_capabilities=_PLANNING_GRANTED,
    )
