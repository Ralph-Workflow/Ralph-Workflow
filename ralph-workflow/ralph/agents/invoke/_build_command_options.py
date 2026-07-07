from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _BuildCommandOptions:
    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    pure: bool = False
    mcp_endpoint: str | None = None
    allowed_mcp_tool_names: tuple[str, ...] = ()
    unsafe_mode: bool = False
    system_prompt_file: str | None = None
    workspace_path: Path | None = None
    initial_session_id: str | None = None
    settings_json: str | None = None
    stop_sentinel_path: Path | None = None
    pi_mcp_extension_path: str | None = None
