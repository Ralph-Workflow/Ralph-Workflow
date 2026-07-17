"""Unit contracts for the dev-agent conflict resolver builder.

All-mock: no test here starts git or a real agent subprocess. The
builder's contract is (a) the resolver invokes the FIRST agent of the
``development`` drain chain with a focused conflict-resolution prompt,
(b) every failure mode (missing agent, invocation error) is contained
as ``False`` so :mod:`ralph.pipeline.auto_integrate_resolve` aborts
the merge instead of crashing the run, and (c) the operator sees WARN
lines around the resolution attempt.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import auto_integrate_agent as resolver_module
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _build_resolver(
    *,
    agent_config: object,
) -> tuple[resolver_module.ConflictResolver, MagicMock, MagicMock]:
    registry = MagicMock()
    registry.get.return_value = agent_config
    display = MagicMock()
    resolver = resolver_module.build_agent_conflict_resolver(
        policy_bundle=_load_default_policy_bundle(),
        registry=registry,
        display=display,
    )
    return resolver, registry, display


def test_resolver_invokes_dev_agent_with_focused_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dev-chain agent is invoked with a resolve-only prompt."""
    calls: dict[str, object] = {}

    def _fake_invoke(
        agent_config: object, prompt_file: str, *, options: object = None
    ) -> object:
        prompt_path = Path(prompt_file)
        calls["agent_config"] = agent_config
        calls["prompt_path"] = prompt_path
        calls["prompt_text"] = prompt_path.read_text(encoding="utf-8")
        calls["options"] = options
        return iter(["line"])

    monkeypatch.setattr(resolver_module, "invoke_agent", _fake_invoke)
    sentinel_agent = object()
    resolver, registry, display = _build_resolver(agent_config=sentinel_agent)

    assert resolver(tmp_path, "main") is True

    # First agent of the development drain chain in the default policy.
    registry.get.assert_called_once_with("claude")
    assert calls["agent_config"] is sentinel_agent
    prompt_text = str(calls["prompt_text"])
    assert "main" in prompt_text
    assert "do not commit" in prompt_text.lower()
    # The invocation runs inside the conflicted repository.
    options = calls["options"]
    assert getattr(options, "workspace_path", None) == tmp_path
    # The transient prompt file is cleaned up after the invocation.
    prompt_path = calls["prompt_path"]
    assert isinstance(prompt_path, Path)
    assert not prompt_path.exists()
    # The operator saw the resolution attempt.
    display.emit_warn_line.assert_called()


def test_resolver_returns_false_when_invocation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An agent invocation error is contained as resolution failure."""

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(resolver_module, "invoke_agent", _boom)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    assert resolver(tmp_path, "main") is False


def test_resolver_returns_false_when_agent_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registry with no dev agent yields a failing (but safe) resolver."""
    invoke = MagicMock()
    monkeypatch.setattr(resolver_module, "invoke_agent", invoke)
    resolver, _registry, _display = _build_resolver(agent_config=None)

    assert resolver(tmp_path, "main") is False
    invoke.assert_not_called()
