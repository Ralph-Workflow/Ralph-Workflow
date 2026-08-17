"""``kimi/<model>`` dynamic alias resolution (PA-003).

The kimi dynamic alias synthesizes an :class:`AgentConfig` from the
built-in kimi entry with a ``-m <model>`` model flag (the documented
Kimi Code short model flag) and ``can_commit=True``.  Model ids are
slash-delimited alias paths (e.g. ``kimi-code/k3-256k``) that MUST be
preserved verbatim, and argv-unsafe shapes fail closed (``None``) so a
malformed alias never reaches the command builder.
"""

from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry, _is_valid_kimi_model_id
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig


def _registry() -> AgentRegistry:
    return AgentRegistry.from_config(UnifiedConfig())


def test_kimi_dynamic_alias_resolves_slash_model_path() -> None:
    """``kimi/kimi-code/k3-256k`` preserves the full slash path in the flag."""
    registry = _registry()

    config = registry.get("kimi/kimi-code/k3-256k")

    assert config is not None
    assert config.transport is AgentTransport.KIMI
    # ``shlex.quote`` is a no-op for this id (no shell-special chars),
    # so the flag is the plain two-token form.
    assert config.model_flag == "-m kimi-code/k3-256k"
    assert config.can_commit is True


def test_kimi_dynamic_alias_model_flag_tokenizes_to_two_argv_tokens() -> None:
    """The flag splits into exactly ``['-m', '<model>']`` through shlex."""
    import shlex

    registry = _registry()
    config = registry.get("kimi/kimi-code/k3-256k")

    assert config is not None
    assert shlex.split(config.model_flag) == ["-m", "kimi-code/k3-256k"]


def test_kimi_dynamic_alias_accepts_plain_model_id() -> None:
    """A bare single-segment model id resolves with the same shape."""
    registry = _registry()

    config = registry.get("kimi/kimi-for-coding")

    assert config is not None
    assert config.model_flag == "-m kimi-for-coding"
    assert config.can_commit is True


def test_kimi_dynamic_alias_is_visible_in_catalog() -> None:
    """The catalog resolves the same synthesized config as the registry."""
    registry = _registry()

    support = registry.catalog.get("kimi/kimi-code/k3-256k")

    assert support is not None
    assert support.config.model_flag == "-m kimi-code/k3-256k"
    assert support.config.transport is AgentTransport.KIMI


def test_bare_kimi_resolves_to_the_builtin_entry() -> None:
    """``kimi`` alone resolves to the built-in support, not a dynamic alias."""
    registry = _registry()

    config = registry.get("kimi")

    assert config is not None
    assert config.transport is AgentTransport.KIMI
    # The built-in carries no model flag; only ``kimi/<model>`` sets one.
    assert config.model_flag is None


@pytest.mark.parametrize(
    "model_id",
    [
        "",
        " ",
        "foo:bar:baz",
        "kimi//x",
        "kimi/",
        "-flag",
        "model with spaces",
    ],
)
def test_kimi_model_id_validator_rejects_argv_unsafe_shapes(model_id: str) -> None:
    """Empty / whitespace / colon / leading-dash / empty-segment shapes fail."""
    assert _is_valid_kimi_model_id(model_id) is False


@pytest.mark.parametrize(
    "model_id",
    [
        "kimi-for-coding",
        "kimi-code/k3-256k",
        "kimi-code/k3-256k-preview",
        "vendor/family/name",
    ],
)
def test_kimi_model_id_validator_accepts_documented_shapes(model_id: str) -> None:
    """Plain ids and non-empty slash paths are accepted verbatim."""
    assert _is_valid_kimi_model_id(model_id) is True


@pytest.mark.parametrize(
    "alias",
    [
        "kimi/",
        "kimi//x",
        "kimi/-flag",
        "kimi/foo:bar:baz",
    ],
)
def test_kimi_dynamic_alias_fails_closed_on_unsafe_shapes(alias: str) -> None:
    """An argv-unsafe alias resolves to ``None`` instead of a broken config."""
    registry = _registry()

    assert registry.get(alias) is None
