"""The identity-normalisation and profile-re-basing rules.

Every delivery guard keys on an identity, so these two rules decide what
the guards see. Both have been wrong at least once in a way the suite
did not notice -- a blank transport treated as a real CLI, a foreign
provider's verdicts carried forward -- so they are pinned here directly
rather than only through the paths that consume them.
"""

from __future__ import annotations

from ralph.mcp.multimodal.capabilities import DeliveryMode


def test_a_whitespace_transport_is_not_a_transport() -> None:
    """Blank is not a CLI name, on either side of the comparison.

    Treating one as authoritative let a blank declaration override a
    genuine restricted transport and then strip to nothing.
    """
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
        inline_image_roundtrip_unsafe,
    )

    codex = MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="codex")

    for blank in ("", "   ", "\t"):
        assert inline_image_roundtrip_unsafe(identity_on_transport(codex, blank)), blank


def test_a_transport_differing_only_in_case_keeps_its_provider() -> None:
    """Case is not a different CLI; dropping the provider there costs the ledger."""
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
    """The less-informed guard must see blank as "knows nothing", like None."""
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
    """Another provider's verdicts describe another API."""
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        profile_for_caller,
        resolve_capability_profile,
    )

    gemini = resolve_capability_profile(
        MultimodalModelIdentity(provider="gemini", model_id="g", transport="claude")
    )
    assert gemini.verdict_for("audio").delivery is DeliveryMode.TYPED_BLOCK

    rebased = profile_for_caller(
        gemini,
        MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="claude"),
    )

    # OpenAI accepts none of these; carrying gemini's verdicts promised
    # typed blocks its API would reject.
    assert rebased.verdict_for("audio").delivery is DeliveryMode.UNSUPPORTED
    assert rebased.verdict_for(MODALITY_PDF).delivery is DeliveryMode.UNSUPPORTED


def test_an_empty_payload_transport_does_not_outrank_the_declaration() -> None:
    """``"transport": ""`` is absence, not a CLI the payload named."""
    from ralph.mcp.server.runtime_session import payload_transport

    assert payload_transport("") is None
    assert payload_transport("   ") is None
    assert payload_transport(None) is None
    assert payload_transport(7) is None
    assert payload_transport(" codex ") == "codex"
