"""Every JSON reader turns an absent provider into the SAME absence.

``str(raw.get("provider", "unknown"))`` is the wrong coercion for a
field that arrives from JSON: it turns ``{"provider": null}`` into the
literal provider ``'none'``, which ``is_known()`` reads as RESOLVED. The
identity-unknown degradation warning is suppressed for an identity that
has no provider at all, and ``'none'`` is what the wire ledger records
as the provider served.

Three readers coerced that way. One was fixed in place and the other two
kept minting ``'none'`` from the same bytes, so this module drives all
three from one payload -- a fix at a single reader fails here.

The rehydrated VERDICTS are checked against the same rule. They are
built beside a canonicalised identity from the raw payload again, so a
padded ``'  CLAUDE  '`` reached the agent in the sentence explaining why
its media was degraded while the identity next to it read ``'claude'``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    profile_from_payload,
)
from ralph.mcp.server.runtime_session import (
    FileBackedSession,
    session_identity_from_payload,
)

#: Every spelling of "this payload names no provider" that JSON allows.
_ABSENT_PROVIDERS: tuple[object, ...] = (None, "", "   ", 7, {}, [])


def _session_with(payload: dict[str, object], tmp_path: Path) -> FileBackedSession:
    document: dict[str, object] = {"session_id": "s1"}
    document.update(payload)
    path = tmp_path / "session.json"
    path.write_text(json.dumps(document))
    return FileBackedSession(path)


def test_an_absent_provider_is_unknown_at_every_reader(tmp_path: Path) -> None:
    """Session identity, delegated identity and stored profile agree."""
    for index, raw_provider in enumerate(_ABSENT_PROVIDERS):
        (tmp_path / str(index)).mkdir(parents=True, exist_ok=True)
        session = _session_with(
            {
                "model_identity": {"provider": raw_provider, "model_id": "m"},
                "delegated_model_identity": {"provider": raw_provider, "model_id": "m"},
                "capability_profile": {"provider": raw_provider, "model_id": "m"},
            },
            tmp_path / str(index),
        )
        delegated = session.delegated_model_identity
        assert delegated is not None
        stored = session.stored_capability_profile
        assert stored is not None
        for label, identity in (
            ("model_identity", session.model_identity),
            ("delegated_model_identity", delegated),
            ("capability_profile", stored.identity),
            ("caller", session.caller_model_identity),
        ):
            assert not identity.is_known(), f"{label} accepted {raw_provider!r}"
            assert identity.provider == "unknown", f"{label} minted {identity.provider!r}"


def test_the_session_identity_seam_agrees_with_the_others() -> None:
    """The seam a declaration goes through reads the field the same way."""
    for raw_provider in _ABSENT_PROVIDERS:
        identity = session_identity_from_payload(
            {"provider": raw_provider, "model_id": "m"}, None
        )
        assert identity.provider == "unknown"
        assert not identity.is_known()


def test_a_rehydrated_verdict_quotes_the_canonical_identity() -> None:
    """The verdict beside an identity names the same provider it does.

    The verdict's ``provider`` and ``model_id`` are what the degradation
    warning and the ``UNSUPPORTED`` error quote back to the agent, so
    reading the payload twice put two spellings of one model in front of
    it.
    """
    profile = profile_from_payload(
        {
            "provider": "  CLAUDE  ",
            "model_id": "  opus  ",
            "verdicts": {
                "audio": {"delivery": "unsupported", "reason": "stale reason"}
            },
        }
    )
    assert profile.identity == MultimodalModelIdentity(provider="claude", model_id="opus")
    for modality, verdict in profile.verdicts.items():
        assert verdict.provider == profile.identity.provider, modality
        assert verdict.model_id == profile.identity.model_id, modality


def test_a_rehydrated_verdict_never_carries_a_provider_the_identity_dropped() -> None:
    """An absent provider does not survive into the stored verdicts."""
    for raw_provider in _ABSENT_PROVIDERS:
        profile = profile_from_payload(
            {
                "provider": raw_provider,
                "verdicts": {"pdf": {"delivery": "typed_block", "reason": "r"}},
            }
        )
        assert not profile.identity.is_known()
        assert profile.verdicts["pdf"].provider == "unknown"
