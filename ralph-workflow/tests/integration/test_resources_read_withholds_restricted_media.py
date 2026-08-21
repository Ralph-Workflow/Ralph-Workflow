"""``resources/read`` must not serve image bytes the tool surface withheld.

A transport whose CLI cannot carry an inline image back into its own API
request is denied one on the tool surface -- sending it kills the turn.
The resource surface reads the same artifact registry, and Ralph Workflow
actively points agents at it ("Retrieve via resources/read with the full
URI"), so leaving it ungated reopened the same failure by a side door and
made the tool-side explanation ("cannot accept the bytes by any route")
untrue.

Non-image modalities are unaffected: the guard is about the image block
specifically, not about the resource surface in general.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.server.runtime import JsonRpcRequest, McpServer, ServerState
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str
from tests.integration._multimodal_e2e_fixtures import (
    TINY_PDF_BYTES,
    build_multimodal_harness,
    install_media_backend,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.timeout_seconds(10)

#: The shape a flagless ``ralph run`` produces for a Codex agent: the
#: provider is genuinely unresolved, and only the transport is known.
_CODEX_IDENTITY = MultimodalModelIdentity(provider="unknown", model_id=None, transport="codex")


def _rpc(
    server: McpServer, state: ServerState, method: str, params: dict[str, object], msg_id: int
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None, ServerState]:
    response, next_state = server.handle_request(
        JsonRpcRequest(jsonrpc="2.0", method=method, params=params, msg_id=msg_id),
        state,
    )
    return response.result, response.error, next_state


def _read_media_uri(server: McpServer, state: ServerState, path: str, msg_id: int) -> str:
    result, error, _ = _rpc(
        server, state, "tools/call", {"name": "read_media", "arguments": {"path": path}}, msg_id
    )
    assert error is None, error
    assert result is not None
    for block in must_dict_list(result["content"]):
        mapping = must_mapping(block)
        if mapping.get("type") == "resource_reference":
            return must_str(mapping["uri"])
    raise AssertionError(f"no resource reference in {result['content']}")


def test_restricted_transport_cannot_read_withheld_image_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The side door is closed for exactly the artifact that was withheld."""
    server, _workspace, backend, session = build_multimodal_harness()
    install_media_backend(monkeypatch, backend)
    session.model_identity = _CODEX_IDENTITY
    session.stored_capability_profile = None
    state = ServerState.RUNNING

    uri = _read_media_uri(server, state, "screenshot.png", 1)
    result, error, _ = _rpc(server, state, "resources/read", {"uri": uri}, 2)

    assert result is None
    assert error is not None
    assert "cannot carry an inline image" in must_str(error["message"])


def test_restricted_transport_can_still_read_a_pdf_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: the guard is about images, not the resource surface."""
    server, _workspace, backend, session = build_multimodal_harness()
    install_media_backend(monkeypatch, backend)
    session.model_identity = _CODEX_IDENTITY
    session.stored_capability_profile = None
    state = ServerState.RUNNING

    uri = _read_media_uri(server, state, "report.pdf", 1)
    result, error, _ = _rpc(server, state, "resources/read", {"uri": uri}, 2)

    assert error is None, error
    assert result is not None
    block = must_mapping(must_dict_list(result["contents"])[0])
    assert base64.b64decode(must_str(block["blob"])) == TINY_PDF_BYTES


def test_a_profile_that_knows_the_cli_closes_the_side_door_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two surfaces must gate on ONE identity, not two.

    A session can know its CLI through its stored capability profile
    while its own identity does not: ``profile_for_caller`` adopts the
    profile's transport when the identity has none. That is not exotic
    -- ``session_payload_json`` writes ``capability_profile`` with its
    transport but OMITS ``model_identity`` entirely when the identity is
    unresolvable, so a child reads back exactly this split.

    The tool surface reads the profile's identity and withheld the
    image; this side door read the session's and served the bytes,
    making the tool's own explanation ("cannot accept the bytes by any
    route") false.
    """
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile

    server, _workspace, backend, session = build_multimodal_harness()
    install_media_backend(monkeypatch, backend)
    # The identity itself knows nothing; only the stored profile does.
    session.model_identity = MultimodalModelIdentity(
        provider="unknown", model_id=None, transport=None
    )
    session.stored_capability_profile = resolve_capability_profile(_CODEX_IDENTITY)
    state = ServerState.RUNNING

    # Not vacuous: the split the test is about is really present.
    assert session.caller_model_identity.transport is None
    assert session.caller_capability_profile.identity.transport == "codex"

    uri = _read_media_uri(server, state, "screenshot.png", 1)
    result, error, _ = _rpc(server, state, "resources/read", {"uri": uri}, 2)

    assert result is None
    assert error is not None
    assert "cannot carry an inline image" in must_str(error["message"])
