"""What a SESSION PAYLOAD may say about the CLI on the other end.

A payload is persisted state: it can be stale, hand-written, or left by
a previous run. These tests pin what a session built from one is allowed
to claim -- specifically, when the provider it names may be carried onto
the CLI the session ends up tagged with, and when it must not be.

Split out of ``test_codex_transport_reaches_the_session`` when that file
outgrew the repo's size limit; the two ask different questions of the
same machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_a_stale_payload_provider_does_not_ride_onto_another_cli(tmp_path: Path) -> None:
    """A payload's provider described the CLI the PAYLOAD named.

    When the resolved transport is not the one the payload stated, that
    provider is stale and must be dropped -- which is exactly what
    ``identity_on_transport`` is for. The session skipped it and paired
    the payload's provider with whichever transport won, so a declared
    ``claude`` server reading a stale ``{"provider": "gemini",
    "transport": "agy"}`` file resolved GEMINI's capabilities and minted
    an AudioContent, a modality Ralph's own matrix says the claude CLI
    cannot take.
    """
    import json

    from ralph.mcp.server.runtime_session import FileBackedSession

    def identity_for(declared: str | None, stated: dict[str, str] | None) -> object:
        payload: dict[str, object] = {"session_id": "s", "run_id": "r", "drain": "development"}
        if stated is not None:
            payload["model_identity"] = stated
        session = FileBackedSession(
            tmp_path / "session.json",
            loader=lambda _path: json.loads(json.dumps(payload)),
            declared_agent_transport=declared,
        )
        return session.model_identity

    stale_gemini = {"provider": "gemini", "model_id": "g", "transport": "agy"}
    rebased = identity_for("claude", stale_gemini)

    assert rebased.transport == "claude", "the declaration names the process actually running"
    assert rebased.provider == "unknown", "a provider for another CLI must not travel"

    # A restricted side still wins outright, and drops the provider too.
    restricted = identity_for("codex", {"provider": "claude", "model_id": "c", "transport": "claude"})
    assert restricted.transport == "codex"
    assert restricted.provider == "unknown"

    # Agreement keeps everything: nothing is stale here.
    agreeing = identity_for("claude", {"provider": "claude", "model_id": "c", "transport": "claude"})
    assert agreeing.provider == "claude"
    assert agreeing.model_id == "c"

    # With no declaration the payload stands -- it is all there is.
    payload_only = identity_for(None, {"provider": "claude", "model_id": "c", "transport": "claude"})
    assert payload_only.provider == "claude"


def test_a_payload_that_names_no_cli_cannot_vouch_for_its_provider(tmp_path: Path) -> None:
    """A provider describes the CLI its own payload named.

    When the payload names NO transport at all -- key absent, empty,
    whitespace, or not a string -- it cannot vouch for the CLI the
    session ends up tagged with, so its provider must not be carried
    there. ``identity_on_transport`` alone does not cover this: it
    preserves the provider for a blank stated transport DELIBERATELY,
    so a delegate naming a different model on the same CLI keeps its
    provider. Right for a delegate, wrong for a session payload.

    The previous round pinned only the shape where the payload states a
    DISAGREEING transport. The shape that states nothing kept the
    provider and minted an AudioContent for a declared claude session --
    a modality Ralph's own matrix says that CLI cannot take.
    """
    import json

    from ralph.mcp.server.runtime_session import FileBackedSession

    def provider_for(stated: object) -> str:
        identity: dict[str, object] = {"provider": "gemini"}
        if stated is not _ABSENT:
            identity["transport"] = stated
        payload = {
            "session_id": "s",
            "run_id": "r",
            "drain": "development",
            "model_identity": identity,
        }
        session = FileBackedSession(
            tmp_path / "session.json",
            loader=lambda _path: json.loads(json.dumps(payload)),
            declared_agent_transport="claude",
        )
        return session.model_identity.provider

    for unstated in (_ABSENT, "", "   ", 5, None):
        assert provider_for(unstated) == "unknown", unstated

    # A payload that DOES name the declared CLI still vouches for itself.
    assert provider_for("claude") == "gemini"


#: Sentinel for "the key is not in the payload at all".
_ABSENT = object()
