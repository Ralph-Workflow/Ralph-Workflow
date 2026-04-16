from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ralph.mcp.server import lifecycle
from ralph.mcp.session import AgentSession

if TYPE_CHECKING:
    from pathlib import Path

PREFLIGHT_TIMEOUT = 123


class FakeProcess:
    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        return None


def test_start_mcp_server_uses_injected_dependencies(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_reserve_port() -> int:
        seen["reserved"] = True
        return 43123

    def fake_create_session_file(root: Path, session: object) -> Path:
        seen["session_root"] = root
        seen["session"] = session
        path = tmp_path / "session.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        seen["session_file"] = session_file
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(command: list[str], cwd: Path, env: dict[str, str]):
        seen["command"] = command
        seen["cwd"] = cwd
        seen["env"] = env
        return FakeProcess()

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        seen["endpoint"] = endpoint
        seen["required_tools"] = required_tools
        seen["timeout"] = timeout

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=PREFLIGHT_TIMEOUT),
    )

    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:43123/mcp"
    assert seen["session_root"] == tmp_path
    assert seen["endpoint"] == "http://127.0.0.1:43123/mcp"
    assert seen["timeout"] == timedelta(seconds=PREFLIGHT_TIMEOUT)
    assert seen["cwd"] == tmp_path
