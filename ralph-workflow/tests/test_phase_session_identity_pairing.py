from __future__ import annotations

from types import SimpleNamespace

from ralph.config.enums import AgentTransport
from ralph.pipeline.fan_out import phase_session_identity

_FLAG = "--model gpt-5.6-terra"


def test_a_restricted_chain_transport_drops_the_first_agents_model_flag() -> None:
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, AgentTransport.CODEX
    )

    assert transport is AgentTransport.CODEX
    assert model_flag is None


def test_the_reset_fires_even_when_the_restricted_agent_is_first() -> None:
    transport, model_flag = phase_session_identity(
        AgentTransport.CODEX, _FLAG, AgentTransport.CODEX
    )

    assert transport is AgentTransport.CODEX
    assert model_flag is None


def test_an_unrestricted_agreeing_chain_keeps_both() -> None:
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, AgentTransport.CLAUDE
    )

    assert transport is AgentTransport.CLAUDE
    assert model_flag == _FLAG


def test_an_unresolved_chain_keeps_the_original_transport() -> None:
    transport, model_flag = phase_session_identity(AgentTransport.CLAUDE, _FLAG, None)

    assert transport is AgentTransport.CLAUDE
    assert model_flag == _FLAG


def test_a_mixed_unrestricted_chain_drops_the_model_flag_too() -> None:
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, None, chain_is_ambiguous=True
    )

    assert transport is AgentTransport.CLAUDE
    assert model_flag is None


def test_ambiguity_is_computed_from_the_candidates() -> None:
    from ralph.mcp.multimodal.capabilities import (
        select_session_transport,
        session_transport_is_ambiguous,
    )

    assert session_transport_is_ambiguous(["claude", "opencode"]) is True
    assert session_transport_is_ambiguous(["agy", "claude"]) is True
    assert session_transport_is_ambiguous(["claude", "claude"]) is False
    assert session_transport_is_ambiguous(["claude"]) is False
    assert session_transport_is_ambiguous([]) is False
    assert session_transport_is_ambiguous(["claude", "codex"]) is False
    assert select_session_transport(["claude", "codex"]) == "codex"


def test_the_commit_chain_uses_the_same_ambiguity_rule() -> None:
    from ralph.pipeline.plumbing.commit_plumbing import (
        commit_chain_is_ambiguous,
        commit_chain_transport,
    )

    class _Registry:
        def __init__(self, transports: dict[str, AgentTransport]) -> None:
            self._transports = transports

        def get(self, name: str) -> object:
            transport = self._transports.get(name)
            if transport is None:
                return None
            return SimpleNamespace(transport=transport, model_flag=None)

    def chain(*names: str) -> object:
        registry = _Registry(
            {
                "claude": AgentTransport.CLAUDE,
                "opencode": AgentTransport.OPENCODE,
                "codex": AgentTransport.CODEX,
            }
        )
        return SimpleNamespace(agents=list(names), registry=registry)

    class _ModelRegistry:
        def __init__(self, mapping: dict[str, str | None]) -> None:
            self._mapping = mapping

        def get(self, name: str) -> object:
            if name not in self._mapping:
                return None
            return SimpleNamespace(transport=AgentTransport.CLAUDE, model_flag=self._mapping[name])

    def model_chain(mapping: dict[str, str | None]) -> object:
        return SimpleNamespace(agents=list(mapping), registry=_ModelRegistry(mapping))

    mixed = chain("claude", "opencode")
    assert commit_chain_transport(mixed) is None
    assert commit_chain_is_ambiguous(mixed) is True

    same = chain("claude", "claude")
    assert commit_chain_transport(same) is AgentTransport.CLAUDE
    assert commit_chain_is_ambiguous(same) is False

    restricted = chain("claude", "codex")
    assert commit_chain_transport(restricted) is AgentTransport.CODEX
    assert commit_chain_is_ambiguous(restricted) is False

    empty = chain()
    assert commit_chain_transport(empty) is None
    assert commit_chain_is_ambiguous(empty) is False

    disagreeing_models = model_chain({"gem": "--model gemini/gemini-2.5-pro", "plain": None})
    assert commit_chain_transport(disagreeing_models) is AgentTransport.CLAUDE
    assert commit_chain_is_ambiguous(disagreeing_models) is True

    agreeing_models = model_chain({"a": "--model x", "b": "--model x"})
    assert commit_chain_is_ambiguous(agreeing_models) is False


def test_a_model_disagreement_drops_the_flag_even_when_the_cli_agrees() -> None:
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, AgentTransport.CLAUDE, chain_is_ambiguous=True
    )

    assert transport is AgentTransport.CLAUDE, "the tag still selects the upstream loaders"
    assert model_flag is None, "an ambiguous chain must not resolve a provider"


def test_ambiguity_drops_the_flag_whatever_the_chain_transport_is() -> None:
    for chain_transport in (None, AgentTransport.CLAUDE, AgentTransport.CODEX):
        _transport, model_flag = phase_session_identity(
            AgentTransport.CLAUDE, _FLAG, chain_transport, chain_is_ambiguous=True
        )

        assert model_flag is None, chain_transport
