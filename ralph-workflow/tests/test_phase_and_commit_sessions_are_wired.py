"""The chain-transport decisions must be WIRED, not merely correct.

``phase_session_identity`` and ``commit_chain_is_ambiguous`` are the two
halves of one rule: a session serving several agents must not resolve a
provider for a CLI that may not be the one that runs. Both were pinned
as units, and neither was pinned at its call site -- so a mutation sweep
reverted each wiring with the entire 14k-test suite green, including one
that re-tagged a phase session with the FIRST agent's transport, which
is exactly the defect the resolution exists to prevent.

That is the same criticism the fix itself made of the code it replaced
("one rule, two call sites"), turned on its own coverage.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime


class _RecordingBridgeFactory:
    """Captures the identity a bridge would have been built with."""

    def __init__(self) -> None:
        self.model_identity: object = "unset"
        self.transport: object = "unset"

    def __call__(self, **kwargs: object) -> object:
        self.model_identity = kwargs.get("model_identity")
        self.transport = kwargs.get("transport")
        return SimpleNamespace(shutdown=lambda: None)


@pytest.fixture
def injected_identity() -> object:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity

    return MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5", transport="claude"
    )


def _drive_bridge(factory: _RecordingBridgeFactory, identity: object, *, drop: bool) -> None:
    core = SimpleNamespace(model_identity=identity)
    with with_bridge_lifetime(
        core,
        factory,
        repo_root=SimpleNamespace(),
        drain="commit",
        session_id_prefix="commit",
        transport=None,
        drop_injected_identity=drop,
    ):
        pass


def test_a_dropped_identity_does_not_reach_the_commit_bridge(injected_identity: object) -> None:
    """An ambiguous chain must not resolve the injected provider.

    Driven end to end: with the flag set the bridge is built with NO
    model identity, so the session resolves as unresolved and delivery
    degrades to resource references every candidate can accept.
    """
    factory = _RecordingBridgeFactory()

    _drive_bridge(factory, injected_identity, drop=True)

    assert factory.model_identity is None


def test_an_unambiguous_chain_keeps_its_injected_identity(injected_identity: object) -> None:
    """Not vacuous: the identity still reaches the bridge otherwise."""
    factory = _RecordingBridgeFactory()

    _drive_bridge(factory, injected_identity, drop=False)

    assert factory.model_identity is injected_identity


def test_the_phase_plan_passes_the_RESOLVED_chain_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fan-out plan must use the resolver, not the first agent.

    Reverting this wiring re-tags a phase session with the FIRST agent's
    transport, so a ``[claude, codex]`` chain hands the codex worker
    inline images -- the original incident. Driven: the values the
    resolver returns are the values the identity rule receives.

    The builder fails further down on stub inputs; that is past the
    decision under test, so the recorded call is the assertion.
    """
    from unittest.mock import MagicMock

    from ralph.config.enums import AgentTransport
    from ralph.pipeline import fan_out

    recorded: dict[str, object] = {}
    real_identity = fan_out.phase_session_identity

    def recording_identity(
        transport: object,
        model_flag: object,
        chain_transport: object,
        *,
        chain_is_ambiguous: bool = False,
    ) -> object:
        recorded["chain_transport"] = chain_transport
        recorded["chain_is_ambiguous"] = chain_is_ambiguous
        return real_identity(
            transport, model_flag, chain_transport, chain_is_ambiguous=chain_is_ambiguous
        )

    monkeypatch.setattr(fan_out, "phase_session_identity", recording_identity)
    monkeypatch.setattr(
        fan_out,
        "resolve_phase_session_transport",
        lambda _agents, _config: (AgentTransport.CODEX, True),
    )

    effect = SimpleNamespace(phase="development", drain=None)
    bundle = MagicMock()
    bundle.pipeline.phases.get.return_value = SimpleNamespace(drain="development")
    # The builder fails further down on stub inputs; that is past the
    # decision under test, so the recorded call is the assertion.
    with pytest.raises(Exception, match=r".*"):
        fan_out.build_session_mcp_plan_for_phase(effect, bundle, MagicMock(), None)

    assert recorded["chain_transport"] is AgentTransport.CODEX
    assert recorded["chain_is_ambiguous"] is True


def test_the_commit_call_site_asks_whether_the_chain_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit plumbing must ASK, not assume.

    Deleting the argument at the call site leaves the default -- keep
    the injected identity -- and survived the whole suite.
    """
    from ralph.pipeline.plumbing import commit_plumbing

    asked: list[object] = []
    monkeypatch.setattr(
        commit_plumbing,
        "commit_chain_is_ambiguous",
        lambda chain_config: asked.append(chain_config) or True,
    )

    recorded: dict[str, object] = {}

    def recording_lifetime(*args: object, **kwargs: object) -> object:
        recorded.update(kwargs)
        msg = "stop after the decision under test"
        raise RuntimeError(msg)

    monkeypatch.setattr(commit_plumbing, "with_bridge_lifetime", recording_lifetime)

    from unittest.mock import MagicMock

    with pytest.raises(RuntimeError, match="stop after the decision"):
        commit_plumbing.run_commit_plumbing(
            diff="diff --git a/x b/x",
            repo_root=MagicMock(),
            chain_config=MagicMock(),
            pipeline_core=MagicMock(model_identity=None),
            bridge_factory=MagicMock(),
        )

    assert recorded["drop_injected_identity"] is True
    assert asked, "the chain was never consulted"


def test_a_chain_that_disagrees_on_MODEL_is_ambiguous_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agreeing on the CLI is not agreeing on the provider.

    Two agents can share a transport and name different models: two
    ``claude``-transport candidates where only the first carries
    ``--model gemini/...``. Comparing transports alone called that
    unambiguous, so the phase session kept the first candidate's flag,
    resolved GEMINI's capabilities for the whole phase, and minted
    AudioContent and VideoContent that the agent which actually ran
    cannot carry. It was order-dependent in exactly the way the
    ambiguity rule was written to stop.
    """
    from unittest.mock import MagicMock

    from ralph.config.enums import AgentTransport
    from ralph.pipeline import chain_identity, fan_out

    def config_for(transport: AgentTransport, model_flag: str | None) -> object:
        return SimpleNamespace(transport=transport, model_flag=model_flag)

    class _Registry:
        def __init__(self, mapping: dict[str, object]) -> None:
            self._mapping = mapping

        def get(self, name: str) -> object:
            return self._mapping.get(name)

    def resolve(mapping: dict[str, object], names: list[str]) -> tuple[object, bool]:
        monkeypatch.setattr(
            chain_identity,
            "AgentRegistry",
            SimpleNamespace(from_config=lambda _config: _Registry(mapping)),
        )
        return fan_out.resolve_phase_session_transport(names, MagicMock())

    mixed_models = {
        "gem": config_for(AgentTransport.CLAUDE, "--model gemini/gemini-2.5-pro"),
        "plain": config_for(AgentTransport.CLAUDE, None),
    }
    transport, ambiguous = resolve(mixed_models, ["gem", "plain"])
    assert transport is AgentTransport.CLAUDE
    assert ambiguous is True

    # Not vacuous: agreeing on both is still unambiguous, so a
    # homogeneous chain keeps the provider it can honestly resolve.
    same = {
        "a": config_for(AgentTransport.CLAUDE, "--model x"),
        "b": config_for(AgentTransport.CLAUDE, "--model x"),
    }
    transport, ambiguous = resolve(same, ["a", "b"])
    assert transport is AgentTransport.CLAUDE
    assert ambiguous is False
