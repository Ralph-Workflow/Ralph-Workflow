from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.pipeline.session_bridge import bridge_env_for
from ralph.policy.models import AgentsPolicy

if TYPE_CHECKING:
    import types

    from _pytest.monkeypatch import MonkeyPatch


def _plumbing_module() -> types.ModuleType:
    importlib.import_module("ralph.cli.commands.commit")
    return importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")


class _StubBridge:
    def __init__(self, endpoint: str, run_id: str) -> None:
        self._endpoint = endpoint
        self.run_id = run_id

    def start(self) -> None:
        return None

    def agent_endpoint_uri(self) -> str:
        return self._endpoint

    def endpoint_uri(self) -> str:
        return self._endpoint

    def shutdown(self) -> None:
        return None


def test_commit_bridge_runs_session_with_commit_run_id(
    monkeypatch: MonkeyPatch,
) -> None:
    plumbing_module = _plumbing_module()
    calls: list[tuple[str, str | None]] = []

    def _fake_mint_and_protect_broker_secret() -> str:
        calls.append(("protect", None))
        return "fresh-secret"

    def _fake_build_session_bridge(
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        session_id_prefix: str | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> _StubBridge:
        calls.append(("build", run_id))
        return _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id=run_id or "")

    monkeypatch.setattr(
        plumbing_module,
        "mint_and_protect_broker_secret",
        _fake_mint_and_protect_broker_secret,
    )
    monkeypatch.setattr(
        plumbing_module,
        "build_session_bridge",
        _fake_build_session_bridge,
    )

    bridge = plumbing_module._start_commit_bridge(
        repo_root=Path("/tmp"),
        agents_policy=AgentsPolicy(),
        model_identity=None,
    )

    expected_run_id = plumbing_module._COMMIT_RUN_ID
    assert calls == [("protect", None), ("build", expected_run_id)]
    assert bridge.run_id == expected_run_id


def test_commit_bridge_env_matches_session_run_id() -> None:
    plumbing_module = _plumbing_module()
    expected_run_id = plumbing_module._COMMIT_RUN_ID

    bridge = _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id=expected_run_id)

    env = bridge_env_for(bridge)

    assert env[str(MCP_RUN_ID_ENV)] == bridge.run_id
    assert env[str(MCP_RUN_ID_ENV)] == expected_run_id


def test_bridge_env_for_cannot_drift_from_bridge_run_id() -> None:
    bridge = _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id="commit-plumbing")

    first = bridge_env_for(bridge)[str(MCP_RUN_ID_ENV)]
    second = bridge_env_for(bridge)[str(MCP_RUN_ID_ENV)]
    assert first == second == "commit-plumbing"
