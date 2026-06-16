"""Web search configuration models for `mcp.toml`."""

from __future__ import annotations

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ralph.config._web_search_backend_spec import WebSearchBackendSpec
from ralph.pydantic_compat import RalphBaseModel
from ralph.timeout_defaults import WEBSEARCH_BACKEND_TIMEOUT_SECONDS


class WebSearchConfig(RalphBaseModel):
    """Top-level `web_search` config in `mcp.toml`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    backend: str = "ddgs"
    fallback: list[str] = Field(default_factory=list)
    backends: dict[str, WebSearchBackendSpec] = Field(default_factory=dict)
    web_search_default_timeout_seconds: float = Field(
        default=WEBSEARCH_BACKEND_TIMEOUT_SECONDS,
        gt=0.0,
        description=(
            "Default per-call timeout in seconds applied to every websearch backend that "
            "does not specify its own timeout_seconds. Sourced from "
            "ralph.timeout_defaults.WEBSEARCH_BACKEND_TIMEOUT_SECONDS. Increase this if a "
            "websearch backend is slow and the 10s default triggers false -32001 Request "
            "timed out errors. Must be <= any per-backend timeout_seconds that is set, "
            "otherwise the per-backend override would be silently smaller than the default."
        ),
    )

    @model_validator(mode="after")
    def _validate_backend_timeouts(self) -> Self:
        default_timeout = self.web_search_default_timeout_seconds
        for backend_name, spec in self.backends.items():
            if spec.timeout_seconds is not None and spec.timeout_seconds < default_timeout:
                raise ValueError(
                    f"backends.{backend_name}.timeout_seconds "
                    f"({spec.timeout_seconds}) must be >= "
                    f"web_search_default_timeout_seconds ({default_timeout})"
                )
        return self


__all__ = ["WebSearchConfig"]
