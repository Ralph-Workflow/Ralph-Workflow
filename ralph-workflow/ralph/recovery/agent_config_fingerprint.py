from __future__ import annotations

import hashlib
from pathlib import Path


def agent_config_fingerprint(
    agent: str,
    *,
    cmd: str,
    model: str | None,
    model_flag: str | None,
    json_parser: str | None,
    transport: str | None,
) -> str:
    first_token = cmd.split(maxsplit=1)[0] if cmd.split(maxsplit=1) else ""
    values = (
        agent,
        Path(first_token).name,
        model or "",
        model_flag or "",
        json_parser or "",
        transport or "",
    )
    return hashlib.sha256("\0".join(values).encode()).hexdigest()[:16]


__all__ = ["agent_config_fingerprint"]
