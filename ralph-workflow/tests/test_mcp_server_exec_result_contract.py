from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools.exec as exec_tool
import ralph.mcp.tools.unsafe_exec as unsafe_exec_tool
from ralph.mcp.server.runtime import build_fastmcp_server

if TYPE_CHECKING:
    from pathlib import Path


def _fake_completed_process(stdout_text: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args="",
        returncode=0,
        stdout=stdout_text.encode("utf-8"),
        stderr=b"",
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_fragment"),
    [
        ("exec", {"command": "python", "args": ["-c", "print('bounded')"]}, "bounded"),
        ("unsafe_exec", {"command": "python -c \"print('unsafe')\""}, "unsafe"),
        ("raw_exec", {"command": "python -c \"print('raw')\""}, "raw"),
    ],
)
def test_fastmcp_exec_family_returns_inline_text_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
    expected_fragment: str,
) -> None:
    monkeypatch.setattr(
        exec_tool,
        "run_command",
        lambda *args, **kwargs: exec_tool._CompletedProcessAdapter(
            stdout=(expected_fragment + "\n").encode("utf-8"),
            stderr=b"",
            returncode=0,
        ),
    )
    monkeypatch.setattr(
        unsafe_exec_tool.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed_process(expected_fragment + "\n"),
    )

    server = build_fastmcp_server(tmp_path)

    result = asyncio.run(server._tool_manager.call_tool(tool_name, arguments))

    assert isinstance(result, dict)
    assert result["isError"] is False
    content = result["content"]
    assert isinstance(content, list)
    assert content
    first_block = content[0]
    assert isinstance(first_block, dict)
    assert first_block.get("type") == "text"
    text = first_block.get("text")
    assert isinstance(text, str)
    assert text.strip()
    assert expected_fragment in text
    assert "Exit code: 0" in text
