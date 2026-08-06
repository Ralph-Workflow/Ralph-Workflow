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
    """

    method: str
    tool_name: str | None
    run_id: str
    timestamp: float
    hmac: str

    @classmethod
    def from_row(cls, row: dict[str, object]) -> WireLedgerCapture | None:
        """Build a ``WireLedgerCapture`` from a raw ledger row.

        Returns ``None`` when the row is missing one of the required
        fields -- a malformed row that the chain verifier already
        rejects is not a valid capture.
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
        return cls(
            method=method,
            tool_name=tool_name,
            run_id=run_id,
            timestamp=float(timestamp),
            hmac=hmac_value,
        )
