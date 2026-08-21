"""Reading a persisted session payload into typed capability values.

Split out of :mod:`ralph.mcp.multimodal.capabilities`, which owns the
provider matrix and the delivery rules. This module owns the boundary
BEFORE them: turning whatever a JSON file happens to contain into values
the rules can be stated about.

Every function here exists because a reader coerced with ``str(...)``
and something that was not a name arrived where a name was expected --
a ``null`` provider read as the literal ``'none'`` and suppressed a
degradation warning, a JSON object arrived as a model id spelled after
Python's ``repr``. They are the single definition of each field's rule,
so a fourth reader cannot invent a fifth interpretation.
"""

from __future__ import annotations

from ralph.mcp.multimodal._delivery_mode import DeliveryMode
from ralph.mcp.multimodal._multimodal_model_identity import MultimodalModelIdentity

#: The spelling an identity gives an unresolved provider, DERIVED from
#: the dataclass's own rule rather than restated. ``capabilities``
#: exports the same value as ``UNKNOWN_IDENTITY``; importing it here
#: would invert this module's place in the import order, and writing
#: ``"unknown"`` again would be a fourth copy of a string three seams
#: already agree on.
_UNKNOWN_PROVIDER = MultimodalModelIdentity(provider="").provider


def payload_transport(raw: object) -> str | None:
    """Return a usable transport from an untrusted payload field, or ``None``.

    An empty or whitespace-only value is NOT a transport. Treating it as
    one made ``"transport": ""`` outrank an operator's
    ``--agent-transport`` declaration and left the guards blind.

    Lowercased as well as stripped, so every seam that writes a transport
    spelling agrees: each matcher strips and lowercases before comparing,
    so this changes no guard -- it makes the session file and the
    wire-ledger capability digest agree with themselves.

    THE definition, re-exported by
    :mod:`ralph.mcp.server.runtime_session` for its historical callers.
    Two copies of this rule existed, one per package, and the packages
    they served hand identities to each other.
    """
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def payload_provider(raw: object) -> str:
    """Return a provider name from an untrusted payload field.

    ``str(...)`` is the wrong coercion for a JSON field: it turns a
    payload ``{"provider": null}`` into the literal provider ``'none'``,
    which :meth:`MultimodalModelIdentity.is_known` reads as RESOLVED --
    so the identity-unknown degradation warning is suppressed for an
    identity that has no provider at all, and ``'none'`` is what the
    wire ledger records as the provider served. ``null`` is the natural
    serialisation of "no provider".

    THREE readers coerced this way. Fixing the one in front of me left
    the other two minting ``'none'`` from the same payload, so the rule
    lives here and every reader goes through it.
    """
    return raw if isinstance(raw, str) else _UNKNOWN_PROVIDER


def payload_model_id(raw: object) -> str | None:
    """Return a model id from an untrusted payload field, or ``None``.

    A non-string is not a model id -- the same rule
    :func:`payload_transport` applies to its own field. The readers
    coerced with ``str(...)``, so a JSON number or object arrived as a
    model id spelled after Python's ``repr``, and that spelling travels
    into every verdict ``reason`` shown to the agent.
    """
    return raw if isinstance(raw, str) else None


def payload_delivery(raw: object) -> DeliveryMode:
    """Return a delivery mode from an untrusted payload field.

    Anything unrecognised -- a misspelling, a mode from a future
    version, a non-string -- becomes a replay handle rather than an
    error: an unreadable stored verdict must not take the artifact away,
    and it must not be coerced with ``str()`` into a mode either.
    """
    if not isinstance(raw, str):
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY
    try:
        return DeliveryMode(raw)
    except ValueError:
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY


def payload_identity(
    provider: object, model_id: object, transport: object
) -> MultimodalModelIdentity:
    """Build an identity from UNTRUSTED payload fields.

    THE one place the three field seams are composed, so a reader
    cannot pick two of them and miss the third.

    NOTE, deliberately not fixed here: an identity naming a provider and
    NO transport keeps its provider, and therefore keeps typed delivery
    for whatever that provider accepts. An audit reported this as the
    last route into typed delivery through a CLI Ralph cannot name, and
    it is real -- deleting ``"transport"`` from a payload turns a
    bounded ``kimi`` session back into an ``AudioContent``.

    Dropping the provider closes it and costs more than it buys.
    ``cli/commands/_smoke_ccs.py`` injects
    ``MultimodalModelIdentity(provider="ccs")`` with no transport, that
    identity crosses the subprocess boundary as JSON, and on the far
    side it is indistinguishable from a hand-written one -- so the rule
    would strip Ralph's own identity and disable the ``ccs`` text-handle
    path in every child. And it buys little while
    :data:`VENDOR_ROUTING_TRANSPORTS` deliberately leaves ``opencode``
    unbounded: a payload wanting typed delivery can simply name that
    transport instead. The honest fix is for the injecting call site to
    record the CLI it is launching, which would make the round trip
    lossless and this rule safe; until then the exposure is stated
    rather than half-closed.
    """
    return MultimodalModelIdentity(
        provider=payload_provider(provider),
        model_id=payload_model_id(model_id),
        transport=payload_transport(transport),
    )


__all__ = [
    "payload_delivery",
    "payload_identity",
    "payload_model_id",
    "payload_provider",
    "payload_transport",
]
