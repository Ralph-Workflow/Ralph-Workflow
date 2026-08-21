"""Delegated identity is in-process state and does not cross the wire.

``AgentSession`` and ``FileBackedSession`` both resolve a delegated call
against ``caller_identity_for``, so a delegate cannot declare the
session's transport away. That machinery has no producer: nothing in
production sets the delegated fields, and ``session_payload_json`` does
not serialise them.

That is a defensible position -- there is no wire format for a feature
with no producer -- but it is a trap if it is only implied. This module
states it out loud, so that wiring delegation later fails HERE, next to
the explanation, rather than silently judging the parent instead of the
delegate on the far side of a subprocess boundary.
"""

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
    """The session's CLI wins; a delegate only names a different model."""
    resolved = caller_identity_for(_SESSION_IDENTITY, _DELEGATE)

    assert resolved.transport == "codex"
    # Its provider went with the transport it no longer carries: keeping
    # it would resolve claude's capabilities for a codex process.
    assert resolved.provider != "claude"


def test_a_session_without_a_delegate_is_its_own_caller() -> None:
    """The common case, which is every case in production today."""
    assert caller_identity_for(_SESSION_IDENTITY, None) is _SESSION_IDENTITY


def test_the_handshake_payload_carries_no_delegated_keys() -> None:
    """Pins the boundary named in ``session_payload_json``'s docstring.

    If delegation is ever wired, this test fails and points at the two
    places that must learn to carry it.
    """
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
    # The session's OWN identity does cross, transport included.
    assert payload["model_identity"]["transport"] == "codex"
