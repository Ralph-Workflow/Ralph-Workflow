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

    # The IDENTITY must be re-resolved, not just the deliveries. Asserting
    # only on delivery let the re-resolution guard be deleted with this
    # test still green: ``verdict_for`` independently corrects gemini's
    # perceptible verdicts down for an openai identity, so both asserts
    # above survive a profile that never re-resolved at all. These are
    # the fields this layer prints and the wire ledger records, and a
    # verdict still claiming ``provider='gemini'`` is an audit trail
    # naming the wrong API.
    assert rebased.identity.provider == "openai"
    assert rebased.identity.model_id == "gpt-5"

    # ``image`` is the modality where the two providers AGREE, so no
    # per-verdict correction fires and the stored verdict is returned
    # verbatim. That makes it the only place the re-resolution itself is
    # observable: a profile that merely carried the new identity over
    # gemini's verdicts still reports ``provider='gemini'`` here, in the
    # text this layer prints and the wire ledger records.
    image_verdict = rebased.verdict_for("image")
    assert image_verdict.provider == "openai"
    assert "gemini" not in image_verdict.reason


def test_an_empty_payload_transport_does_not_outrank_the_declaration() -> None:
    """``"transport": ""`` is absence, not a CLI the payload named."""
    from ralph.mcp.server.runtime_session import payload_transport

    assert payload_transport("") is None
    assert payload_transport("   ") is None
    assert payload_transport(None) is None
    assert payload_transport(7) is None
    assert payload_transport(" codex ") == "codex"


def test_a_re_based_identity_carries_the_canonical_transport_spelling() -> None:
    """The transport written into an identity is canonicalised, not copied.

    Every consumer strips and lowercases before matching, so a
    difference in spelling changed no behaviour and no test -- but the
    spelling IS what lands in the session file and the wire-ledger
    capability digest, and the other seam that re-bases a transport
    lowercases. Two seams normalising differently made that digest
    depend on which one ran, and left this function returning
    ``transport='CODEX'`` while claiming to have re-based the identity
    onto the launched ``codex``.
    """
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )

    shouted = MultimodalModelIdentity(provider="openai", model_id="m", transport="  CODEX ")

    assert identity_on_transport(shouted, "codex").transport == "codex"
    assert identity_on_transport(shouted, None).transport == "codex"
    assert identity_on_transport(shouted, "  Codex  ").transport == "codex"


def test_a_blank_stated_transport_takes_the_launched_one() -> None:
    """Whitespace is not a transport, in either position.

    Treating a whitespace-only value as authoritative let
    ``--agent-transport "  "`` override a delegate's genuine restricted
    CLI and then strip to nothing, reopening the incident at the one
    seam the payload readers' normalisation did not cover.
    """
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        identity_on_transport,
    )

    blank = MultimodalModelIdentity(provider="openai", model_id="m", transport="   ")
    stated = MultimodalModelIdentity(provider="openai", model_id="m", transport="codex")

    assert identity_on_transport(blank, "codex").transport == "codex"
    # A blank LAUNCHED value must not clear a real stated transport.
    assert identity_on_transport(stated, "   ").transport == "codex"


def test_an_identity_nobody_can_place_is_returned_untouched() -> None:
    """``None`` transport means unknown, and must not become ``""``.

    Canonicalising unconditionally rewrote an unknown transport to the
    empty string, which reads as "known to be nothing" and flips
    ``lifecycle.identity_is_serialisable`` (it tests ``is not None``) --
    so an identity nothing knew anything about started being written
    into the session file and the wire-ledger digest.
    """
    from ralph.mcp.multimodal.capabilities import (
        UNKNOWN_IDENTITY,
        identity_on_transport,
    )
    from ralph.mcp.server.lifecycle import identity_is_serialisable

    rebased = identity_on_transport(UNKNOWN_IDENTITY, None)

    assert rebased.transport is None
    assert identity_is_serialisable(rebased) is False


def test_the_standalone_seam_canonicalises_like_the_others() -> None:
    """Three seams write a transport spelling; all three must agree.

    The guards strip and lower before matching, so a shouted value still
    works -- but the spelling reaches the session file and the
    wire-ledger capability digest, and two spellings of one CLI produced
    two different digests for the same run.
    """
    from ralph.mcp.server.runtime import standalone_session_identity

    assert standalone_session_identity("CODEX").transport == "codex"
    assert standalone_session_identity("  Codex  ").transport == "codex"
    assert standalone_session_identity("codex").transport == "codex"
    assert standalone_session_identity("   ").transport is None
