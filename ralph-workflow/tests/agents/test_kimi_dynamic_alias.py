from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry, _is_valid_kimi_model_id
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig


def _registry() -> AgentRegistry:
    return AgentRegistry.from_config(UnifiedConfig())


def test_kimi_dynamic_alias_resolves_slash_model_path() -> None:
    registry = _registry()

    config = registry.get("kimi/kimi-code/k3-256k")

    assert config is not None
    assert config.transport is AgentTransport.KIMI
    assert config.model_flag == "-m kimi-code/k3-256k"


def test_kimi_dynamic_alias_model_flag_tokenizes_to_two_argv_tokens() -> None:
    import shlex

    registry = _registry()
    config = registry.get("kimi/kimi-code/k3-256k")

    assert config is not None
    assert shlex.split(config.model_flag) == ["-m", "kimi-code/k3-256k"]


def test_kimi_dynamic_alias_accepts_plain_model_id() -> None:
    registry = _registry()

    config = registry.get("kimi/kimi-for-coding")

    assert config is not None
    assert config.model_flag == "-m kimi-for-coding"


def test_kimi_dynamic_alias_is_visible_in_catalog() -> None:
    registry = _registry()

    support = registry.catalog.get("kimi/kimi-code/k3-256k")

    assert support is not None
    assert support.config.model_flag == "-m kimi-code/k3-256k"
    assert support.config.transport is AgentTransport.KIMI


def test_bare_kimi_resolves_to_the_builtin_entry() -> None:
    registry = _registry()

    config = registry.get("kimi")

    assert config is not None
    assert config.transport is AgentTransport.KIMI
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
    registry = _registry()

    assert registry.get(alias) is None
