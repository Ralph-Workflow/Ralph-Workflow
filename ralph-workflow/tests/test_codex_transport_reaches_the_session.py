from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE, ResourceReferenceContent
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    DeliveryMode,
    MultimodalModelIdentity,
    inline_image_roundtrip_unsafe,
)
from ralph.mcp.tools.coordination import ImageContent
from ralph.mcp.tools.workspace._media_handlers import (
    handle_read_media,
)
from ralph.workspace.fs import FsWorkspace
from tests.mock_session_with_manifest import MockSessionWithManifest

MEDIA_READ_CAPABILITY = "media.read"

pytestmark = pytest.mark.timeout_seconds(5)

_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDAT"
    b"\x78\x9c\x62\x00\x01\x00\x00\x05\x00\x01"
    b"\x0d\x0a\x2d\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_CODEX_IDENTITY = MultimodalModelIdentity(
    provider="openai",
    model_id="gpt-5.6-terra",
    transport="codex",
)


def _write_png(tmp_path: Path, name: str = "tiny.png") -> Path:
    file_path = tmp_path / name
    file_path.write_bytes(_PNG_BYTES)
    return file_path


def _set_profile(session: object, profile: object) -> None:
    session.capability_profile = profile


def _codex_session() -> MockSessionWithManifest:
    return MockSessionWithManifest(
        MEDIA_READ_CAPABILITY,
        model_identity=_CODEX_IDENTITY,
    )


def _minimal_agents_policy() -> object:
    from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

    return AgentsPolicy(
        agent_chains={
            "development": AgentChainConfig(agents=["codex"], max_retries=1, retry_delay_ms=1000)
        },
        agent_drains={
            "development": AgentDrainConfig(chain="development", drain_class="development")
        },
    )


def test_flagless_codex_plan_still_carries_the_transport(tmp_path: Path) -> None:
    from ralph.mcp.session_plan import build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CODEX,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
    )

    assert plan.model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(plan.model_identity)


def test_flagless_codex_plan_withholds_inline_image_delivery(tmp_path: Path) -> None:
    from ralph.mcp.session_plan import build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CODEX,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
    )

    assert plan.capability_profile is not None
    verdict = plan.capability_profile.verdict_for(MODALITY_IMAGE)
    assert verdict.delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY


def test_flagless_non_codex_plan_keeps_unknown_provider(tmp_path: Path) -> None:
    from ralph.mcp.session_plan import build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
    )

    assert plan.model_identity.provider == "unknown"
    assert plan.model_identity.transport == "claude"
    assert not inline_image_roundtrip_unsafe(plan.model_identity)


def test_transport_only_identity_survives_the_session_handshake() -> None:
    import json as _json

    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.server.lifecycle import session_payload_json

    class _Session:
        session_id = "s-1"
        run_id = "r-1"
        drain = "development"
        capabilities: frozenset[str] = frozenset()
        model_identity = MultimodalModelIdentity(
            provider="unknown", model_id=None, transport="codex"
        )

    payload = _json.loads(session_payload_json(_Session()))

    assert payload.get("model_identity", {}).get("transport") == "codex"


def test_codex_replay_ignores_a_stale_inline_image_verdict(tmp_path: Path) -> None:
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        ResolvedCapabilityProfile,
    )

    _write_png(tmp_path)
    session = _codex_session()
    stale_profile = ResolvedCapabilityProfile(
        identity=_CODEX_IDENTITY,
        verdicts={
            MODALITY_IMAGE: CapabilityVerdict(
                modality=MODALITY_IMAGE,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider=_CODEX_IDENTITY.provider,
                model_id=_CODEX_IDENTITY.model_id,
                reason="stored by a pre-fix Ralph Workflow session",
            )
        },
    )
    _set_profile(session, stale_profile)
    workspace = FsWorkspace(tmp_path)

    first = handle_read_media(session, workspace, {"path": "tiny.png"})
    handles = [block.uri for block in first.content if isinstance(block, ResourceReferenceContent)]
    assert handles, f"expected a replay handle, got {first.content}"

    replayed = handle_read_media(session, workspace, {"path": handles[0]})

    assert not any(isinstance(block, ImageContent) for block in replayed.content)


def test_codex_pdf_delivery_gets_no_spurious_degradation_warning(
    tmp_path: Path,
) -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity

    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    result = handle_read_media(
        MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(
                provider="claude",
                model_id="claude-opus-5",
                transport="codex",
            ),
        ),
        FsWorkspace(tmp_path),
        {"path": "report.pdf"},
    )

    warnings = [
        block
        for block in result.content
        if getattr(block, "type", None) == "text" and "WARNING" in getattr(block, "text", "")
    ]
    assert warnings == [], f"unexpected degradation warning: {warnings}"


def test_capability_helpers_are_exported() -> None:
    from ralph.mcp.multimodal import capabilities

    assert "inline_image_roundtrip_unsafe" in capabilities.__all__
    assert "inline_image_requires_text_handle" in capabilities.__all__


def test_injected_identity_gets_the_transport_backfilled(tmp_path: Path) -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CODEX,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
        model_opts=SessionModelOpts(
            model_identity=MultimodalModelIdentity(provider="openai", model_id="gpt-5.6-terra")
        ),
    )

    assert plan.model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(plan.model_identity)


def test_the_launched_transport_outranks_a_stale_injected_one(tmp_path: Path) -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
        model_opts=SessionModelOpts(
            model_identity=MultimodalModelIdentity(
                provider="opencode", model_id="minimax", transport="opencode"
            )
        ),
    )

    assert plan.model_identity.transport == "claude"
    assert plan.model_identity.provider == "unknown"


def test_an_injected_identity_agreeing_on_the_transport_is_untouched(
    tmp_path: Path,
) -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
        model_opts=SessionModelOpts(
            model_identity=MultimodalModelIdentity(
                provider="claude", model_id="claude-opus-5", transport="claude"
            )
        ),
    )

    assert plan.model_identity.provider == "claude"
    assert plan.model_identity.model_id == "claude-opus-5"


def test_prebuilt_capability_plan_keeps_the_agent_identity(tmp_path: Path) -> None:
    from importlib import import_module
    from types import SimpleNamespace

    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.workspace.memory import MemoryWorkspace

    session_runtime = import_module("ralph.session_runtime")
    agent_config = AgentConfig(cmd="codex", transport=AgentTransport.CODEX)
    config = UnifiedConfig(general=GeneralConfig(), agents={"codex": agent_config})
    captured: dict[str, object] = {}

    def _fake_start_mcp_server(*args: object) -> object:
        captured["session"] = args[0]
        return SimpleNamespace(
            agent_endpoint_uri=lambda: "http://127.0.0.1:9999/mcp",
            shutdown=lambda: None,
        )

    deps = session_runtime.ManagedAgentSessionDeps(
        start_mcp_server=_fake_start_mcp_server,
        invoke_agent=lambda *_a: iter(()),
        materialize_master_prompt=lambda *_a: str(tmp_path / "system.md"),
        workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
    )

    with session_runtime.ManagedAgentSessionRuntime.open(
        config=config,
        workspace_root=tmp_path,
        agent_config=agent_config,
        request=session_runtime.ManagedAgentSessionRequest(
            session_id_prefix="managed-agent",
            drain="standalone",
            capabilities=frozenset({"media.read"}),
        ),
        deps=deps,
    ):
        pass

    session = captured["session"]

    assert session.model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(session.model_identity)


def test_codex_metadata_handle_survives_a_stale_inline_verdict(tmp_path: Path) -> None:
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        ResolvedCapabilityProfile,
    )

    _write_png(tmp_path)
    session = _codex_session()
    _set_profile(
        session,
        ResolvedCapabilityProfile(
            identity=_CODEX_IDENTITY,
            verdicts={
                MODALITY_IMAGE: CapabilityVerdict(
                    modality=MODALITY_IMAGE,
                    delivery=DeliveryMode.INLINE_IMAGE,
                    provider=_CODEX_IDENTITY.provider,
                    model_id=_CODEX_IDENTITY.model_id,
                    reason="stored by a pre-guard session",
                )
            },
        ),
    )

    result = handle_read_media(
        session, FsWorkspace(tmp_path), {"path": "tiny.png", "format": "metadata"}
    )
    envelope = json.loads(result.content[0].text)

    assert envelope["resource_handle"] is not None
    assert envelope["inline_only"] is False


def test_serialised_profile_does_not_carry_a_stale_inline_verdict() -> None:
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        ResolvedCapabilityProfile,
    )

    profile = ResolvedCapabilityProfile(
        identity=_CODEX_IDENTITY,
        verdicts={
            MODALITY_IMAGE: CapabilityVerdict(
                modality=MODALITY_IMAGE,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider=_CODEX_IDENTITY.provider,
                model_id=_CODEX_IDENTITY.model_id,
                reason="stored by a pre-guard session",
            )
        },
    )

    payload = profile.to_payload()
    verdicts = payload["verdicts"]
    assert isinstance(verdicts, dict)

    assert verdicts[MODALITY_IMAGE]["delivery"] == DeliveryMode.RESOURCE_REFERENCE_REPLAY.value


def test_a_restricted_launched_transport_overrides_an_injected_one(
    tmp_path: Path,
) -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CODEX,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
        model_opts=SessionModelOpts(
            model_identity=MultimodalModelIdentity(
                provider="claude", model_id="claude-opus-5", transport="claude"
            )
        ),
    )

    assert plan.model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(plan.model_identity)


def test_session_transport_selection_prefers_the_restricted_candidate() -> None:
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "codex"]) == "codex"
    assert select_session_transport(["codex", "claude"]) == "codex"


def test_session_transport_selection_keeps_a_homogeneous_chain() -> None:
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "claude"]) == "claude"


def test_session_transport_selection_declines_a_mixed_capable_chain() -> None:
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "opencode"]) is None
    assert select_session_transport([]) is None


def test_standalone_server_can_declare_the_agent_cli_it_serves() -> None:
    from ralph.mcp.server.runtime import parse_args, standalone_session_identity

    assert parse_args(["--agent-transport", "codex"]).agent_transport == "codex"

    identity = standalone_session_identity("codex")

    assert identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(identity)


def test_standalone_server_without_a_declared_cli_stays_permissive() -> None:
    from ralph.mcp.server.runtime import parse_args, standalone_session_identity

    assert parse_args([]).agent_transport is None

    assert not inline_image_roundtrip_unsafe(standalone_session_identity(None))


def test_upstream_block_without_a_readable_type_is_rejected() -> None:
    from ralph.mcp.upstream.client import (
        UpstreamCallError,
        normalize_upstream_content_blocks,
    )

    for bad_type in ({}, {"type": 7}):
        block = dict(bad_type)
        block.update({"data": "iVBORw0KGgo=", "mimeType": "image/png"})
        result: dict[str, object] = {"content": [block]}

        with pytest.raises(UpstreamCallError):
            normalize_upstream_content_blocks(result, "srv", "tool")


def _write_session_file(tmp_path: Path, identity: dict[str, object] | None) -> Path:
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import session_payload_json

    parent = AgentSession(
        session_id="s",
        run_id="r",
        drain="standalone",
        capabilities={MEDIA_READ_CAPABILITY},
        model_identity=(
            MultimodalModelIdentity(
                provider=str(identity["provider"]),
                model_id=str(identity["model_id"]),
                transport=str(identity["transport"]),
            )
            if identity is not None
            else UNKNOWN_IDENTITY
        ),
    )
    payload = json.loads(session_payload_json(parent))
    assert "capability_profile" in payload, "the real writer always emits this key"
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps(payload), encoding="utf-8")
    return session_file


def test_declared_cli_reaches_a_file_backed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.mcp.server.runtime_session import session_from_env

    monkeypatch.setenv("RALPH_MCP_SESSION_FILE", str(_write_session_file(tmp_path, None)))

    session = session_from_env(declared_agent_transport="codex")

    assert session is not None
    assert session.caller_model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(session.caller_model_identity)
    assert session.caller_capability_profile.identity.transport == "codex"
    assert session.caller_capability_profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_the_restricted_side_wins_between_payload_and_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.mcp.server.runtime_session import session_from_env

    monkeypatch.setenv(
        "RALPH_MCP_SESSION_FILE",
        str(
            _write_session_file(
                tmp_path,
                {"provider": "claude", "model_id": "claude-opus-5", "transport": "claude"},
            )
        ),
    )

    session = session_from_env(declared_agent_transport="codex")

    assert session is not None
    assert session.caller_model_identity.transport == "codex"


def test_overriding_the_transport_also_drops_the_stale_provider(
    tmp_path: Path,
) -> None:
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan

    plan = build_session_mcp_plan(
        transport=AgentTransport.CODEX,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_minimal_agents_policy(),
        model_opts=SessionModelOpts(
            model_identity=MultimodalModelIdentity(
                provider="claude", model_id="claude-opus-5", transport="claude"
            )
        ),
    )

    assert plan.model_identity.transport == "codex"
    assert plan.model_identity.provider == "unknown"
    assert plan.capability_profile is not None
    assert plan.capability_profile.verdict_for(MODALITY_PDF).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_the_commit_chain_rule_matches_the_shared_rule() -> None:
    from types import SimpleNamespace

    from ralph.config.models import AgentConfig
    from ralph.mcp.multimodal.capabilities import select_session_transport
    from ralph.pipeline.plumbing.commit_plumbing import commit_chain_transport

    shapes = [
        ["claude"],
        ["codex"],
        ["claude", "codex"],
        ["codex", "claude"],
        ["claude", "opencode"],
        ["claude", "claude"],
        ["codex", "codex"],
        ["claude", "opencode", "codex"],
        [],
    ]
    registry = {
        "claude": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        "codex": AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        "opencode": AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
    }

    for agents in shapes:
        chain = SimpleNamespace(agents=agents, registry=SimpleNamespace(get=registry.get))
        chain_answer = commit_chain_transport(chain)
        shared_answer = select_session_transport(
            [registry[name].transport.value for name in agents]
        )
        assert (chain_answer.value if chain_answer else None) == shared_answer, agents


def test_declared_cli_reaches_a_json_env_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.mcp.protocol.env import MCP_SESSION_ENV, MCP_SESSION_FILE_ENV
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import session_payload_json
    from ralph.mcp.server.runtime_session import session_from_env

    del tmp_path
    parent = AgentSession(
        session_id="s", run_id="r", drain="standalone", capabilities={MEDIA_READ_CAPABILITY}
    )
    monkeypatch.delenv(str(MCP_SESSION_FILE_ENV), raising=False)
    monkeypatch.setenv(str(MCP_SESSION_ENV), session_payload_json(parent))

    session = session_from_env(declared_agent_transport="codex")

    assert session is not None
    assert session.caller_capability_profile.identity.transport == "codex"
    assert session.caller_capability_profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_a_delegate_inherits_the_sessions_transport() -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.protocol.session import AgentSession

    session = AgentSession(
        session_id="s",
        run_id="r",
        drain="standalone",
        capabilities={MEDIA_READ_CAPABILITY},
        model_identity=_CODEX_IDENTITY,
    )
    session.delegated_model_identity = MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5"
    )

    assert inline_image_roundtrip_unsafe(session.caller_model_identity)
    assert session.caller_capability_profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_a_delegate_cannot_declare_the_guard_away() -> None:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.protocol.session import AgentSession

    session = AgentSession(
        session_id="s",
        run_id="r",
        drain="standalone",
        capabilities={MEDIA_READ_CAPABILITY},
        model_identity=_CODEX_IDENTITY,
    )
    session.delegated_model_identity = MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5", transport="claude"
    )

    assert session.caller_model_identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(session.caller_model_identity)
    assert session.caller_capability_profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_a_replayed_pdf_is_never_wrapped_in_an_image_block(tmp_path: Path) -> None:
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        MultimodalModelIdentity,
        ResolvedCapabilityProfile,
    )

    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    claude = MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5", transport="claude"
    )
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY, model_identity=claude)
    _set_profile(
        session,
        ResolvedCapabilityProfile(
            identity=claude,
            verdicts={
                MODALITY_PDF: CapabilityVerdict(
                    modality=MODALITY_PDF,
                    delivery=DeliveryMode.INLINE_IMAGE,
                    provider="claude",
                    model_id="claude-opus-5",
                    reason="stored by something that should not have",
                )
            },
        ),
    )
    workspace = FsWorkspace(tmp_path)

    first = handle_read_media(session, workspace, {"path": "doc.pdf"})

    assert not any(isinstance(b, ImageContent) for b in first.content), first.content
    typed = [b for b in first.content if getattr(b, "type", None) == "pdf"]
    assert typed, first.content

    replayed = handle_read_media(session, workspace, {"path": typed[0].uri})

    assert not any(isinstance(b, ImageContent) for b in replayed.content), replayed.content
    assert [getattr(b, "type", None) for b in replayed.content] == ["pdf"], replayed.content


def test_the_child_payload_records_the_verdicts_the_runtime_acts_on(
    tmp_path: Path,
) -> None:
    import json as _json

    from ralph.mcp.multimodal.capabilities import (
        UNKNOWN_IDENTITY,
        MultimodalModelIdentity,
        resolve_capability_profile,
    )
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import session_payload_json

    del tmp_path
    session = AgentSession(
        session_id="s",
        run_id="r",
        drain="standalone",
        capabilities={MEDIA_READ_CAPABILITY},
        model_identity=MultimodalModelIdentity(
            provider="unknown", model_id=None, transport="codex"
        ),
        stored_capability_profile=resolve_capability_profile(UNKNOWN_IDENTITY),
    )

    payload = _json.loads(session_payload_json(session))
    profile = payload["capability_profile"]

    assert profile["transport"] == "codex"
    assert profile["verdicts"][MODALITY_IMAGE]["delivery"] == (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY.value
    )
