"""WebSearchBackendSpec model — backend configuration for built-in web search providers."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ralph.pydantic_compat import RalphBaseModel


class WebSearchBackendSpec(RalphBaseModel):
    """Backend configuration for built-in web search providers."""

    model_config = ConfigDict(frozen=True)

    backend: Literal["ddgs", "searxng", "tavily", "brave", "exa"]
    url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None

    @model_validator(mode="after")
    def validate_secret_shape(self) -> Self:
        if self.backend == "searxng" and not self.url:
            raise ValueError("searxng backend requires url")
        if self.backend in {"tavily", "brave", "exa"}:
            has_api_key = self.api_key is not None
            has_api_key_env = self.api_key_env is not None
            if has_api_key == has_api_key_env:
                raise ValueError("api_key xor api_key_env is required for keyed backends")
        return self


__all__ = ["WebSearchBackendSpec"]
