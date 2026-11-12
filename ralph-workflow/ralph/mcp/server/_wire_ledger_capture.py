from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WireLedgerCapture"]


@dataclass(frozen=True)
class WireLedgerCapture:
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
            delivery_mode=(delivery_mode_raw if isinstance(delivery_mode_raw, str) else None),
            provider=provider_raw if isinstance(provider_raw, str) else None,
            model_id=model_id_raw if isinstance(model_id_raw, str) else None,
            agent_id=agent_id_raw if isinstance(agent_id_raw, str) else None,
        )
