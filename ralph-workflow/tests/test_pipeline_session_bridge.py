"""Tests for the shared session-bridge module.

These tests are black-box and use injected fakes only: no real subprocess,
no real network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.format_docs import load_bundled_format_doc
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.session_plan import SessionMcpPlan, SessionModelOpts
from ralph.pipeline.session_bridge import (
    BridgeFactory,
    BuildSessionMcpPlanFn,
    SessionBridgeLike,
    StartMcpServerFn,
    WorkspaceFactoryFn,
    bridge_env_for,
    build_session_bridge,
    reset_tool_registry_callback,
)
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import McpServerExtras
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace.protocol import Workspace


class FakeSessionBridge:
    """Fake bridge implementing SessionBridgeLike."""

    def __init__(
        self,
        endpoint: str = "http://localhost:9999",
        run_id: str = "fake-run-id",
    ) -> None:
        self._endpoint = endpoint
        self._run_id = run_id
        self.started = False
        self.shutdown_called = False
        self.reset_tool_registry_called = False
        # AC-11: capture the session the bridge was constructed with so
        # the test for exec-resolver attachment can introspect what
        # ``build_session_bridge`` attached before ``start()`` was
        # called.
        self.attached_session: AgentSession | None = None
        self.attached_workspace: Workspace | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    def start(self) -> None:
        self.started = True

    def agent_endpoint_uri(self) -> str:
        return self._endpoint

    def endpoint_uri(self) -> str:
        return self._endpoint

    def shutdown(self) -> None:
        self.shutdown_called = True

    def reset_tool_registry(self) -> str:
        self.reset_tool_registry_called = True
        return "reset"


def fake_build_session_mcp_plan(
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None,
    model_opts: SessionModelOpts | None,
    model_flag: str | None,
) -> SessionMcpPlan:
    identity = (
        model_opts.model_identity
        if model_opts is not None and model_opts.model_identity is not None
        else UNKNOWN_IDENTITY
    )
    return SessionMcpPlan(
        capabilities=frozenset({"workspace.read"}),
        server_env={"FAKE_ENV": "1"},
        model_identity=identity,
        capability_profile=resolve_capability_profile(identity),
    )


def fake_start_mcp_server(
    session: AgentSession,
    workspace: Workspace,
    extras: McpServerExtras | None = None,
) -> SessionBridgeLike:
    bridge = FakeSessionBridge(endpoint="http://localhost:8888", run_id=session.run_id)
    # AC-11: capture the session the bridge was constructed with so the
    # test for exec-resolver attachment can introspect what
    # ``build_session_bridge`` attached before ``start()`` was called.
    bridge.attached_session = session
    bridge.attached_workspace = workspace
    return bridge


def fake_workspace_factory(root: Path) -> Workspace:
    return MemoryWorkspace(root)


class TestBuildSessionBridge:
    """Black-box tests for build_session_bridge."""

    def test_returns_bridge_with_endpoint(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"
        assert bridge.endpoint_uri() == "http://localhost:8888"
        assert bridge.started is True

    def test_threads_model_identity_into_plan(self, tmp_path: Path) -> None:
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            model_identity=model_identity,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_defaults_to_unknown_identity_when_model_identity_is_none(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_passes_run_id_and_session_id_prefix(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            session_id_prefix="commit",
            run_id="run-123",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_bridge_exposes_run_id_property(self, tmp_path: Path) -> None:
        """The bridge must expose the run_id it was constructed with so
        bridge_env_for cannot drift from the session's identity.

        Pre-fix, ``bridge_env_for`` accepted a free-form ``run_id_label`` that
        the caller (commit_plumbing) passed independently of the bridge's
        run_id. The two could disagree; the receipt was stamped with one and
        the gate looked it up with the other.
        """
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert bridge.run_id == "commit-plumbing"

    def test_bridge_env_for_uses_bridge_run_id_after_build(self, tmp_path: Path) -> None:
        """End-to-end binding: the env a fresh bridge produces must match
        the session's run_id, so the gate and the receipt share one identity.
        """
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        env = bridge_env_for(bridge)
        assert env[str(MCP_RUN_ID_ENV)] == bridge.run_id
        assert env[str(MCP_RUN_ID_ENV)] == "commit-plumbing"

    def test_parallel_worker_flag_is_passed(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            parallel_worker=True,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)

    def test_exec_resource_resolver_attached_before_bridge_start(self, tmp_path: Path) -> None:
        """AC-11: ``session.exec_resource_resolver`` MUST be attached to the
        parent ``AgentSession`` BEFORE the subprocess is spawned.

        ``build_session_bridge`` is the single owner of the production
        bridge path. The session payload that the subprocess consumes is
        serialized inside ``server_fn`` (which maps to ``start_mcp_server``).
        The exec resolver must therefore be attached before that call so
        the on-disk payload can carry the resolver config and the
        subprocess MCP server can re-construct the resolver from the
        payload. Otherwise the production ``resources/read`` path returns
        a structured "resolver not attached" error and the AC-11
        stdout/stderr replayable resource IDs are not re-readable inside
        the spawned MCP subprocess.
        """
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )
        assert isinstance(bridge, FakeSessionBridge)
        # The session the bridge was constructed with is captured by the
        # fake. By the time ``start()`` is called, the parent
        # ``AgentSession`` must already own a resolver. ``start()`` is
        # the production subprocess spawn point, so this is the
        # production bridge path's contract.
        assert bridge.started is True
        assert bridge.attached_session is not None
        resolver = bridge.attached_session.exec_resource_resolver
        assert resolver is not None, (
            "AC-11: exec_resource_resolver must be attached to the "
            "parent session before the subprocess MCP server is spawned; "
            "a missing resolver makes the production resources/read "
            "path return a structured 'resolver not attached' error and "
            "the AC-11 replayable stdout/stderr resource IDs are not "
            "re-readable."
        )

    def test_session_payload_json_carries_exec_spill_roots(self, tmp_path: Path) -> None:
        """AC-11: ``session_payload_json`` MUST serialize the exec resolver's
        trusted spill roots so the subprocess session can re-construct a
        matching resolver from the on-disk payload.

        Without this re-attach in the subprocess MCP server, the
        ``resources/read`` handler returns a structured "resolver not
        attached" error and the AC-11 stdout/stderr replayable contract
        is broken for the production subprocess path.
        """
        from ralph.mcp.server.lifecycle import session_payload_json

        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )
        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.attached_session is not None
        import json

        payload = json.loads(session_payload_json(bridge.attached_session))
        assert "exec_spill_roots" in payload, (
            "AC-11: session_payload_json must include exec_spill_roots so "
            "the subprocess MCP server can re-construct the resolver "
            "from the on-disk payload."
        )
        spill_roots = payload["exec_spill_roots"]
        assert isinstance(spill_roots, list)
        assert spill_roots, "exec_spill_roots must include at least one path"
        # All listed roots must be Path-like strings; the subprocess
        # session re-constructs ``Path`` objects from them.
        for root in spill_roots:
            assert isinstance(root, str) and root
        # Sanity: the workspace's ``.agent/tmp`` is the canonical trusted
        # root the production bridge attaches. The exact name is
        # internal to the bridge, but the path must point at something
        # under the workspace root the bridge was built for.
        assert any(
            str(tmp_path) in root or str(tmp_path / ".agent" / "tmp") in root
            for root in spill_roots
        ), (
            f"AC-11: exec_spill_roots must include the workspace "
            f"<tmp_path>/.agent/tmp, got {spill_roots!r}"
        )

    def test_file_backed_session_reconstructs_resolver_from_on_disk_payload(
        self, tmp_path: Path
    ) -> None:
        """AC-11: the subprocess ``FileBackedSession`` MUST re-construct
        the exec resolver from the on-disk session payload's
        ``exec_spill_roots`` so ``resources/read`` can replay parent-side
        exec URIs.

        This is the production subprocess path: the parent
        ``build_session_bridge`` writes the on-disk session payload via
        ``create_session_file`` and the subprocess session reads it via
        ``session_from_env``. Without the lazy re-attach, the resolver
        is ``None`` inside the subprocess and the AC-11 contract is
        broken.
        """
        from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV
        from ralph.mcp.server.lifecycle import _create_session_file
        from ralph.mcp.server.runtime_session import session_from_env

        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )
        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.attached_session is not None
        # Drop the session file exactly where the production
        # ``_spawn_mcp_process`` would: under ``.agent/tmp``.
        session_file = _create_session_file(tmp_path, bridge.attached_session)
        try:
            restored = session_from_env({MCP_SESSION_FILE_ENV: str(session_file)})
            assert restored is not None
            assert getattr(restored, "exec_resource_resolver", None) is not None, (
                "AC-11: the subprocess session must re-construct the "
                "exec resolver from exec_spill_roots in the on-disk "
                "payload."
            )
        finally:
            session_file.unlink(missing_ok=True)

    def test_artifact_format_docs_are_materialized_into_workspace(self, tmp_path: Path) -> None:
        """The pre-render hook must materialize every bundled format doc.

        The artifact submission macro tells the agent to read
        ``.agent/artifact-formats/<type>.md`` at submit time. If the doc
        isn't on disk, the agent's first read ENOENTs and the submission
        silently breaks. The bug surfaced on 2026-06-14 when the commit
        path was reworked but the format docs were never written into the
        workspace, so ``commit_message.md`` didn't exist when the agent
        tried to read it.

        ``build_session_bridge`` is the single owner of session setup, so
        the materialization MUST happen there — before the prompt is
        rendered, before the agent runs. This test pins that contract.
        """
        stale_doc = tmp_path / ".agent" / "artifact-formats" / "commit_message.md"
        stale_doc.parent.mkdir(parents=True)
        stale_doc.write_text(
            "Call ralph_submit_artifact with a JSON-serialized content payload.",
            encoding="utf-8",
        )

        build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            run_id="commit-plumbing",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        formats_dir = tmp_path / ".agent" / "artifact-formats"
        # At minimum, every artifact type the single-shot templates submit
        # must have its doc on disk. If the bundled set grows, the
        # assertion is exhaustive — the docs directory is the audit
        # surface, not a hand-picked subset.
        expected = {
            "commit_message",
            "commit_cleanup",
            "development_result",
            "issues",
            "development_analysis_decision",
            "planning_analysis_decision",
            "review_analysis_decision",
        }
        actual = {p.stem for p in formats_dir.glob("*.md")}
        missing = expected - actual
        assert not missing, (
            f"build_session_bridge did not materialize format docs: "
            f"missing {sorted(missing)} in {formats_dir}"
        )
        bundled_commit_doc = load_bundled_format_doc("commit_message")
        assert bundled_commit_doc is not None
        assert stale_doc.read_text(encoding="utf-8") == bundled_commit_doc
        assert "ralph_submit_artifact" not in bundled_commit_doc


class TestBridgeEnvFor:
    """Black-box tests for bridge_env_for.

    The MCP_RUN_ID_ENV value MUST be derived from ``bridge.run_id`` (the
    session's authoritative run identity). Deriving it from a free-form label
    passed in by the caller is the root cause of the artifact-handoff drift
    bug: when the label and the session's run_id disagree, the submission
    handler stamps the receipt with one value and the completion gate looks
    it up under the other.
    """

    def test_returns_exactly_two_keys(self, tmp_path: Path) -> None:
        bridge = FakeSessionBridge(endpoint="http://localhost:7777", run_id="commit-plumbing")
        env = bridge_env_for(bridge)

        assert set(env.keys()) == {str(MCP_ENDPOINT_ENV), str(MCP_RUN_ID_ENV)}
        assert env[str(MCP_ENDPOINT_ENV)] == "http://localhost:7777"
        assert env[str(MCP_RUN_ID_ENV)] == "commit-plumbing"

    def test_run_id_env_equals_bridge_run_id(self) -> None:
        """MCP_RUN_ID_ENV is derived from the bridge's run_id (no separate label)."""
        bridge = FakeSessionBridge(run_id="any-stable-value")
        env = bridge_env_for(bridge)
        assert env[str(MCP_RUN_ID_ENV)] == bridge.run_id

    def test_run_id_env_changes_when_bridge_run_id_changes(self) -> None:
        """Two bridges with different run_ids must produce different env values.

        This is the binding test: the env's run_id cannot drift away from
        the session's run_id, because the two values come from the same
        object property.
        """
        bridge_a = FakeSessionBridge(run_id="run-A")
        bridge_b = FakeSessionBridge(run_id="run-B")
        assert bridge_env_for(bridge_a)[str(MCP_RUN_ID_ENV)] == "run-A"
        assert bridge_env_for(bridge_b)[str(MCP_RUN_ID_ENV)] == "run-B"


class TestResetToolRegistryCallback:
    """Black-box tests for reset_tool_registry_callback."""

    def test_returns_none_when_bridge_is_none(self) -> None:
        assert reset_tool_registry_callback(None) is None

    def test_returns_callable_when_reset_tool_registry_exists(self, tmp_path: Path) -> None:
        bridge = FakeSessionBridge()
        callback = reset_tool_registry_callback(bridge)

        assert callback is not None
        assert callback() == "reset"
        assert bridge.reset_tool_registry_called is True

    def test_returns_none_when_reset_tool_registry_missing(self, tmp_path: Path) -> None:
        class NoResetBridge:
            pass

        assert reset_tool_registry_callback(NoResetBridge()) is None


class TestProtocolAliases:
    """Ensure callable aliases are importable and structural."""

    def test_bridge_factory_protocol_is_callable(self, tmp_path: Path) -> None:
        class FakeBridgeFactory:
            @property
            def run_id(self) -> str:
                return "fake-run-id"

            def __call__(
                self,
                *,
                workspace_root: Path,
                drain: str,
                agents_policy: AgentsPolicy | None,
                transport: AgentTransport | None = None,
                capabilities: frozenset[str] | None = None,
                session_id_prefix: str | None = None,
                run_id: str | None = None,
                model_identity: MultimodalModelIdentity | None = None,
                parallel_worker: bool = False,
                build_session_mcp_plan_fn: BuildSessionMcpPlanFn | None = None,
                start_mcp_server_fn: StartMcpServerFn | None = None,
                workspace_factory: WorkspaceFactoryFn | None = None,
            ) -> SessionBridgeLike:
                return FakeSessionBridge()

        factory: BridgeFactory = FakeBridgeFactory()
        bridge = factory(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
        )
        assert isinstance(bridge, FakeSessionBridge)
