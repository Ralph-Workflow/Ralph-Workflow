from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.multimodal.capabilities import DeliveryMode

if TYPE_CHECKING:
    from pathlib import Path


def test_a_whitespace_transport_is_not_a_transport() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
        inline_image_roundtrip_unsafe,
    )

    codex = MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="codex")

    for blank in ("", "   ", "\t"):
        assert inline_image_roundtrip_unsafe(identity_on_transport(codex, blank)), blank


def test_a_transport_differing_only_in_case_keeps_its_provider() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )

    identity = MultimodalModelIdentity(
        provider="openai", model_id="gpt-5.6-terra", transport="CODEX"
    )

    result = identity_on_transport(identity, "codex")

    assert result.provider == "openai"
    assert result.model_id == "gpt-5.6-terra"


def test_a_blank_identity_transport_does_not_relax_a_restricted_profile() -> None:
    from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        profile_for_caller,
        resolve_capability_profile,
    )

    restricted = resolve_capability_profile(
        MultimodalModelIdentity(provider="unknown", model_id=None, transport="codex")
    )

    for blank in ("", "   "):
        rebased = profile_for_caller(
            restricted,
            MultimodalModelIdentity(provider="unknown", model_id=None, transport=blank),
        )
        assert rebased.verdict_for(MODALITY_IMAGE).delivery is (
            DeliveryMode.RESOURCE_REFERENCE_REPLAY
        ), blank


def test_a_profile_from_another_provider_is_re_resolved() -> None:
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        profile_for_caller,
        resolve_capability_profile,
    )

    gemini = resolve_capability_profile(
        MultimodalModelIdentity(provider="gemini", model_id="g", transport="agy")
    )
    assert gemini.verdict_for("audio").delivery is DeliveryMode.TYPED_BLOCK

    rebased = profile_for_caller(
        gemini,
        MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="agy"),
    )

    assert rebased.verdict_for("audio").delivery is DeliveryMode.UNSUPPORTED
    assert rebased.verdict_for(MODALITY_PDF).delivery is DeliveryMode.UNSUPPORTED

    assert rebased.identity.provider == "openai"
    assert rebased.identity.model_id == "gpt-5"

    image_verdict = rebased.verdict_for("image")
    assert image_verdict.provider == "openai"
    assert "gemini" not in image_verdict.reason


def test_an_empty_payload_transport_does_not_outrank_the_declaration() -> None:
    from ralph.mcp.multimodal.capabilities import payload_transport

    assert payload_transport("") is None
    assert payload_transport("   ") is None
    assert payload_transport(None) is None
    assert payload_transport(7) is None
    assert payload_transport(" codex ") == "codex"


def test_a_re_based_identity_carries_the_canonical_transport_spelling() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )

    shouted = MultimodalModelIdentity(provider="openai", model_id="m", transport="  CODEX ")

    assert identity_on_transport(shouted, "codex").transport == "codex"
    assert identity_on_transport(shouted, None).transport == "codex"
    assert identity_on_transport(shouted, "  Codex  ").transport == "codex"


def test_a_blank_stated_transport_takes_the_launched_one() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )

    blank = MultimodalModelIdentity(provider="openai", model_id="m", transport="   ")
    stated = MultimodalModelIdentity(provider="openai", model_id="m", transport="codex")

    assert identity_on_transport(blank, "codex").transport == "codex"
    assert identity_on_transport(stated, "   ").transport == "codex"


def test_an_identity_nobody_can_place_is_returned_untouched() -> None:
    from ralph.mcp.multimodal.capabilities import (
        UNKNOWN_IDENTITY,
        identity_on_transport,
    )
    from ralph.mcp.server.lifecycle import identity_is_serialisable

    rebased = identity_on_transport(UNKNOWN_IDENTITY, None)

    assert rebased.transport is None
    assert identity_is_serialisable(rebased) is False


def test_the_standalone_seam_canonicalises_like_the_others() -> None:
    from ralph.mcp.server.runtime import standalone_session_identity

    assert standalone_session_identity("CODEX").transport == "codex"
    assert standalone_session_identity("  Codex  ").transport == "codex"
    assert standalone_session_identity("codex").transport == "codex"
    assert standalone_session_identity("   ").transport is None


def test_every_seam_that_writes_a_transport_spelling_agrees() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
        payload_transport,
    )
    from ralph.mcp.server.runtime import standalone_session_identity

    for raw in ("codex", "CODEX", "  codex  ", "\tCodex\n"):
        seams = {
            "payload": payload_transport(raw),
            "standalone": standalone_session_identity(raw).transport,
            "rebased": identity_on_transport(
                MultimodalModelIdentity(provider="openai", model_id="m", transport=raw), None
            ).transport,
        }
        assert set(seams.values()) == {"codex"}, (raw, seams)

    for blank in ("", "   ", "\t\n"):
        assert payload_transport(blank) is None
        assert standalone_session_identity(blank).transport is None


def test_a_blank_transport_is_not_serialised_as_known() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )
    from ralph.mcp.server.lifecycle import identity_is_serialisable

    for blank in ("", "  "):
        rebased = identity_on_transport(
            MultimodalModelIdentity(provider="unknown", model_id=None, transport=blank), None
        )

        assert rebased.transport is None
        assert identity_is_serialisable(rebased) is False


def test_the_persisted_and_declared_seams_normalise_too() -> None:
    import json
    from pathlib import Path

    from ralph.mcp.multimodal.capabilities import profile_from_payload
    from ralph.mcp.server.runtime_session import FileBackedSession

    for raw in ("CODEX", "  codex  ", "codex"):
        profile = profile_from_payload({"provider": "openai", "model_id": "m", "transport": raw})
        assert profile.identity.transport == "codex", raw

        session = FileBackedSession(
            Path("/nonexistent/session.json"),
            loader=lambda _path: json.loads('{"session_id":"s","run_id":"r","drain":"d"}'),
            declared_agent_transport=raw,
        )
        assert session.model_identity.transport == "codex", raw


def test_a_blank_transport_is_normalised_at_the_reconciling_seam() -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import reconcile_injected_transport

    for blank in ("", "   "):
        reconciled = reconcile_injected_transport(
            MultimodalModelIdentity(provider="openai", model_id="m", transport=blank), None
        )

        assert reconciled.transport is None, blank


def test_the_payload_writer_canonicalises_the_transport_it_records() -> None:
    import json

    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import session_payload_json

    for spelling in ("CODEX", "  codex  ", "\tCoDeX\n", "codex"):
        session = AgentSession(
            session_id="s",
            run_id="r",
            drain="development",
            capabilities=frozenset({"media.read"}),
            model_identity=MultimodalModelIdentity(
                provider="openai", model_id="gpt-5", transport=spelling
            ),
        )

        payload = json.loads(session_payload_json(session))

        assert payload["model_identity"]["transport"] == "codex", spelling


def test_a_stale_payload_cannot_defeat_a_declared_restricted_cli(tmp_path: Path) -> None:
    import json

    from ralph.mcp.server.runtime_session import FileBackedSession, reconcile_declared_transport

    payload = {
        "session_id": "s",
        "run_id": "r",
        "drain": "development",
        "model_identity": {
            "provider": "claude",
            "model_id": "claude-opus-5",
            "transport": "claude",
        },
    }
    session = FileBackedSession(
        tmp_path / "session.json",
        loader=lambda _path: json.loads(json.dumps(payload)),
        declared_agent_transport="codex",
    )

    assert session.model_identity.transport == "codex"
    assert session.caller_model_identity.transport == "codex"

    assert reconcile_declared_transport("claude", "codex") == "codex"
    assert reconcile_declared_transport(None, "claude") == "claude"
    assert reconcile_declared_transport("codex", None) == "codex"
    assert reconcile_declared_transport("claude", "claude") == "claude"
    assert reconcile_declared_transport(None, None) is None


def test_the_capability_profile_beside_the_identity_is_canonicalised_too() -> None:
    import json

    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        resolve_capability_profile,
    )
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import session_payload_json

    digests = set()
    for spelling in ("codex", "CODEX", "  Codex  "):
        identity = MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport=spelling)
        session = AgentSession(
            session_id="s",
            run_id="r",
            drain="development",
            capabilities=frozenset({"media.read"}),
            model_identity=identity,
            stored_capability_profile=resolve_capability_profile(identity),
        )

        payload = json.loads(session_payload_json(session))

        assert payload["model_identity"]["transport"] == "codex", spelling
        assert payload["capability_profile"]["transport"] == "codex", spelling
        digests.add(json.dumps(payload["capability_profile"], sort_keys=True))

    assert len(digests) == 1, "one run must produce one capability digest"


def test_a_padded_provider_cannot_change_what_an_agent_is_served() -> None:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        get_delivery_mode,
    )

    canonical = get_delivery_mode(MultimodalModelIdentity(provider="claude"), "audio")
    for spelling in (" claude ", "CLAUDE", "\tClaude\n"):
        identity = MultimodalModelIdentity(provider=spelling)

        assert identity.provider == "claude", spelling
        assert get_delivery_mode(identity, "audio").delivery is canonical.delivery, spelling


def test_a_blank_provider_is_unknown_not_a_resolved_one() -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity

    for blank in ("", "   ", "Unknown", "unknown"):
        identity = MultimodalModelIdentity(provider=blank)

        assert identity.provider == "unknown", blank
        assert identity.is_known() is False, blank

    assert MultimodalModelIdentity(provider="claude").is_known() is True


def test_a_padded_model_id_does_not_change_the_digest() -> None:
    import json

    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        resolve_capability_profile,
    )

    digests = {
        json.dumps(
            resolve_capability_profile(
                MultimodalModelIdentity(provider="openai", model_id=spelling, transport="codex")
            ).to_payload(),
            sort_keys=True,
        )
        for spelling in ("gpt-5", "  gpt-5 ", "\tgpt-5\n")
    }

    assert len(digests) == 1, "padding changed the capability digest"

    assert MultimodalModelIdentity(provider="openai", model_id="GPT-5").model_id == "GPT-5"
    assert MultimodalModelIdentity(provider="openai", model_id="   ").model_id is None


def test_a_json_null_provider_reads_as_unknown() -> None:
    import json

    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.server.runtime_session import session_identity_from_payload

    for raw in (None, 0, [], {"a": 1}, True, ""):
        identity = session_identity_from_payload({"provider": raw}, None)

        assert identity.provider == "unknown", raw
        assert identity.is_known() is False, raw

    assert MultimodalModelIdentity(provider="claude").is_known() is True
    assert json.loads('{"provider": null}')["provider"] is None
