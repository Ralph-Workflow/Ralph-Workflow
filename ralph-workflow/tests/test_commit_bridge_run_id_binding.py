"""Binding test: the commit bridge's run_id is the same value as its MCP_RUN_ID_ENV.

This is the structural binding the architect requires: a receipt stamped by
the commit MCP server can only be read back by the completion gate when the
two sides agree on the run identity. Pre-fix, ``_start_commit_bridge`` called
``build_session_bridge`` WITHOUT ``run_id`` (so the session got a random
``uuid4()``), and ``_commit_bridge_env`` then set ``MCP_RUN_ID_ENV`` to the
hard-coded label ``"commit-plumbing"`` — so the receipt was stamped under
``uuid4()`` and the gate looked it up under ``"commit-plumbing"`` and never
found it. That is exactly the artifact-handoff drift bug on the
``--generate-commit`` path.

The drift-proof contract now is: the env value MUST equal the bridge's
``run_id`` (the same property the session was constructed with and the same
value the submission handler stamps receipts against). This test binds
those three surfaces so they cannot drift.
"""

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
    """Resolve ``commit_plumbing`` lazily so the cycle resolves cleanly.

    ``commit_plumbing`` triggers ``ralph.cli.commands.commit`` at module
    load, and ``commit`` re-imports symbols from ``commit_plumbing``. The
    cycle resolves correctly only when ``commit`` finishes loading first;
    this helper imports ``commit`` first via ``importlib``, then resolves
    ``commit_plumbing`` after both modules are stable in ``sys.modules``.
    """
    importlib.import_module("ralph.cli.commands.commit")
    return importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")


class _StubBridge:
    """Minimal stand-in for a session bridge — typed end-to-end (no Mock)."""

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
    """``_start_commit_bridge`` MUST pass the commit run_id to build_session_bridge.

    Without it, the session is constructed with a random uuid, the receipt
    is stamped under that uuid, and the gate (which looks up the value
    ``bridge_env_for(bridge)`` puts in ``MCP_RUN_ID_ENV``) can never find
    the receipt.
    """
    # Import inside the test so module-load order is consistent with the
    # commit_plumbing module's own late-binding import of
    # ``ralph.cli.commands.commit``.
    plumbing_module = _plumbing_module()
    run_id_seen: list[str | None] = []

    def _fake_build_session_bridge(
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        session_id_prefix: str | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> _StubBridge:
        run_id_seen.append(run_id)
        return _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id=run_id or "")

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
    assert run_id_seen == [expected_run_id], (
        f"_start_commit_bridge must call build_session_bridge with run_id="
        f"{expected_run_id!r}; got {run_id_seen!r}"
    )
    assert bridge.run_id == expected_run_id


def test_commit_bridge_env_matches_session_run_id() -> None:
    """The env emitted for the commit bridge has MCP_RUN_ID_ENV == bridge.run_id.

    This is the one-line binding the receipt lookup depends on: the
    submission handler stamps receipts with ``session.run_id``, the gate
    looks them up with the value of ``MCP_RUN_ID_ENV``, and
    ``bridge_env_for`` is the single source for that value.
    """
    plumbing_module = _plumbing_module()
    expected_run_id = plumbing_module._COMMIT_RUN_ID

    bridge = _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id=expected_run_id)

    env = bridge_env_for(bridge)

    assert env[str(MCP_RUN_ID_ENV)] == bridge.run_id
    assert env[str(MCP_RUN_ID_ENV)] == expected_run_id


def test_bridge_env_for_cannot_drift_from_bridge_run_id() -> None:
    """Defence-in-depth: the env's run_id is a property read, not a stored value.

    ``bridge_env_for`` MUST read ``bridge.run_id`` at call time. A second
    read on the same bridge returns the same value because the property
    is the bridge's authoritative identity; this is the property that
    makes drift impossible.
    """
    bridge = _StubBridge(endpoint="http://127.0.0.1:65535/mcp", run_id="commit-plumbing")

    first = bridge_env_for(bridge)[str(MCP_RUN_ID_ENV)]
    second = bridge_env_for(bridge)[str(MCP_RUN_ID_ENV)]
    assert first == second == "commit-plumbing"
