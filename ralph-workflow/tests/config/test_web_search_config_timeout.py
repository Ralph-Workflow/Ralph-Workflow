"""Tests for the timeout-centralization contract in the websearch config layer.

Asserts the new ``web_search_default_timeout_seconds`` and per-backend
``timeout_seconds`` fields:

- the field default matches the central ``ralph.timeout_defaults`` constant;
- the per-backend default is ``None`` (inherit-from-default semantics);
- positive ``timeout_seconds`` are accepted, zero/negative rejected by
  the pydantic ``gt=0.0`` invariant;
- a per-backend override smaller than the default is rejected by the
  ``WebSearchConfig`` model_validator (mirrors the import-time invariant
  in ``ralph.timeout_defaults``);
- the configured timeout flows through ``_build_backend`` into every
  backend's ``timeout_seconds`` attribute.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ralph.config.mcp_models import McpConfig

if TYPE_CHECKING:
    from types import ModuleType


def _import_config_module() -> ModuleType:
    return import_module("ralph.config.web_search_config")


def _import_spec_module() -> ModuleType:
    return import_module("ralph.config._web_search_backend_spec")


def _import_tools_module() -> ModuleType:
    return import_module("ralph.mcp.tools.websearch")


def test_default_timeout_matches_timeout_defaults() -> None:
    config_module = _import_config_module()
    constants = import_module("ralph.timeout_defaults")
    c = McpConfig()
    assert (
        c.web_search.web_search_default_timeout_seconds
        == constants.WEBSEARCH_BACKEND_TIMEOUT_SECONDS
    )
    assert c.web_search.web_search_default_timeout_seconds == 10.0
    assert config_module.WebSearchConfig.model_fields[
        "web_search_default_timeout_seconds"
    ].default == constants.WEBSEARCH_BACKEND_TIMEOUT_SECONDS


def test_backend_spec_timeout_inherits_default() -> None:
    spec_module = _import_spec_module()
    spec = spec_module.WebSearchBackendSpec(backend="brave", api_key="k")
    assert spec.timeout_seconds is None


def test_backend_spec_with_timeout_seconds_accepted() -> None:
    spec_module = _import_spec_module()
    spec = spec_module.WebSearchBackendSpec(
        backend="brave", api_key="k", timeout_seconds=20.0
    )
    assert spec.timeout_seconds == 20.0


@pytest.mark.parametrize("bad", [0.0, -1.0, -0.5])
def test_backend_spec_rejects_zero_or_negative(bad: float) -> None:
    spec_module = _import_spec_module()
    with pytest.raises(ValidationError):
        spec_module.WebSearchBackendSpec(backend="brave", api_key="k", timeout_seconds=bad)


def test_config_rejects_backend_timeout_smaller_than_default() -> None:
    config_module = _import_config_module()
    with pytest.raises(ValidationError) as exc_info:
        config_module.WebSearchConfig.model_validate(
            {
                "web_search_default_timeout_seconds": 50.0,
                "backends": {
                    "exa": {
                        "backend": "exa",
                        "api_key": "k",
                        "timeout_seconds": 5.0,
                    }
                },
            }
        )
    assert "must be >=" in str(exc_info.value)


def test_config_accepts_backend_timeout_equal_or_larger_than_default() -> None:
    config_module = _import_config_module()
    config = config_module.WebSearchConfig.model_validate(
        {
            "web_search_default_timeout_seconds": 10.0,
            "backends": {
                "exa": {
                    "backend": "exa",
                    "api_key": "k",
                    "timeout_seconds": 10.0,
                }
            },
        }
    )
    assert config.backends["exa"].timeout_seconds == 10.0
    config2 = config_module.WebSearchConfig.model_validate(
        {
            "web_search_default_timeout_seconds": 10.0,
            "backends": {
                "exa": {
                    "backend": "exa",
                    "api_key": "k",
                    "timeout_seconds": 30.0,
                }
            },
        }
    )
    assert config2.backends["exa"].timeout_seconds == 30.0


@pytest.mark.parametrize(
    "backend_name,api_key,expected_class_name",
    [
        ("ddgs", None, "DdgsBackend"),
        ("searxng", None, "SearxngBackend"),
        ("tavily", "tvly", "TavilyBackend"),
        ("brave", "brv", "BraveBackend"),
        ("exa", "exa-key", "ExaBackend"),
    ],
)
def test_build_backend_propagates_default_timeout(
    backend_name: str, api_key: str | None, expected_class_name: str
) -> None:
    tools_module = _import_tools_module()
    config_module = _import_config_module()
    backends_module = import_module("ralph.mcp.websearch.backends")
    constants = import_module("ralph.timeout_defaults")
    if backend_name == "searxng":
        config = config_module.WebSearchConfig.model_validate(
            {"backends": {"searxng": {"backend": "searxng", "url": "http://x"}}}
        )
    elif api_key is not None:
        config = config_module.WebSearchConfig.model_validate(
            {"backends": {backend_name: {"backend": backend_name, "api_key": api_key}}}
        )
    else:
        config = config_module.WebSearchConfig()
    backend = tools_module._build_backend(backend_name, config)
    expected_class = getattr(backends_module, expected_class_name)
    assert isinstance(backend, expected_class)
    assert backend.timeout_seconds == constants.WEBSEARCH_BACKEND_TIMEOUT_SECONDS


def test_build_backend_propagates_per_backend_override() -> None:
    tools_module = _import_tools_module()
    config_module = _import_config_module()
    config = config_module.WebSearchConfig.model_validate(
        {
            "web_search_default_timeout_seconds": 10.0,
            "backends": {
                "brave": {"backend": "brave", "api_key": "k", "timeout_seconds": 25.0}
            },
        }
    )
    backend = tools_module._build_backend("brave", config)
    assert backend.timeout_seconds == 25.0
