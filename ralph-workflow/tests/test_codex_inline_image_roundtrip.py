"""Regression: the Codex CLI must never receive an inline image block.

Field evidence (2026-08-20, ``codex/gpt-5.6-terra`` dev loop): Ralph
answered a ``read_media`` call with a base64 ``ImageContent`` block.
The Codex CLI re-serialised that MCP tool result into its next
Responses API request using the part type ``output_text``, which the
API rejects::

    [400]: Invalid value: 'output_text'. Supported values are:
    'input_text', 'input_image', 'input_file', and 'scoped_content'.
    (param: input[101].output[1])

The turn died (``turn.failed``), the process was killed, and the run
was graded ``FAILED (no artifact)`` because ``development_result.md``
was never written. Ralph cannot fix the Codex CLI's serialisation, so
it must not hand that transport an inline image in the first place.

The delivery decision keys on the *transport*, not the provider: a
Codex CLI run resolves to ``provider='openai'`` with
``transport='codex'`` (see ``_TRANSPORT_FIXED_PROVIDER`` in
``ralph/mcp/session_plan.py``), and the defect is in the CLI's wire
serialisation rather than in any provider's API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE, ResourceReferenceContent
from ralph.mcp.multimodal.capabilities import (
    DeliveryMode,
    MultimodalModelIdentity,
    get_delivery_mode,
    inline_image_roundtrip_unsafe,
)
from ralph.mcp.session_plan import resolve_model_identity
from ralph.mcp.tools.coordination import ImageContent
from ralph.mcp.tools.workspace._media_handlers import (
    handle_read_image,
    handle_read_media,
)
from ralph.workspace.fs import FsWorkspace
from tests.mock_session_with_manifest import MockSessionWithManifest

MEDIA_READ_CAPABILITY = "media.read"

pytestmark = pytest.mark.timeout_seconds(5)

# Minimal valid PNG: 1x1 transparent pixel, generated inline so the
# test stays hermetic (no file fixtures).
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
    """Inject a capability profile onto a mock session.

    The production path stores the profile on the session; the mock
    exposes the same attribute so the production helper reads it.
    """
    session.capability_profile = profile


def _codex_session() -> MockSessionWithManifest:
    return MockSessionWithManifest(
        MEDIA_READ_CAPABILITY,
        model_identity=_CODEX_IDENTITY,
    )


# ---------------------------------------------------------------------------
# Capability layer — the single source of truth for delivery decisions
# ---------------------------------------------------------------------------


def test_codex_transport_is_flagged_inline_image_roundtrip_unsafe() -> None:
    """The Codex CLI cannot round-trip an inline MCP image block."""
    assert inline_image_roundtrip_unsafe(_CODEX_IDENTITY)


def test_codex_transport_image_verdict_is_not_inline_image() -> None:
    """A Codex-transport image resolves to resource-reference delivery."""
    verdict = get_delivery_mode(_CODEX_IDENTITY, MODALITY_IMAGE)

    assert verdict.delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY
    assert verdict.delivery is not DeliveryMode.INLINE_IMAGE


def test_codex_transport_image_verdict_reason_names_the_transport() -> None:
    """The verdict explains why inline was withheld, for operator triage."""
    verdict = get_delivery_mode(_CODEX_IDENTITY, MODALITY_IMAGE)

    assert "codex" in verdict.reason.lower()


def test_openai_provider_without_codex_transport_still_gets_inline_image() -> None:
    """The guard keys on transport: a direct OpenAI identity is unaffected."""
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-5.6-terra")

    assert not inline_image_roundtrip_unsafe(identity)
    assert get_delivery_mode(identity, MODALITY_IMAGE).delivery is DeliveryMode.INLINE_IMAGE


def test_claude_transport_still_gets_inline_image() -> None:
    """No regression for the transports that do round-trip images."""
    identity = MultimodalModelIdentity(
        provider="claude",
        model_id="claude-opus-5",
        transport="claude",
    )

    assert not inline_image_roundtrip_unsafe(identity)
    assert get_delivery_mode(identity, MODALITY_IMAGE).delivery is DeliveryMode.INLINE_IMAGE


def test_resolved_codex_identity_is_roundtrip_unsafe() -> None:
    """The identity the runtime actually builds for Codex trips the guard.

    Guards the provider/transport mismatch that made the ``"codex"``
    key in ``_PROVIDER_UNSUPPORTED_MODALITIES`` dead code: a Codex CLI
    run resolves to ``provider='openai'``, so a provider-keyed check
    would never fire.
    """
    identity = resolve_model_identity(AgentTransport.CODEX, "gpt-5.6-terra")

    assert identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(identity)


# ---------------------------------------------------------------------------
# Tool layer — the surface that produced the 400
# ---------------------------------------------------------------------------


def test_read_media_on_codex_returns_no_inline_image_block(tmp_path: Path) -> None:
    """The exact regression: no ImageContent reaches a Codex transport."""
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert not any(isinstance(block, ImageContent) for block in result.content)


def test_read_image_on_codex_returns_no_inline_image_block(tmp_path: Path) -> None:
    """``read_image`` shares the delivery path and must be guarded too."""
    _write_png(tmp_path)

    result = handle_read_image(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert not any(isinstance(block, ImageContent) for block in result.content)


def test_read_media_on_codex_returns_replayable_resource_reference(
    tmp_path: Path,
) -> None:
    """Withholding inline bytes must not lose the artifact."""
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    refs = [b for b in result.content if isinstance(b, ResourceReferenceContent)]
    assert len(refs) == 1
    assert refs[0].uri.startswith("ralph://media/")
    assert refs[0].modality == MODALITY_IMAGE


def test_codex_replay_of_media_uri_never_returns_inline_image(tmp_path: Path) -> None:
    """Replaying the handle must not re-introduce the inline block.

    Without this the guard would be a single-hop fix: the first read
    returns a handle, and dereferencing that handle hands Codex the
    ImageContent that kills the turn.
    """
    _write_png(tmp_path)
    session = _codex_session()
    workspace = FsWorkspace(tmp_path)

    first = handle_read_media(session, workspace, {"path": "tiny.png"})
    refs = [b for b in first.content if isinstance(b, ResourceReferenceContent)]
    assert refs, "expected a replay handle from the first read"

    replayed = handle_read_media(session, workspace, {"path": refs[0].uri})

    assert replayed.is_error is False
    assert not any(isinstance(block, ImageContent) for block in replayed.content)


def test_codex_metadata_format_registers_a_replay_handle(tmp_path: Path) -> None:
    """``format='metadata'`` must still hand Codex a dereferenceable handle.

    The metadata path skipped artifact registration for images because
    images were previously always inline-delivered; a Codex image is
    resource-reference delivered, so the handle must be registered.
    """
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png", "format": "metadata"},
    )

    envelope = json.loads(result.content[0].text)

    assert envelope["media_kind"] == "image"
    assert envelope["resource_handle"] is not None
    assert envelope["resource_handle"].startswith("ralph://media/")


def test_non_codex_transport_still_receives_inline_image(tmp_path: Path) -> None:
    """Negative case: the fix must not degrade image-capable transports."""
    _write_png(tmp_path)

    result = handle_read_media(
        MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(
                provider="claude",
                model_id="claude-opus-5",
                transport="claude",
            ),
        ),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert any(isinstance(block, ImageContent) for block in result.content)


def test_codex_receives_a_warning_explaining_the_withheld_image(
    tmp_path: Path,
) -> None:
    """A bare reference for an obvious PNG needs a stated reason.

    Criterion 3's graceful-degradation contract: the agent gets a usable
    payload AND an operator-visible explanation of why this is the
    degraded path.
    """
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    warnings = [
        block.text
        for block in result.content
        if getattr(block, "type", None) == "text" and "WARNING" in getattr(block, "text", "")
    ]
    assert len(warnings) == 1, result.content
    text = warnings[0].lower()
    assert "codex" in text
    # The message must not send the agent round a loop for bytes it
    # cannot be given.
    assert "do not retry" in text
    assert "metadata" in text


def _minimal_agents_policy() -> object:
    """Smallest policy that lets ``build_session_mcp_plan`` resolve a drain."""
    from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

    return AgentsPolicy(
        agent_chains={
            "development": AgentChainConfig(
                agents=["codex"], max_retries=1, retry_delay_ms=1000
            )
        },
        agent_drains={
            "development": AgentDrainConfig(chain="development", drain_class="development")
        },
    )



# ---------------------------------------------------------------------------
# Runtime plumbing — the guard is worthless if the transport never arrives
# ---------------------------------------------------------------------------


def test_flagless_codex_plan_still_carries_the_transport(tmp_path: Path) -> None:
    """A guard keyed on a value the runtime discards is not a fix.

    ``ralph run`` builds most sessions with no ``--model`` flag. The plan
    builder used to fall straight through to ``UNKNOWN_IDENTITY`` in that
    case, dropping the transport it had been handed -- so the identity
    reaching the MCP server had ``transport=None`` and every Codex image
    took the inline path regardless of the capability guard.
    """
    from ralph.config.enums import AgentTransport
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
    """End of the same chain: the resolved profile must not say inline."""
    from ralph.config.enums import AgentTransport
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
    """Tagging the transport must not silently promote the provider.

    Resolving a canonical provider here would flip other modalities from
    resource-reference to UNSUPPORTED for every flagless run. The fix adds
    the transport tag only.
    """
    from ralph.config.enums import AgentTransport
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
    """The subprocess must receive the transport too.

    ``lifecycle`` serialized ``model_identity`` only when the provider
    resolved, so a transport-tagged unknown identity was dropped on the
    way to the MCP server and the guard died at the boundary.
    """
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
    """A rehydrated profile must not reopen the inline path.

    ``profile_from_payload`` trusts a stored verdict string verbatim, so a
    session payload written by a pre-fix Ralph carries
    ``image -> inline_image``. The replay sites keyed on that verdict
    rather than on the identity, so dereferencing a handle handed back by
    the (correctly guarded) first read returned the ImageContent that
    kills the turn.
    """
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
    handles = [
        block.uri for block in first.content if isinstance(block, ResourceReferenceContent)
    ]
    assert handles, f"expected a replay handle, got {first.content}"

    replayed = handle_read_media(session, workspace, {"path": handles[0]})

    assert not any(isinstance(block, ImageContent) for block in replayed.content)


def test_codex_pdf_delivery_gets_no_spurious_degradation_warning(
    tmp_path: Path,
) -> None:
    """The inline-image guard must not warn about unrelated modalities.

    A Codex CLI pointed at an image-incapable-but-PDF-capable provider
    delivers a PDF as a healthy typed block; prepending a
    "multimodal degraded" warning to that would be simply false.
    """
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
    """Both guards are imported by name across modules; export them."""
    from ralph.mcp.multimodal import capabilities

    assert "inline_image_roundtrip_unsafe" in capabilities.__all__
    assert "inline_image_requires_text_handle" in capabilities.__all__


def test_injected_identity_gets_the_transport_backfilled(tmp_path: Path) -> None:
    """A caller-supplied identity must not smuggle the transport away.

    ``SessionModelOpts(model_identity=...)`` is taken verbatim. A caller
    that knows the provider but leaves ``transport`` unset (the pro-hooks
    and public ``run_pipeline`` shape) therefore produced an untagged
    identity and reopened the inline path.
    """
    from ralph.config.enums import AgentTransport
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


def test_injected_identity_keeps_a_capable_explicit_transport(tmp_path: Path) -> None:
    """A stated transport survives when neither side is restricted.

    Backfill and reconciliation exist to close a hazard, not to
    second-guess a caller: with no restricted transport involved, the
    identity the caller supplied is left exactly as given.
    """
    from ralph.config.enums import AgentTransport
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

    assert plan.model_identity.transport == "opencode"


def test_prebuilt_capability_plan_keeps_the_agent_identity(tmp_path: Path) -> None:
    """Pre-supplied capabilities must not discard the resolved identity.

    Exercised through the public managed-session runtime: that branch
    returned a bare plan built from dataclass defaults, so the agent's
    transport never reached the session and the delivery guards were
    blind on this path.
    """
    from importlib import import_module
    from types import SimpleNamespace

    from ralph.config.enums import AgentTransport
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
    """The metadata envelope must not hand back a handle-less dead end.

    The envelope registers a replayable handle only for a
    resource-reference delivery. Under a stale stored ``inline_image``
    verdict that test failed, so a codex agent got
    ``resource_handle: null`` pointing at nothing -- the exact failure
    the registration branch exists to prevent.
    """
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
    """Re-serialisation must not propagate a verdict the runtime overrode.

    The session payload and the wire-ledger capability digest are the
    audit record of what was delivered. Emitting the raw stored verdict
    made that record disagree with the runtime on every re-save.
    """
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
    """When the two disagree, the restricted transport wins.

    The bridge lifetime primitive passes BOTH an injected identity and
    the transport the chain resolved. While a stated transport was always
    kept, a stale injected tag silently discarded the chain's answer and
    handed a restricted agent the inline image that kills its turn.
    """
    from ralph.config.enums import AgentTransport
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
    """A session serving several candidate agents takes the safe tag.

    A session is built before anyone knows which agent in a chain will
    run. Degrading a capable agent to a resource reference is harmless;
    handing a restricted agent an inline image kills its turn.
    """
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "codex"]) == "codex"
    assert select_session_transport(["codex", "claude"]) == "codex"


def test_session_transport_selection_keeps_a_homogeneous_chain() -> None:
    """A single-transport chain is tagged with its own transport."""
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "claude"]) == "claude"


def test_session_transport_selection_declines_a_mixed_capable_chain() -> None:
    """With nothing restricted and no agreement there is no honest tag."""
    from ralph.mcp.multimodal.capabilities import select_session_transport

    assert select_session_transport(["claude", "opencode"]) is None
    assert select_session_transport([]) is None
