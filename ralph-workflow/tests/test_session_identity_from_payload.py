from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_a_stale_payload_provider_does_not_ride_onto_another_cli(tmp_path: Path) -> None:
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

    restricted = identity_for(
        "codex", {"provider": "claude", "model_id": "c", "transport": "claude"}
    )
    assert restricted.transport == "codex"
    assert restricted.provider == "unknown"

    agreeing = identity_for(
        "claude", {"provider": "claude", "model_id": "c", "transport": "claude"}
    )
    assert agreeing.provider == "claude"
    assert agreeing.model_id == "c"

    payload_only = identity_for(
        None, {"provider": "claude", "model_id": "c", "transport": "claude"}
    )
    assert payload_only.provider == "claude"


def test_a_payload_that_names_no_cli_cannot_vouch_for_its_provider(tmp_path: Path) -> None:
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

    assert provider_for("claude") == "gemini"


_ABSENT = object()
