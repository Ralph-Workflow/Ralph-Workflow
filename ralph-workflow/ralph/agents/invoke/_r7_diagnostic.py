
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class R7AbsentLayoutDiagnostic:

    code: str
    claude_code_version: str | None
    project_key: str
    session_id: str
    probed_path: str
    dispatch_tool_use_id: str
    dispatch_tool_name: str


__all__ = ["R7AbsentLayoutDiagnostic"]
