from __future__ import annotations

import json

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    caller_identity_for,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import session_payload_json

_SESSION_IDENTITY = MultimodalModelIdentity(
    provider="openai",
    model_id="gpt-5",
    transport="codex",
)
_DELEGATE = MultimodalModelIdentity(
    provider="claude",
    model_id="claude-opus-5",
    transport="claude",
)


def test_a_delegate_cannot_bring_its_own_transport() -> None:
    resolved = caller_identity_for(_SESSION_IDENTITY, _DELEGATE)

    assert resolved.transport == "codex"
    assert resolved.provider != "claude"


def test_a_session_without_a_delegate_is_its_own_caller() -> None:
    assert caller_identity_for(_SESSION_IDENTITY, None) is _SESSION_IDENTITY


def test_the_handshake_payload_carries_no_delegated_keys() -> None:
    session = AgentSession(
        session_id="s-1",
        run_id="r-1",
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=_SESSION_IDENTITY,
    )

    payload = json.loads(session_payload_json(session))

    assert "delegated_agent_id" not in payload
    assert "delegated_model_identity" not in payload
    assert "delegated_capability_profile" not in payload
    assert payload["model_identity"]["transport"] == "codex"
