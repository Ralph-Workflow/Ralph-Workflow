"""Wire-ledger capture row (S-9 / F5).

The wire ledger is the durable, machine-verifiable capture record
for a real AGY run. :class:`WireLedgerCapture` is the per-row view
the fixture exporter consumes to regenerate
``tests/display/_fixtures/agy_wire_provenance.md``'s capture-method
table rather than hand-authoring it.

Lives in its own module so the repository's one-class-per-file
policy (enforced by ``ralph.testing.audit_repo_structure``) keeps
``_wire_ledger.py`` focused on the append / verify path.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WireLedgerCapture"]


@dataclass(frozen=True)
class WireLedgerCapture:
    """One wire-ledger row summarised as an AGY wire-provenance capture entry.

    Fields:
        method: the JSON-RPC method (``tools/call``, ``tools/list``,
            ``initialize``, etc).
        tool_name: the canonical tool name the row targeted, or
            ``None`` for non-tool methods.
        run_id: the run-scoped identifier the row belongs to.
        timestamp: the row's recorded timestamp (wall-clock seconds
            since epoch).
        hmac: the per-row HMAC, kept here for verifier traceability.
        delivery_mode: S-6 (criterion 17) -- the multimodal
            delivery mode the call resolved to, when known. ``None``
            for non-multimodal ``tools/call`` rows or for pre-S-6
            rows that don't carry the field.
        provider: S-6 (criterion 17) -- the model provider the call
            resolved to, when known. ``None`` when unknown or when
            the pre-S-6 row shape is in effect.
        model_id: S-6 (criterion 17) -- the model identifier the
            call resolved to, when known. ``None`` when unknown or
            when the pre-S-6 row shape is in effect.
        agent_id: S-6 (criterion 17) -- the agent identifier the
            call was dispatched on behalf of, when known. ``None``
            when unknown or when the pre-S-6 row shape is in
            effect.
    """

    method: str
    tool_name: str | None
    run_id: str
    timestamp: float
    hmac: str
    delivery_mode: str | None = None
    provider: str | None = None
    model_id: str | None = None
    agent_id: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, object]) -> WireLedgerCapture | None:
        """Build a ``WireLedgerCapture`` from a raw ledger row.

        Returns ``None`` when the row is missing one of the required
        fields -- a malformed row that the chain verifier already
        rejects is not a valid capture. The four S-6 optional fields
        are read defensively (defaulting to ``None`` when the key is
        absent or has a non-string value), so a pre-S-6 row shape
        still produces a valid capture with the four new fields at
        their default ``None``.
        """
        method = row.get("method")
        run_id = row.get("run_id")
        timestamp = row.get("timestamp")
        hmac_value = row.get("hmac")
        tool_name = row.get("tool_name")
        if not (
            isinstance(method, str)
            and isinstance(run_id, str)
            and isinstance(timestamp, int | float)
            and isinstance(hmac_value, str)
            and (tool_name is None or isinstance(tool_name, str))
        ):
            return None
        delivery_mode_raw = row.get("delivery_mode")
        provider_raw = row.get("provider")
        model_id_raw = row.get("model_id")
        agent_id_raw = row.get("agent_id")
        return cls(
            method=method,
            tool_name=tool_name,
            run_id=run_id,
            timestamp=float(timestamp),
            hmac=hmac_value,
            delivery_mode=(
                delivery_mode_raw if isinstance(delivery_mode_raw, str) else None
            ),
            provider=provider_raw if isinstance(provider_raw, str) else None,
            model_id=model_id_raw if isinstance(model_id_raw, str) else None,
            agent_id=agent_id_raw if isinstance(agent_id_raw, str) else None,
        )
